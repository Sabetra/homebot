"""LLM-gesttzte Batch-Kategorisierung uncategorisierter Buchungen.

Workflow:
1. ``suggest(...)`` ldt eine Batch unkategorisierter Transaktionen, baut
   einen Prompt mit den existierenden Kategorien (damit das LLM bevorzugt
   wiederverwendet) und ruft ``LLMStructuredWrapper.generate_structured``
   mit ``CategorySuggestions`` als Output-Schema auf -- token-weise GBNF-
   erzwungenes JSON, kein freies Parsen.
2. ``apply(...)`` schreibt die akzeptierten Vorschlge in die DB
   (``transaction_category`` mit ``source='llm'``) und legt -- falls vom
   LLM markiert oder vom User gewnscht -- eine ``counterparty_rules``-
   Regel an, sodass zuknftige + bisherige Treffer derselben Counterparty
   automatisch kategorisiert werden. Das ist der Sustainability-Kern.

Bewusst KEINE Keyword/Regex-Heuristik: die LLM-Inferenz ist semantisch,
die Persistenz exakt-deterministisch (Counterparty-IBAN bzw. normalisierter
Counterparty-String).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from finance.db_schema import (
    FinanceDB,
    Transaction,
    _from_cents,
)
from finance.models import CategorySuggestion, CategorySuggestions
from finance.token_budget import resolve_context_tokens

logger = logging.getLogger(__name__)

_CAT_ADAPTIVE_MAX_DEPTH = 4

# Token-Budget-Modell (analog finance.extractor):
#   - n_ctx          = LLM-Kontextlimit (Loader-gecacht, Fallback Env)
#   - prompt_tokens  ~= prompt_chars / _CAT_CHARS_PER_TOKEN
#   - safety_margin  = System-Prompt + Schema-Hints + Padding
#   - max_tokens_out = n_ctx - prompt_tokens - safety_margin
# Pro Suggestion ca. 50 Output-Tokens (id, kategorie, confidence,
# create_rule, optional reason). 25 Tx -> ~1300 Tokens. Floor/Ceil
# schuetzen vor Degenerate-Faellen.
_CAT_CHARS_PER_TOKEN = 3.8
_CAT_SAFETY_MARGIN_TOKENS = 800
_CAT_MAX_TOKENS_FLOOR = 512
_CAT_MAX_TOKENS_CEIL = 8192
_CAT_TOKENS_PER_SUGGESTION = 60  # konservativ inkl. JSON-Overhead
_CAT_BATCH_FLOOR = 5
_CAT_BATCH_CEIL = 200


def _adaptive_cat_max_tokens(prompt_chars: int, n_ctx: int) -> int:
    """Output-Budget strukturell aus n_ctx -- verhindert JSON-Truncation."""
    prompt_tokens_estimate = int(prompt_chars / _CAT_CHARS_PER_TOKEN)
    available = n_ctx - prompt_tokens_estimate - _CAT_SAFETY_MARGIN_TOKENS
    return max(_CAT_MAX_TOKENS_FLOOR, min(_CAT_MAX_TOKENS_CEIL, available))


def recommended_batch_size(n_ctx: int, *, avg_chars_per_tx_line: int = 280) -> int:
    """Optimale Batch-Groesse aus n_ctx ableiten.

    Modell: Halbiere n_ctx grob in Prompt-Teil und Output-Teil (Output ist
    durch Suggestion-Tokens dominiert). Begrenzung: pro Tx faellt
    avg_chars_per_tx_line Markdown im Prompt + _CAT_TOKENS_PER_SUGGESTION
    Tokens im Output an. Wir suchen das maximale N, fuer das
        N * (chars_per_tx/CHARS_PER_TOKEN + tokens_per_sug) + safety
        + system_prompt_tokens(~600) + categories_block(~400)
        <= n_ctx
    """
    fixed_overhead_tokens = _CAT_SAFETY_MARGIN_TOKENS + 600 + 400
    per_tx_tokens = (avg_chars_per_tx_line / _CAT_CHARS_PER_TOKEN) + _CAT_TOKENS_PER_SUGGESTION
    raw = (n_ctx - fixed_overhead_tokens) / per_tx_tokens
    return max(_CAT_BATCH_FLOOR, min(_CAT_BATCH_CEIL, int(raw)))


_CATEGORIZER_SYSTEM_PROMPT = (
    "Du bist ein Experte für persönliche Finanzbuchhaltung. Deine Aufgabe ist, "
    "Buchungen aus einem Bankkonto strukturiert zu kategorisieren.\n\n"
    "Regeln:\n"
    "- Verwende bevorzugt die im Prompt aufgelisteten existierenden Kategorien. "
    "Erfinde NUR dann eine neue Kategorie, wenn keine passt.\n"
    "- Kategorien sind kurze, konsistente, deutsche Substantive (z.B. 'Lebensmittel', "
    "'Miete', 'Mobilität', 'Restaurants', 'Versicherungen', 'Gehalt', 'Zinsen').\n"
    "- Confidence ehrlich einschätzen: 0.95+ nur bei eindeutigen, etablierten "
    "Counterparties (z.B. ein bekannter Lebensmitteldiscounter).\n"
    "- ``create_rule=true`` NUR setzen, wenn die Counterparty bzw. ihre IBAN "
    "stabil und merchant-spezifisch ist und die Kategorie deshalb dauerhaft passt. "
    "Bei sporadischen Empfängern (Privatpersonen, einmalige Überweisungen) false.\n"
    "- ``transaction_id`` muss exakt der ID aus dem Input entsprechen.\n"
    "- Erfinde keine Buchungen; gib genau eine Suggestion pro Input-Buchung zurück."
)


@dataclass(frozen=True)
class CategorizationOutcome:
    """Ergebnis von ``apply``."""

    assigned: int           # tatsächlich kategorisierte Buchungen
    rules_created: int      # frisch persistierte Counterparty-Regeln
    rules_applied_extra: int  # rueckwirkend durch Regeln getroffene Buchungen


class FinanceCategorizer:
    """LLM-Categorizer mit GBNF-erzwungenem Output."""

    def __init__(
        self,
        llm_client: Any,
        *,
        db: Optional[FinanceDB] = None,
        max_retries: int = 2,
    ) -> None:
        if llm_client is None:
            raise ValueError("llm_client is required for FinanceCategorizer")
        from llm_structured_wrapper import LLMStructuredWrapper, StructuredOutputError

        self.db = db or FinanceDB.get_instance()
        self._structured_output_error = StructuredOutputError
        self._wrapper = LLMStructuredWrapper(
            llm_client=llm_client,
            max_retries=max_retries,
            temperature=0.0,
            enable_logging=False,
        )

        # Einheitliche n_ctx-Aufloesung ueber finance.token_budget.
        self._n_ctx = resolve_context_tokens(llm_client)

    # -- public API --------------------------------------------------

    def suggest(
        self,
        *,
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 25,
    ) -> List[CategorySuggestion]:
        """Holt unkategorisierte Buchungen und liefert LLM-Vorschlge."""
        txs = self.db.list_uncategorized(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        if not txs:
            return []
        return self._suggest_with_adaptive_batching(list(txs), depth=0)

    def apply(
        self,
        suggestions: Sequence[CategorySuggestion],
        *,
        create_rules: bool = True,
    ) -> CategorizationOutcome:
        """Persistiert Vorschläge: Kategorie-Assignment + optional Regel.

        ``create_rules=True`` (default): wenn Suggestion ``create_rule=True``
        markiert ist, wird zusätzlich eine ``counterparty_rules``-Regel
        angelegt -- mit IBAN-Match falls vorhanden, sonst exakt-normalisiertem
        Counterparty-Match. Die neue Regel wird sofort rückwirkend auf alle
        passenden noch unkategorisierten Buchungen angewendet.
        """
        assigned = 0
        rules_created = 0
        rules_applied_extra = 0
        existing_kind_by_name = {
            c.name.strip().casefold(): c.kind
            for c in self.db.list_categories()
            if c.name and c.kind
        }
        for sug in suggestions:
            tx = self._fetch_tx(sug.transaction_id)
            if tx is None:
                continue
            key = (sug.category or "").strip().casefold()
            # Preserve the semantic kind of existing categories so a transfer
            # bucket is not silently downgraded to expense/income by amount sign.
            cat_kind = existing_kind_by_name.get(key) or self._infer_kind(
                tx.amount_cents,
                transaction_nature=tx.transaction_nature,
            )
            cat_id = self.db.upsert_category(name=sug.category, kind=cat_kind)
            existing_kind_by_name[key] = cat_kind
            self.db.assign_category(
                transaction_id=tx.id,
                category_id=cat_id,
                confidence=sug.confidence,
                source="llm",
            )
            assigned += 1
            if create_rules and sug.create_rule:
                if tx.counterparty_iban:
                    self.db.upsert_rule(category_id=cat_id, match_iban=tx.counterparty_iban)
                elif tx.counterparty:
                    self.db.upsert_rule(category_id=cat_id, match_counterparty=tx.counterparty)
                else:
                    continue
                rules_created += 1
                # Rueckwirkend Regeln auf bestehende uncategorisierte Buchungen anwenden
                rules_applied_extra += self.db.apply_rules(only_uncategorized=True)
        return CategorizationOutcome(
            assigned=assigned,
            rules_created=rules_created,
            rules_applied_extra=rules_applied_extra,
        )

    # -- helpers -----------------------------------------------------

    def _fetch_tx(self, tx_id: int) -> Optional[Transaction]:
        return self.db.get_transaction(tx_id)

    @staticmethod
    def _infer_kind(
        amount_cents: int,
        *,
        transaction_nature: Optional[str] = None,
    ) -> str:
        if transaction_nature == "internal_transfer":
            return "transfer"
        return "income" if amount_cents > 0 else "expense"

    def _build_prompt(self, txs: Sequence[Transaction]) -> str:
        existing = [c.name for c in self.db.list_categories()]
        existing_block = (
            "Existierende Kategorien (BEVORZUGT verwenden):\n"
            + ("- " + "\n- ".join(existing) if existing else "(noch keine)")
        )
        tx_lines: List[str] = []
        for t in txs:
            sign = "+" if t.amount_cents > 0 else ""
            tx_lines.append(
                f"id={t.id} | {t.booking_date} | {sign}{_from_cents(t.amount_cents):.2f} {t.currency} | "
                f"counterparty={t.counterparty or '-'} | "
                f"counterparty_iban={t.counterparty_iban or '-'} | "
                f"purpose={(t.purpose or '-')[:160]} | "
                f"booking_type={t.booking_type or '-'}"
            )
        return (
            f"{existing_block}\n\n"
            "Zu kategorisierende Buchungen:\n"
            + "\n".join(tx_lines)
            + "\n\nGib pro Buchung GENAU eine Suggestion zurück."
        )

    def _suggest_with_adaptive_batching(
        self,
        txs: List[Transaction],
        *,
        depth: int,
    ) -> List[CategorySuggestion]:
        """Erzeugt robuste LLM-Suggestions via rekursivem Batch-Splitting.

        Root-Cause-orientiert: Wenn ein grosser Batch das strukturierte
        Output-Budget überläuft (typisch: JSON bricht mitten in der Liste ab),
        wird der Batch deterministisch halbiert und beide Hälften separat
        extrahiert. Zusätzlich wird auf Vollständigkeit geprüft: fehlen IDs,
        werden nur diese fehlenden IDs erneut (kleiner) inferiert.
        """
        if not txs:
            return []

        valid_ids = {t.id for t in txs}
        prompt = self._build_prompt(txs)
        try:
            result: CategorySuggestions = self._wrapper.generate_structured(
                prompt=prompt,
                output_schema=CategorySuggestions,
                max_tokens=_adaptive_cat_max_tokens(len(prompt), self._n_ctx),
                system_prompt=_CATEGORIZER_SYSTEM_PROMPT,
            )
        except self._structured_output_error:
            if depth >= _CAT_ADAPTIVE_MAX_DEPTH or len(txs) <= 1:
                raise
            mid = len(txs) // 2
            left = self._suggest_with_adaptive_batching(txs[:mid], depth=depth + 1)
            right = self._suggest_with_adaptive_batching(txs[mid:], depth=depth + 1)
            merged: Dict[int, CategorySuggestion] = {}
            for s in left + right:
                if s.transaction_id in valid_ids and s.transaction_id not in merged:
                    merged[s.transaction_id] = s
            return [merged[t.id] for t in txs if t.id in merged]

        by_id: Dict[int, CategorySuggestion] = {}
        for suggestion in result.suggestions:
            tx_id = suggestion.transaction_id
            if tx_id in valid_ids and tx_id not in by_id:
                by_id[tx_id] = suggestion

        missing = [t for t in txs if t.id not in by_id]
        if missing and depth < _CAT_ADAPTIVE_MAX_DEPTH:
            logger.warning(
                "Categorizer returned %d/%d suggestions; retrying %d missing ids in smaller batch",
                len(by_id),
                len(txs),
                len(missing),
            )
            recovered = self._suggest_with_adaptive_batching(missing, depth=depth + 1)
            for suggestion in recovered:
                if suggestion.transaction_id in valid_ids and suggestion.transaction_id not in by_id:
                    by_id[suggestion.transaction_id] = suggestion

        return [by_id[t.id] for t in txs if t.id in by_id]


__all__ = ["FinanceCategorizer", "CategorizationOutcome", "recommended_batch_size"]
