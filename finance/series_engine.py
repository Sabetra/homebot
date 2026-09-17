"""Forecast-UX AP2: gemeinsame Serien-Engine (rein deterministisch).

Kanonicaler Baustein laut Prompt §5.2 ("Gemeinsame Berechnung"): eine
Folge von Planvorkommen speist Kalender, Rechnungsfenster, Audit,
Tages-/Monatsaggregation und Charts. Diese Engine ist bewusst:

* REIN (kein DB-, Streamlit- oder Modell-Zugriff) — deterministisch
  testbar ohne Infrastruktur;
* VORZEICHEN-BEWUSST (Einnahmen/Erstattungen getrennt von Ausgaben, F04);
* KONTEN-/WAHRUNGSBINDEND (Scope = iban + currency + direction +
  counterparty, F03);
* AUSNAHME-STABIL: Skip/Move/Amount gelten pro Serien-ID + ORIGINAL-Termin;
  erneute Expansion erzeugt weder Dubletten noch Verlust der Ausnahmen
  (Prompt §5.1, T11).

Nur stdlib — keine neuen Dependencies.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# Rhythmen (Prompt §5.1: einmalig, woechentlich, alle N Wochen, monatlich,
# alle N Monate, jaehrlich — "Zweimonatlich" ist NICHT zweimal monatlich).
VALID_CADENCES = ("monthly", "n_months", "weekly", "n_weeks", "yearly")

# Ausnahmetypen (Prompt §5.1: "verschoben, Betrag ersetzt oder ausgelassen").
VALID_EXCEPTION_TYPES = ("skip", "move", "amount")

# Kandidaten-Status (Prompt §5.1: "Abgelehnte Erkennungen stabil speichern").
VALID_CANDIDATE_STATUSES = ("pending", "confirmed", "rejected")

# Sicherheits-Cap: Expansion darf nicht weglaufen (pathologische Fenster).
_MAX_OCCURRENCES = 2000


def clamp_day(year: int, month: int, anchor_day: int) -> int:
    """Anker-Tag auf die Tagezahl des Kalendermonats clampen.

    Kalendermonat ist NICHT 30 Tage (Prompt §5.1): Anker 29..31 werden
    das Ende des jeweiligen Monats (2026-06-29 => 2026-06-30, Anker 31
    im Februar => 28/29). Anker 1..28 sind immer stabil.
    """
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise ValueError(f"year must be 1..9999, got {year!r}")
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError(f"month must be 1..12, got {month!r}")
    if isinstance(anchor_day, bool) or not isinstance(anchor_day, int) or not 1 <= anchor_day <= 31:
        raise ValueError(f"anchor_day must be 1..31, got {anchor_day!r}")
    last_day = calendar.monthrange(year, month)[1]
    if anchor_day >= 29:
        return last_day
    return anchor_day


@dataclass(frozen=True)
class SeriesSpec:
    """Fachliche Serien-Definition (persistenz-agnostisch).

    * ``cadence``: 'monthly' | 'n_months' | 'weekly' | 'n_weeks' | 'yearly'
    * ``period_n``: Schrittweite (1 = einfache Periode; n_months 2..11;
      n_weeks 2..52). Zweimonatlich ist NICHT zweimal monatlich.
    * ``anchor_day``: Anker-TAG im Kalendermonat (1..31) fuer
      monthly/n_months/yearly; wird bei Expansion auf den Monat clamped.
    * ``anchor_date``: ISO-Datum des Erstankers; Startpunkt der Expansion
      und Referenz fuer weekly/n_weeks.
    * ``effective_from``/``effective_to``: Geltungsbereich (ISO);
      ``effective_to=None`` => offen (keine kuendigungsbedingte Ende).
    * ``amount_cents`` > 0; Vorzeichen kommt aus ``direction``.
    """

    key: str
    direction: str  # 'income' | 'expense'
    cadence: str  # VALID_CADENCES
    period_n: int  # >= 1
    anchor_day: int  # 1..31
    anchor_date: str  # ISO YYYY-MM-DD (Erstanker)
    amount_cents: int  # > 0
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    counterparty: str = ""
    currency: str = ""
    iban: str = ""

    def __post_init__(self) -> None:
        if self.direction not in ("income", "expense"):
            raise ValueError(f"direction must be income|expense, got {self.direction!r}")
        if self.cadence not in VALID_CADENCES:
            raise ValueError(f"cadence must be one of {VALID_CADENCES}, got {self.cadence!r}")
        if isinstance(self.period_n, bool) or not isinstance(self.period_n, int) or self.period_n < 1:
            raise ValueError(f"period_n must be int >= 1, got {self.period_n!r}")
        if self.cadence == "n_months" and not 2 <= self.period_n <= 11:
            raise ValueError(f"n_months requires period_n 2..11, got {self.period_n}")
        if self.cadence == "n_weeks" and not 2 <= self.period_n <= 52:
            raise ValueError(f"n_weeks requires period_n 2..52, got {self.period_n}")
        if self.cadence in ("monthly", "weekly", "yearly") and self.period_n != 1:
            raise ValueError(f"{self.cadence} requires period_n == 1, got {self.period_n}")
        if isinstance(self.anchor_day, bool) or not isinstance(self.anchor_day, int) or not 1 <= self.anchor_day <= 31:
            raise ValueError(f"anchor_day must be 1..31, got {self.anchor_day!r}")
        if isinstance(self.amount_cents, bool) or not isinstance(self.amount_cents, int) or self.amount_cents <= 0:
            raise ValueError(f"amount_cents must be int > 0, got {self.amount_cents!r}")
        date.fromisoformat(self.anchor_date)
        if self.effective_from is not None:
            date.fromisoformat(self.effective_from)
        if self.effective_to is not None:
            date.fromisoformat(self.effective_to)
            if self.effective_from is not None and self.effective_to < self.effective_from:
                raise ValueError("effective_to must not precede effective_from")

    def weekly_step_days(self) -> Optional[int]:
        """Schrittweite in Tagen, falls wochenbasiert, sonst None."""
        if self.cadence in ("weekly", "n_weeks"):
            return 7 * self.period_n
        return None

    def months_per_step(self) -> Optional[int]:
        """Monat-Schrittweite (1, 2..11 oder 12), falls monats-/jahresbasiert."""
        if self.cadence == "monthly":
            return 1
        if self.cadence == "n_months":
            return self.period_n
        if self.cadence == "yearly":
            return 12
        return None


@dataclass(frozen=True)
class SeriesException:
    """Ausnahme pro Serien-Key + ORIGINAL-Termin (Prompt §5.1).

    * ``skip``:   Vorkommen wird ausgelassen (kein Ersatztermin).
    * ``move``:   Vorkommen wird verschoben; ``new_due_date`` gesetzt.
      Die Identitaet (serieninterner Slot) bleibt erhalten — erneute
      Expansion erzeugt weder Dublette noch Verlust der Ausnahme.
    * ``amount``: Betrag wird ersetzt; ``amount_cents`` > 0 gesetzt.
    """

    key: str  # Serien-Key (entspricht SeriesSpec.key)
    original_due_date: str  # ISO — ORIGINAL-Termin der Basisexpansion
    exception_type: str  # VALID_EXCEPTION_TYPES
    new_due_date: Optional[str] = None  # fuer 'move'
    amount_cents: Optional[int] = None  # fuer 'amount'

    def __post_init__(self) -> None:
        if self.exception_type not in VALID_EXCEPTION_TYPES:
            raise ValueError(
                f"exception_type must be one of {VALID_EXCEPTION_TYPES}, got {self.exception_type!r}"
            )
        date.fromisoformat(self.original_due_date)
        if self.exception_type == "move":
            if not self.new_due_date:
                raise ValueError("move exception requires new_due_date")
            date.fromisoformat(self.new_due_date)
        if self.exception_type == "amount":
            if (
                isinstance(self.amount_cents, bool)
                or not isinstance(self.amount_cents, int)
                or self.amount_cents <= 0
            ):
                raise ValueError(f"amount exception requires amount_cents > 0, got {self.amount_cents!r}")
        if self.exception_type == "skip" and (self.new_due_date is not None or self.amount_cents is not None):
            raise ValueError("skip exception must not carry new_due_date/amount_cents")


@dataclass(frozen=True)
class Occurrence:
    """Ein Planvorkommen der kanonischen Folge (Prompt §5.2).

    ``origin`` = 'series' | 'manual' | 'plan_item' | 'goal' | 'estimate';
    ``source_id`` bindet stabil an die Quelle (kein Zeilenindex/Anzeigetext).
    ``original_date`` bleibt auch bei Move erhalten (Identitaet +
    Ausnahme-Bindung); ``date`` ist der wirksame Termin.
    """

    key: str
    date: str  # ISO, wirksam
    original_date: str  # ISO, Basisexpansion
    amount_cents: int  # > 0 (Vorzeichen via direction)
    direction: str  # 'income' | 'expense'
    counterparty: str
    currency: str = ""
    iban: str = ""
    origin: str = "series"
    source_id: Optional[str] = None
    exception: Optional[str] = None  # None | 'skip' | 'move' | 'amount'


def _month_index(y: int, m: int) -> int:
    return y * 12 + (m - 1)


def _expand_base(spec: SeriesSpec, start: date, end: date) -> List[date]:
    """Basisvorkommen (ohne Ausnahmen) in [start, end], sortiert."""
    anchor = date.fromisoformat(spec.anchor_date)
    out: List[date] = []
    step_days = spec.weekly_step_days()
    if step_days is not None:
        # Wochenbasiert: exakte Tages-Schritte vom Erstanker.
        if anchor >= end:
            return out
        if anchor < start:
            days = (start - anchor).days
            k = (days + step_days - 1) // step_days
        else:
            k = 0
        d = anchor + timedelta(days=k * step_days)
        while d <= end and len(out) < _MAX_OCCURRENCES:
            if d >= start:
                out.append(d)
            d += timedelta(days=step_days)
        return out
    step_months = spec.months_per_step()
    assert step_months is not None  # cadence-Validierung oben
    a_idx = _month_index(anchor.year, anchor.month)
    s_idx = _month_index(start.year, start.month)
    e_idx = _month_index(end.year, end.month)
    m = a_idx
    while m <= e_idx and len(out) < _MAX_OCCURRENCES:
        if (m - a_idx) % step_months == 0:
            y, mo = divmod(m, 12)
            mo += 1
            d = date(y, mo, clamp_day(y, mo, spec.anchor_day))
            if start <= d <= end:
                out.append(d)
        m += step_months
    return sorted(out)


def expand_series(
    spec: SeriesSpec,
    exceptions: Optional[Mapping[str, SeriesException]] = None,
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
) -> List[Occurrence]:
    """Kanonicaler Planvorkommen-Ausdruck einer Serie (Prompt §5.2).

    Deterministisch: gleiche Eingabe => gleiche Folge (Datum, Betrag,
    Ausnahme-Marker). Ausnahmen werden pro ORIGINAL-Termin angewendet:
    ``skip`` entfernt den Slot, ``move`` ersetzt das Datum, ``amount``
    den Betrag. ``effective_from``/``effective_to`` begrenzen die Folge;
    ``window_start``/``window_end`` (ISO) beschneiden das Ergebnis.
    """
    anchor = date.fromisoformat(spec.anchor_date)
    lo = anchor
    if spec.effective_from is not None:
        lo = max(lo, date.fromisoformat(spec.effective_from))
    hi: Optional[date] = None
    if spec.effective_to is not None:
        hi = date.fromisoformat(spec.effective_to)
    if window_start is not None:
        lo = max(lo, date.fromisoformat(window_start))
    if window_end is not None:
        w = date.fromisoformat(window_end)
        hi = w if hi is None else min(hi, w)
    if hi is None:
        hi = lo  # ohne Fenster/Ende: nur den Anker selbst liefern
    if lo > hi:
        return []
    exc_map = exceptions or {}
    out: List[Occurrence] = []
    for d in _expand_base(spec, lo, hi):
        orig_iso = d.isoformat()
        exc = exc_map.get(orig_iso)
        if exc is not None and exc.exception_type == "skip":
            continue
        eff_date = d
        amount = spec.amount_cents
        marker: Optional[str] = None
        if exc is not None:
            marker = exc.exception_type
            if exc.exception_type == "move":
                assert exc.new_due_date is not None
                eff_date = date.fromisoformat(exc.new_due_date)
            elif exc.exception_type == "amount":
                assert exc.amount_cents is not None
                amount = exc.amount_cents
        out.append(
            Occurrence(
                key=spec.key,
                date=eff_date.isoformat(),
                original_date=orig_iso,
                amount_cents=amount,
                direction=spec.direction,
                counterparty=spec.counterparty,
                currency=spec.currency,
                iban=spec.iban,
                origin="series",
                source_id=spec.key,
                exception=marker,
            )
        )
    out.sort(key=lambda o: (o.date, o.key, o.original_date))
    return out


def next_occurrences(
    spec: SeriesSpec,
    count: int,
    on_or_after: str,
    exceptions: Optional[Mapping[str, SeriesException]] = None,
) -> List[Occurrence]:
    """Die naechsten ``count`` Vorkommen ab ``on_or_after`` (mit Ausnahmen).

    Suchfenster: bis zu 3 Jahre in die Zukunft, damit auch jaehrliche
    Serien mit Loechern sicher genug Treffer liefern.
    """
    if count <= 0:
        return []
    start = date.fromisoformat(on_or_after)
    end = start + timedelta(days=366 * 3)
    if spec.effective_to is not None:
        e = date.fromisoformat(spec.effective_to)
        if e < start:
            return []
        end = min(end, e)
    return expand_series(
        spec, exceptions=exceptions, window_start=on_or_after, window_end=end.isoformat()
    )[:count]


@dataclass(frozen=True)
class SeriesCandidate:
    """Erkennungskandidat aus Buchungs-Beobachtungen (Prompt §5.1/§5.4).

    * 2+ Beobachtungen => PRUEFBARER VORSCHLAG (status 'pending'),
      NIE automatische Bestätigung.
    * ``fingerprint``: stabil aus fachlichem Scope (iban, currency,
      direction, counterparty, cadence, period_n, anchor) — kein
      Zeilenindex, kein Anzeigetext (Prompt §5.1).
    * ``evidence``: Beobachtungs-IDs/Daten/Betraege (stabil, sortiert).
    * ``confidence``: 'low' (2 Belege) | 'medium' (3) | 'high' (>=4).
    """

    fingerprint: str
    iban: str
    currency: str
    direction: str  # 'income' | 'expense'
    counterparty: str
    cadence: str  # VALID_CADENCES
    period_n: int
    anchor_day: int
    anchor_date: str  # ISO (erste Beobachtung)
    amount_cents: int  # letzter (aktuellster) Betrag, > 0
    evidence: Tuple[Tuple[str, str, int], ...] = ()  # (txn_id, date, amount)
    confidence: str = "low"


def candidate_fingerprint(
    iban: str,
    currency: str,
    direction: str,
    counterparty: str,
    cadence: str,
    period_n: int,
    anchor_day: int,
    anchor_date: str = "",
) -> str:
    """Stabler, deterministischer Kandidaten-Fingerprint (Prompt §5.1)."""
    if not isinstance(iban, str) or not "".join(iban.split()):
        raise ValueError(f"iban must be a non-empty string, got {iban!r}")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError(f"currency must be a non-empty string, got {currency!r}")
    if direction not in ("income", "expense"):
        raise ValueError(f"direction must be income|expense, got {direction!r}")
    if not isinstance(counterparty, str) or not counterparty.strip():
        raise ValueError(f"counterparty must be a non-empty string, got {counterparty!r}")
    if cadence not in VALID_CADENCES:
        raise ValueError(f"cadence must be one of {VALID_CADENCES}, got {cadence!r}")
    if isinstance(period_n, bool) or not isinstance(period_n, int) or period_n < 1:
        raise ValueError(f"period_n must be int >= 1, got {period_n!r}")
    if isinstance(anchor_day, bool) or not isinstance(anchor_day, int) or not 1 <= anchor_day <= 31:
        raise ValueError(f"anchor_day must be 1..31, got {anchor_day!r}")
    anchor_iso = date.fromisoformat(anchor_date).isoformat() if anchor_date else ""
    payload = {
        "iban": "".join(iban.split()).upper(),
        "currency": currency.strip().upper(),
        "direction": direction,
        "counterparty": " ".join(counterparty.lower().split()),
        "cadence": cadence,
        "period_n": period_n,
        "anchor_day": anchor_day,
        "anchor": anchor_iso,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _month_gap(a: date, b: date) -> Optional[int]:
    """Kalendermonat-Absstand a -> b (ganze Monate, >= 1) oder None.

    Tagesdrift bis 3 Tage wird als Clamping/Drift toleriert
    (z. B. Anker 31: 2026-01-31 -> 2026-02-28 zaehlt als 1 Monat).
    """
    gap = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day - 3:
        gap -= 1
    return gap if gap >= 1 else None


def estimate_cadence(
    dates: Sequence[str],
) -> Optional[Tuple[str, int, int, str]]:
    """Schaeetzt (cadence, period_n, anchor_day, confidence) aus Terminen.

    Konservativ (Prompt §5.1: "Variable, wiederholte Kaufe sind noch
    keine festen Rechnungstermine"):

    * < 2 Beobachtungen oder inkonsistente Abstaende => None.
    * Wochenbasiert (Abstaende <= 58 Tage): alle Abstaende ~ k * 7*N
      Tage (Toleranz 2 Tage), Mehrheit der Abstaende ist genau k=1.
    * Monatsbasiert (Tageskomponente stabil ±3, Clamping-Toleranz):
      alle Abstaende sind ganze Kalendermonate; ganzzahlige Vielfache
      des Rhythmus sind erlaubte Luecken (ausgelassene Perioden), die
      Mehrheit der Abstaende ist genau 1 Periode. N=1 => monthly,
      2..11 => n_months, 12 => yearly.
    * confidence: 2 Belege 'low', 3 'medium', >=4 'high'.
    """
    ds = sorted(date.fromisoformat(d) for d in dates)
    if len(ds) < 2:
        return None
    conf = "low" if len(ds) == 2 else ("medium" if len(ds) == 3 else "high")
    days = [d.day for d in ds]
    base_day = max(set(days), key=days.count)
    pairs = list(zip(ds, ds[1:]))
    # Wochenbasiert: exakte 7-Tage-Schritte, keine Clamping-Toleranz.
    gaps = [(b - a).days for a, b in pairs]
    if all(5 <= g <= 7 * 8 + 2 for g in gaps):
        for n in range(1, 53):
            period = 7 * n
            ks = [round(g / period) for g in gaps]
            if all(k >= 1 and abs(g - k * period) <= 2 for k, g in zip(ks, gaps)) and sum(
                k == 1 for k in ks
            ) * 2 >= len(gaps):
                return ("weekly" if n == 1 else "n_weeks"), n, base_day, conf
    # Monatsbasiert: Tageskomponente stabil (±3, Clamping-Toleranz).
    if all(abs(d - base_day) <= 3 for d in days):
        mgs = [_month_gap(a, b) for a, b in pairs]
        if all(m is not None for m in mgs):
            mgs = [m for m in mgs if m is not None]
            for n in range(1, 12):
                if all(m % n == 0 for m in mgs) and sum(m == n for m in mgs) * 2 >= len(mgs):
                    if n == 1:
                        return "monthly", 1, base_day, conf
                    return "n_months", n, base_day, conf
            if all(m % 12 == 0 for m in mgs) and sum(m == 12 for m in mgs) * 2 >= len(mgs):
                return "yearly", 1, base_day, conf
    return None


def _norm_scope(fact: Mapping[str, Any]) -> Optional[Tuple[str, str, str, str]]:
    """Fachlicher Scope einer Beobachtung (F03/F04): Konto + Waehrung +
    Vorzeichenrichtung + Gegenpartei. Unvollstaendige Fakten => None."""
    iban = "".join(str(fact.get("iban") or "").split()).upper()
    currency = str(fact.get("currency") or "").upper()
    counterparty = " ".join(str(fact.get("counterparty") or "").lower().split())
    amount = fact.get("amount_cents")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
        return None
    if not iban or not currency or not counterparty:
        return None
    # Unvollstaendige Belege duerfen keine Kandidaten bilden (T06):
    # ein gueltiges ISO-Datum ist Pflicht.
    try:
        date.fromisoformat(str(fact["date"]))
    except (KeyError, TypeError, ValueError):
        return None
    direction = "income" if amount > 0 else "expense"
    return iban, currency, direction, counterparty


def detect_candidates(
    facts: Sequence[Mapping[str, Any]],
    min_occurrences: int = 2,
) -> List[SeriesCandidate]:
    """Erkennt pruefbare Serien-Kandidaten aus Ist-Beobachtungen.

    Eingabe (je Fakt): iban, currency, counterparty, transaction_id,
    date (ISO), amount_cents (signed: + Einnahme / - Ausgabe).

    Regeln (Prompt §5.1/§5.4, T06):
    * Gruppierung nach (iban, currency, direction, counterparty) —
      gleiche Gegenpartei auf verschiedenen Konten/Waehrungen bleiben
      ISOLIERT (F03); Erstattungen (+) und Ausgaben (-) sind getrennte
      Gruppen (F04).
    * ``min_occurrences`` (Default 2) => Vorschlag mit confidence 'low';
      NIE automatische Bestätigung.
    * Inkonsistente Abstaende => kein Kandidat (variable Kaeufe sind
      keine festen Termine).
    * Betrag = aktueller (neuester) Betrag der Gruppe (Preisaenderung
      bleibt als letzte Beobachtung erhalten, F02).
    """
    groups: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    for fact in facts:
        scope = _norm_scope(fact)
        if scope is None:
            continue
        groups.setdefault(scope, []).append(dict(fact))
    out: List[SeriesCandidate] = []
    for (iban, currency, direction, counterparty), rows in sorted(groups.items()):
        if len(rows) < max(2, min_occurrences):
            continue
        rows.sort(key=lambda r: (r["date"], str(r.get("transaction_id"))))
        dates = [r["date"] for r in rows]
        est = estimate_cadence(dates)
        if est is None:
            continue
        cadence, period_n, anchor_day, confidence = est
        last = rows[-1]
        out.append(
            SeriesCandidate(
                fingerprint=candidate_fingerprint(
                    iban, currency, direction, counterparty, cadence, period_n, anchor_day,
                    rows[0]["date"],
                ),
                iban=str(rows[0]["iban"]),
                currency=currency,
                direction=direction,
                counterparty=str(rows[0]["counterparty"]),
                cadence=cadence,
                period_n=period_n,
                anchor_day=anchor_day,
                anchor_date=rows[0]["date"],
                amount_cents=abs(int(last["amount_cents"])),
                evidence=tuple(
                    (str(r.get("transaction_id")), r["date"], abs(int(r["amount_cents"]))) for r in rows
                ),
                confidence=confidence,
            )
        )
    out.sort(key=lambda c: (c.iban, c.currency, c.direction, c.counterparty, c.anchor_date))
    return out


@dataclass(frozen=True)
class MatchResult:
    """Ist-Abgleich eines einzelnen Planvorkommens (T8/T12).

    reason: 'ok' | 'direction_mismatch' | 'scope_mismatch' |
    'date_window' | 'amount' | 'invalid'. Konservativ: fehlende Daten,
    falsche Richtung, falsche IBAN/Gegenpartei/Waehrung, Termin
    ausserhalb des Fensters oder Betrag ausserhalb der Toleranz =>
    Fehlmatch (nur melden, nie raten).
    """

    occurrence_key: str
    matched_transaction_id: Optional[str]  # None bei Fehlmatch
    reason: str  # 'ok' | 'direction_mismatch' | 'scope_mismatch' |
    # 'date_window' | 'amount' | 'invalid'

    def __post_init__(self) -> None:
        if self.reason not in (
            "ok",
            "direction_mismatch",
            "scope_mismatch",
            "date_window",
            "amount",
            "invalid",
        ):
            raise ValueError(f"invalid MatchResult.reason {self.reason!r}")
        if self.reason == "ok" and not self.matched_transaction_id:
            raise ValueError("reason 'ok' requires matched_transaction_id")





def match_occurrence(
    occ: Occurrence,
    actual: Mapping[str, Any],
    window_days: int = 1,
) -> MatchResult:
    """Konservativer 1:1-Abgleich eines Vorkommens mit EINEM Beleg.

    Passend = gleiche Richtung (explizit oder aus dem Vorzeichen),
    gleiche IBAN/Gegenpartei/Waehrung (wenn am Beleg gesetzt), Datum
    innerhalb ±``window_days`` um den wirksamen Termin und Betrag
    innerhalb der Beleg-Toleranz (``tolerance_cents`` am Beleg; fehlt
    sie, muss der Betrag exakt stimmen). Mehrdeutigkeit wird auf
    ``match_occurrences``-Ebene gemeldet, nie automatisch aufgelost.
    """
    if window_days < 0:
        raise ValueError("window_days must be >= 0")
    if isinstance(actual.get("amount_cents"), bool):
        return MatchResult(occ.key, None, "invalid")
    try:
        signed = int(actual["amount_cents"])
        odate = date.fromisoformat(occ.date)
        adate = date.fromisoformat(str(actual["date"]))
    except (KeyError, TypeError, ValueError):
        return MatchResult(occ.key, None, "invalid")
    if signed == 0:
        return MatchResult(occ.key, None, "invalid")
    # Richtung: explizit (income/expense), sonst aus dem Vorzeichen.
    # Erstattungen (+) sind Einnahmen und matchen nie Ausgaben (F04).
    explicit = actual.get("direction")
    if explicit not in ("income", "expense"):
        explicit = "expense" if signed < 0 else "income"
    if explicit != occ.direction:
        return MatchResult(occ.key, None, "direction_mismatch")
    # Scope: IBAN (normalisiert), Gegenpartei, Waehrung (F03).
    a_iban = "".join(str(actual.get("iban", "")).split()).upper()
    if a_iban and occ.iban and a_iban != "".join(occ.iban.split()).upper():
        return MatchResult(occ.key, None, "scope_mismatch")
    a_cp = " ".join(str(actual.get("counterparty", "")).split()).lower()
    if a_cp != " ".join(occ.counterparty.split()).lower():
        return MatchResult(occ.key, None, "scope_mismatch")
    a_cur = str(actual.get("currency", "")).strip().upper()
    if occ.currency and a_cur != occ.currency.strip().upper():
        return MatchResult(occ.key, None, "scope_mismatch")
    if abs((adate - odate).days) > window_days:
        return MatchResult(occ.key, None, "date_window")
    diff = abs(abs(signed) - occ.amount_cents)
    tol = actual.get("tolerance_cents")
    if isinstance(tol, int) and not isinstance(tol, bool) and tol > 0:
        if diff >= tol:
            return MatchResult(occ.key, None, "amount")
    elif diff:
        return MatchResult(occ.key, None, "amount")
    return MatchResult(occ.key, str(actual["transaction_id"]), "ok")


def match_occurrences(
    occs: Sequence[Occurrence],
    actuals: Sequence[Mapping[str, Any]],
    window_days: int = 1,
) -> Dict[str, str]:
    """Konservatives 1:1-Matchen (T8/T12).

    Ein Vorkommen matcht nur, wenn genau ein Ist-Beleg passt und dieser
    von KEINER anderen Vorkommen-Instanz beansprucht wird. Wird ein
    Beleg von mehreren Instanzen beansprucht (Mehrdeutigkeit), bleibt
    er ungematcht — Ambiguitaet wird nur gemeldet, nie automatisch
    aufgelost (Prompt: "vorerst nur 1:1").
    """
    candidates: Dict[str, List[str]] = {}
    for o in occs:
        candidates[o.key] = [
            str(a["transaction_id"])
            for a in actuals
            if match_occurrence(o, a, window_days=window_days).reason == "ok"
        ]
    claims: Dict[str, int] = {}
    for hits in candidates.values():
        for t in hits:
            claims[t] = claims.get(t, 0) + 1
    contested = {t for t, n in claims.items() if n > 1}
    used: set = set()
    out: Dict[str, str] = {}
    for o in occs:
        hits = candidates[o.key]
        if len(hits) == 1 and hits[0] not in used and hits[0] not in contested:
            out[o.key] = hits[0]
            used.add(hits[0])
    return out