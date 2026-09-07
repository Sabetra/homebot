"""Streamlit-Tab für Finanz-Analyse (Kontoauszüge).

Sub-Tabs:
    1. Import        -- PDF-Auszüge hochladen, in Finanz-DB persistieren.
    2. Konten        -- Übersicht aller bekannten Banken/Konten.
    3. Auswertungen  -- Aggregate, Kontostand, Top-Empfänger (Plotly).
    4. Buchungen     -- Filterbare Detail-Liste.

Strenger Pull-Pfad: Tab nutzt ausschliesslich ``finance.*``-Module --
keine Imports aus dem RAG-/KG-Stack, keine direkten DB-Pfade. Die einzige
Bot-seitige Abhängigkeit ist die LLM-Client-Referenz für den Extractor,
die über ``st.session_state.chat_logic.model_loader`` geholt wird (Lazy).
"""

from __future__ import annotations

import logging
import tempfile
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

import pandas as pd
import streamlit as st
from i18n import t as i18n_t

from finance.db_schema import FinanceDB
from finance.models import DEFAULT_CURRENCY
from utils.followup_question_extractor import extract_followup_questions

if TYPE_CHECKING:
    from finance.extractor import StatementImportResult

logger = logging.getLogger(__name__)


def _tr(key: str, default: str, **kwargs: Any) -> str:
    """Translate with safe fallback to the provided default text."""
    translated = i18n_t(key, **kwargs)
    if translated == key:
        if kwargs:
            try:
                return default.format(**kwargs)
            except Exception:
                return default
        return default
    return translated


# ---------------------------------------------------------------------------
# Type-safe helpers for pandas interoperability
# ---------------------------------------------------------------------------

def _safe_int(value: Any) -> int:
    """Safely extract a scalar int from a value that may be a pandas Series,
    ndarray, or plain scalar.  Raises ValueError when the value cannot be
    interpreted as an integer (SOTA: no silent fallbacks)."""
    if isinstance(value, pd.Series):
        # Series from iterrows() should have exactly one element
        scalar = value.iloc[0]
    elif hasattr(value, "item"):
        # ndarray path
        scalar = value.item()  # type: ignore[attr-defined]
    else:
        scalar = value
    return int(scalar)  # type: ignore[arg-type]


def _scalar(value: Any) -> Any:
    """Extract a plain Python scalar from a value that may be wrapped in a
    pandas container (Series / ndarray).  Used for non-numeric columns."""
    if isinstance(value, pd.Series):
        return value.iloc[0]
    if hasattr(value, "item"):
        return value.item()  # type: ignore[attr-defined]
    return value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_llm_client():
    """Holt den LLM-Client (model_loader) aus dem Bot-Session-State."""
    chat_logic = st.session_state.get("chat_logic")
    if chat_logic is None:
        return None
    return getattr(chat_logic, "model_loader", None)


def _format_eur(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _relink_transfers(db: FinanceDB, *, productive_window_days: int) -> int:
    return db.relink_all_transfers(max_days=productive_window_days)


# ---------------------------------------------------------------------------
# Sub-Tab: Import
# ---------------------------------------------------------------------------


def _render_import_tab(db: FinanceDB) -> None:
    st.subheader(_tr("finance_ui.import.subheader", "📥 Kontoauszug-Import"))
    st.caption(
        _tr(
            "finance_ui.import.caption",
            "PDF-Kontoauszug hochladen -> Docling extrahiert Text + Tabellen -> strukturierter LLM-Output befuellt die Finanz-DB. Re-Imports desselben PDFs werden idempotent erkannt (SHA-256 Hash).",
        )
    )

    uploaded = st.file_uploader(
        _tr("finance_ui.import.uploader", "Kontoauszug-PDF(s) hochladen"),
        type=["pdf"],
        key="finance_pdf_upload",
        accept_multiple_files=True,
        help=(
            "Bank-Kontoauszug als PDF. Mehrere Banken werden über IBAN automatisch unterschieden. "
            "Mehrere Dateien werden im Pipeline-Modus importiert (Docling parallel zur LLM-Stage)."
        ),
    )

    if not uploaded:
        return

    if st.button(_tr("finance_ui.import.action", "🚀 Auszug(e) importieren"), type="primary", key="finance_import_btn"):
        llm_client = _get_llm_client()
        if llm_client is None:
            st.error(
                _tr(
                    "finance_ui.import.llm_missing",
                    "❌ LLM-System nicht initialisiert. Bitte zuerst in der Sidebar das AI-System laden, dann erneut importieren.",
                )
            )
            return

        # Alle Uploads auf temp-files materialisieren (Streamlit-UploadedFile
        # ist nur waehrend des Reruns lesbar).
        tmp_paths: List[str] = []
        src_names: List[Optional[str]] = []
        for u in uploaded:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(u.getbuffer())
                tmp_paths.append(tmp.name)
                src_names.append(u.name)

        from finance.extractor import FinanceExtractor

        try:
            extractor = FinanceExtractor(llm_client=llm_client, db=db)

            if len(tmp_paths) == 1:
                with st.spinner(_tr("finance_ui.import.spinner_single", "📄 Docling konvertiert PDF... 🤖 LLM extrahiert Buchungen...")):
                    results = [extractor.import_pdf(tmp_paths[0], source_filename=src_names[0])]
            else:
                progress = st.progress(0.0, text=_tr("finance_ui.import.pipeline_progress", "Pipeline-Import: {done}/{total}", done=0, total=len(tmp_paths)))

                def _on_progress(done: int, total: int) -> None:
                    progress.progress(done / total, text=_tr("finance_ui.import.pipeline_progress", "Pipeline-Import: {done}/{total}", done=done, total=total))

                with st.spinner(_tr("finance_ui.import.spinner_batch", "📄 Docling-Producer + 🤖 LLM-Consumer parallel...")):
                    results = extractor.import_pdfs_batch(
                        tmp_paths,
                        source_filenames=src_names,
                        progress_cb=_on_progress,
                    )
                progress.empty()
        finally:
            for p in tmp_paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass

        # Ergebnisse pro Datei rendern (Schleife ueber alle Imports).
        for src_name, result in zip(src_names, results):
            if len(results) > 1:
                st.markdown(_tr("finance_ui.import.file_header", "---\n### 📄 {name}", name=src_name))
            _render_import_result(result)


def _render_import_result(result: "StatementImportResult") -> None:
    """Rendert ein einzelnes StatementImportResult (UI-Zweig aus Multi-Upload)."""
    if not result.success:
        st.error(_tr("finance_ui.import.failed", "❌ Import fehlgeschlagen: {error}", error=result.error))
        if result.extracted is not None:
            with st.expander(_tr("finance_ui.import.extracted_before_error", "Extrahierte Daten (vor Persistenz-Fehler)")):
                st.json(result.extracted.model_dump())
        return

    if result.skipped_existing_pdf:
        st.info(
            _tr(
                "finance_ui.import.already_imported",
                "ℹ️ Dieses PDF wurde bereits importiert (statement_id={statement_id}). Ueberspringe - keine doppelte Buchung.",
                statement_id=result.statement_id,
            )
        )
    else:
        st.success(
            _tr(
                "finance_ui.import.success",
                "✅ Auszug importiert: {inserted} neue Buchungen, {duplicates} Duplikate verworfen.",
                inserted=result.inserted_transactions,
                duplicates=result.duplicate_transactions,
            )
        )

    if result.reconcile_new_links is not None:
        st.caption(
            _tr(
                "finance_ui.import.reconcile",
                "Post-Import-Reconcile: {count} neue Verknuepfung(en) angelegt.",
                count=result.reconcile_new_links,
            )
        )

    if result.settlement_gap_count is not None:
        status_counts = result.settlement_gap_status_counts or {}
        with st.expander(_tr("finance_ui.import.settlement_gaps", "Offene Settlement-Gaps nach Import"), expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Kein Kandidat", int(status_counts.get("no_candidate", 0)))
            c2.metric("Außerhalb Fenster", int(status_counts.get("candidate_out_of_window", 0)))
            c3.metric("Mehrdeutig", int(status_counts.get("ambiguous_in_window", 0)))
            c4.metric("Eindeutig", int(status_counts.get("single_candidate_in_window", 0)))
            st.caption(_tr("finance_ui.import.open_statements", "Gesamt offene Statements: {count}", count=result.settlement_gap_count))

    if result.completeness_check:
        check = result.completeness_check
        sev = str(check.get("severity") or "info").lower()
        msg = str(check.get("message") or "")
        status = str(check.get("status") or "")
        if sev == "error":
            st.error(_tr("finance_ui.import.completeness", "Vollstaendigkeits-Check: {status} - {message}", status=status, message=msg))
        elif sev == "warning":
            st.warning(_tr("finance_ui.import.completeness", "Vollstaendigkeits-Check: {status} - {message}", status=status, message=msg))
        else:
            st.info(_tr("finance_ui.import.completeness", "Vollstaendigkeits-Check: {status} - {message}", status=status, message=msg))

        with st.expander(_tr("finance_ui.import.completeness_details", "Details Vollstaendigkeits-Check"), expanded=False):
            st.json(check)

    if result.extracted is not None:
        with st.expander(_tr("finance_ui.import.extracted_details", "Extrahierte Auszugs-Details")):
            ext = result.extracted
            cols = st.columns(3)
            cols[0].metric("Bank", ext.bank.name)
            cols[1].metric("IBAN", ext.account.iban)
            cols[2].metric("Währung", ext.account.currency)
            if ext.opening_balance is not None and ext.closing_balance is not None:
                cols2 = st.columns(2)
                cols2[0].metric("Anfangssaldo", _format_eur(ext.opening_balance))
                cols2[1].metric("Endsaldo", _format_eur(ext.closing_balance))


# ---------------------------------------------------------------------------
# Sub-Tab: Konten
# ---------------------------------------------------------------------------


def _render_accounts_tab(db: FinanceDB) -> None:
    st.subheader(_tr("finance_ui.accounts.subheader", "🏦 Banken & Konten"))
    accounts = db.list_accounts()
    if not accounts:
        st.info(_tr("finance_ui.accounts.empty", "Noch keine Konten erfasst. Bitte zuerst einen Auszug importieren."))
        return

    import pandas as pd

    df = pd.DataFrame(
        [
            {
                "Bank": a.bank_name or "?",
                "IBAN": a.iban,
                "Inhaber": a.account_holder or "—",
                "Währung": a.currency,
                "Typ": a.account_type or "—",
            }
            for a in accounts
        ]
    )
    st.dataframe(df, width='stretch', hide_index=True)

    with st.expander(_tr("finance_ui.accounts.type_expander", "Konto-Typ setzen / aendern"), expanded=False):
        st.caption(
            _tr(
                "finance_ui.accounts.type_caption",
                "Der Konto-Typ steuert die Cashflow-Erkennung - z.B. erkennt das System bei `credit_card` automatisch die Sammelbelastung auf dem Girokonto als Transfer und vermeidet Doppelzaehlungen.",
            )
        )
        cols = st.columns([3, 2, 1])
        iban_options = [a.iban for a in accounts]
        sel_iban = cols[0].selectbox(_tr("finance_ui.accounts.account_label", "Konto"), iban_options, key="finance_acct_type_iban")
        sel_type = cols[1].selectbox(
            _tr("finance_ui.accounts.type_label", "Typ"), ["checking", "credit_card", "savings", "cash", "investment", "other"],
            key="finance_acct_type_kind",
        )
        if cols[2].button(_tr("finance_ui.accounts.set_button", "Setzen"), key="finance_acct_type_set"):
            sel_acc = next((a for a in accounts if a.iban == sel_iban), None)
            if sel_acc:
                db.set_account_type(sel_acc.id, sel_type)
                st.success(_tr("finance_ui.accounts.type_set_success", "Typ fuer {iban} gesetzt: {type}", iban=sel_iban, type=sel_type))
                st.rerun()


def _render_analytics_tab(db: FinanceDB) -> None:
    st.subheader(_tr("finance_ui.analytics.subheader", "📊 Auswertungen"))
    accounts = db.list_accounts()
    if not accounts:
        st.info(_tr("finance_ui.analytics.empty", "Noch keine Daten. Bitte zuerst einen Auszug importieren."))
        return

    account_options = {f"{a.bank_name or '?'} — {a.iban}": a.id for a in accounts}
    account_options = {"(alle Konten)": None, **account_options}
    selected_label = st.selectbox(
        _tr("finance_ui.analytics.account_label", "Konto"), list(account_options.keys()), key="finance_analytics_account"
    )
    account_id: Optional[int] = account_options[selected_label]

    col1, col2 = st.columns(2)
    start = col1.date_input(_tr("finance_ui.analytics.date_from", "Von"), value=None, key="finance_analytics_start")
    end = col2.date_input(_tr("finance_ui.analytics.date_to", "Bis"), value=None, key="finance_analytics_end")
    start_str = start.isoformat() if isinstance(start, date) else None
    end_str = end.isoformat() if isinstance(end, date) else None

    # Monthly trend
    monthly = db.aggregate(
        group_by="month",
        account_id=account_id,
        start_date=start_str,
        end_date=end_str,
    )
    if monthly:
        import pandas as pd
        import plotly.graph_objects as go

        df_m = pd.DataFrame(
            [
                {
                    "Monat": r["key"],
                    "Einnahmen": r["income_cents"] / 100.0,
                    "Ausgaben": -r["expense_cents"] / 100.0,  # absolute für Balken
                    "Netto": r["net_cents"] / 100.0,
                }
                for r in monthly
            ]
        )
        fig = go.Figure()
        fig.add_bar(x=df_m["Monat"], y=df_m["Einnahmen"], name="Einnahmen", marker_color="#2ecc71")
        fig.add_bar(x=df_m["Monat"], y=-df_m["Ausgaben"], name="Ausgaben", marker_color="#e74c3c")
        fig.add_scatter(
            x=df_m["Monat"], y=df_m["Netto"], name="Netto", mode="lines+markers",
            line=dict(color="#34495e", width=3),
        )
        fig.update_layout(
            barmode="relative", title="Monatliche Einnahmen / Ausgaben / Netto",
            yaxis_title=DEFAULT_CURRENCY, xaxis_title="Monat",
        )
        st.plotly_chart(fig, width='stretch')
    else:
        st.info(_tr("finance_ui.analytics.no_period_data", "Keine Buchungen im gewaehlten Zeitraum."))

    # Top counterparties
    st.markdown(_tr("finance_ui.analytics.top_counterparties", "### Top-Empfaenger / Auftraggeber"))
    top = db.aggregate(
        group_by="counterparty",
        account_id=account_id,
        start_date=start_str,
        end_date=end_str,
    )
    if top:
        import pandas as pd

        df_top = pd.DataFrame(
            [
                {
                    "Gegenseite": r["key"],
                    f"Einnahmen ({DEFAULT_CURRENCY})": r["income_cents"] / 100.0,
                    f"Ausgaben ({DEFAULT_CURRENCY})": r["expense_cents"] / 100.0,
                    f"Netto ({DEFAULT_CURRENCY})": r["net_cents"] / 100.0,
                    "Anzahl": r["count"],
                }
                for r in top
            ]
        )
        df_top = df_top.reindex(
            df_top[f"Netto ({DEFAULT_CURRENCY})"].abs().sort_values(ascending=False).index
        ).head(20)
        st.dataframe(df_top, width='stretch', hide_index=True)

    # Balance at date
    if account_id is not None:
        st.markdown(_tr("finance_ui.analytics.balance_on_date", "### Kontostand zum Stichtag"))
        as_of = st.date_input(_tr("finance_ui.analytics.cutoff_date", "Stichtag"), value=date.today(), key="finance_balance_date")
        balance = db.balance_at(account_id=account_id, as_of_date=as_of.isoformat())
        st.metric(_tr("finance_ui.analytics.balance_label", "Saldo am {date}", date=as_of.isoformat()), _format_eur(balance["balance"]))

    # --- Kategorien-Aufschlüsselung ---
    st.markdown(_tr("finance_ui.analytics.category_breakdown", "### Kategorien-Aufschluesselung"))
    cat_agg = db.aggregate(
        group_by="category",
        account_id=account_id,
        start_date=start_str,
        end_date=end_str,
    )
    if cat_agg:
        import pandas as pd
        import plotly.express as px

        # Trennung: nur Ausgaben (negativ) für Pie-Chart
        df_cat = pd.DataFrame(
            [
                {
                    "Kategorie": r["key"],
                    "Einnahmen": r["income_cents"] / 100.0,
                    "Ausgaben (abs)": -r["expense_cents"] / 100.0,
                    "Netto": r["net_cents"] / 100.0,
                }
                for r in cat_agg
            ]
        )
        df_exp = df_cat[df_cat["Ausgaben (abs)"] > 0].copy()
        if not df_exp.empty:
            fig_pie = px.pie(
                df_exp,
                names="Kategorie",
                values="Ausgaben (abs)",
                title="Ausgabenverteilung nach Kategorie",
                hole=0.4,
            )
            st.plotly_chart(fig_pie, width='stretch')
        st.dataframe(df_cat, width='stretch', hide_index=True)

    # --- Monats-Report mit Budget-Status ---
    st.markdown(_tr("finance_ui.analytics.monthly_report", "### Monats-Report"))
    rep_month = st.text_input(
        _tr("finance_ui.analytics.month_input", "Monat (YYYY-MM)"),
        value=date.today().strftime("%Y-%m"),
        key="finance_report_month",
    )
    if st.button(_tr("finance_ui.analytics.show_report", "Report anzeigen"), key="finance_report_btn"):
        rep = db.monthly_report(rep_month, account_id=account_id)
        cols = st.columns(4)
        cols[0].metric(_tr("finance_ui.analytics.income_metric", "Einnahmen"), _format_eur(rep["income_cents"] / 100.0))
        cols[1].metric(_tr("finance_ui.analytics.expense_metric", "Ausgaben"), _format_eur(rep["expense_cents"] / 100.0))
        cols[2].metric(_tr("finance_ui.analytics.net_metric", "Netto"), _format_eur(rep["net_cents"] / 100.0))
        cols[3].metric(_tr("finance_ui.analytics.bookings_metric", "Buchungen"), str(rep["tx_count"]))
        if rep["budget_status"]:
            import pandas as pd

            st.markdown(_tr("finance_ui.analytics.budget_status", "#### Budget-Status"))
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Kategorie": r["category"],
                            "Budget (€)": r["budget_cents"] / 100.0,
                            "Ist (€)": r["actual_cents"] / 100.0,
                            "Rest (€)": r["remaining_cents"] / 100.0,
                            "Buchungen": r["tx_count"],
                        }
                        for r in rep["budget_status"]
                    ]
                ),
                width='stretch',
                hide_index=True,
            )


# ---------------------------------------------------------------------------
# Sub-Tab: Kategorisierung
# ---------------------------------------------------------------------------


def _render_categorization_tab(db: FinanceDB) -> None:
    st.subheader(_tr("finance_ui.categorization.subheader", "🏷️ Kategorisierung"))
    st.caption(
        _tr(
            "finance_ui.categorization.caption",
            "Nachhaltig: einmal kategorisiert mit 'als Regel speichern' -> alle zukuenftigen + bisherigen Buchungen derselben Counterparty werden automatisch zugewiesen. Vorschlaege kommen vom LLM, akzeptiert oder korrigiert wird hier.",
        )
    )

    # --- Kategorien-Verwaltung ---
    with st.expander(_tr("finance_ui.categorization.manage_categories", "Kategorien verwalten"), expanded=False):
        cats = db.list_categories()
        if cats:
            import pandas as pd

            st.dataframe(
                pd.DataFrame(
                    [{"ID": c.id, "Name": c.name, "Typ": c.kind, "Farbe": c.color or ""} for c in cats]
                ),
                width='stretch',
                hide_index=True,
            )
        with st.form("finance_new_category"):
            cols = st.columns([3, 2, 2, 1])
            new_name = cols[0].text_input(_tr("finance_ui.categorization.new_category", "Neue Kategorie"), key="finance_new_cat_name")
            new_kind = cols[1].selectbox(
                _tr("finance_ui.categorization.type_label", "Typ"), ["expense", "income", "transfer"], key="finance_new_cat_kind"
            )
            new_color = cols[2].color_picker(_tr("finance_ui.categorization.color_label", "Farbe"), value="#3498db", key="finance_new_cat_color")
            submitted = cols[3].form_submit_button(_tr("finance_ui.categorization.create_button", "➕ Anlegen"))
            if submitted and new_name.strip():
                db.upsert_category(name=new_name.strip(), kind=new_kind, color=new_color)
                st.success(_tr("finance_ui.categorization.category_saved", "Kategorie '{name}' angelegt/aktualisiert.", name=new_name))
                st.rerun()

    # --- Aktive Auto-Regeln ---
    with st.expander(_tr("finance_ui.categorization.active_rules", "Aktive Auto-Regeln"), expanded=False):
        rules = db.list_rules()
        if not rules:
            st.info(_tr("finance_ui.categorization.no_rules", "Keine Regeln aktiv. Wenn du unten eine Buchung mit 'Als Regel speichern' kategorisierst, erscheint sie hier."))
        else:
            import pandas as pd

            df_r = pd.DataFrame(
                [
                    {
                        "ID": r.id,
                        "Match-IBAN": r.match_iban or "",
                        "Match-Counterparty": r.match_counterparty or "",
                        "Kategorie": r.category_name or "",
                    }
                    for r in rules
                ]
            )
            st.dataframe(df_r, width='stretch', hide_index=True)
            del_id = st.number_input(
                _tr("finance_ui.categorization.rule_delete_id", "Regel-ID loeschen"), min_value=0, step=1, value=0, key="finance_rule_delete"
            )
            if st.button(_tr("finance_ui.categorization.delete_rule", "🗑️ Regel loeschen"), key="finance_rule_delete_btn"):
                if del_id > 0:
                    db.delete_rule(int(del_id))
                    st.success(_tr("finance_ui.categorization.rule_deleted", "Regel geloescht."))
                    st.rerun()

        if st.button(_tr("finance_ui.categorization.apply_rules", "🔄 Regeln auf existierende Buchungen anwenden (Backfill)"), key="finance_rules_apply"):
            applied = db.apply_rules(only_uncategorized=True)
            st.success(_tr("finance_ui.categorization.rules_applied", "{count} Buchung(en) durch Regeln zugewiesen.", count=applied))
            st.rerun()

    # --- LLM-Vorschläge für unkategorisierte Buchungen ---
    st.markdown(_tr("finance_ui.categorization.uncategorized", "### Unkategorisierte Buchungen"))
    uncategorized = db.list_uncategorized(limit=200)
    if not uncategorized:
        st.success(_tr("finance_ui.categorization.all_categorized", "✅ Alle Buchungen sind kategorisiert."))
        return

    st.caption(_tr("finance_ui.categorization.uncategorized_caption", "{count} unkategorisierte Buchung(en) (Limit: 200).", count=len(uncategorized)))
    st.caption(_tr("finance_ui.categorization.internal_transfers_hidden", "Interne Transfers werden hier bewusst ausgeblendet."))

    col_a, col_b = st.columns([2, 1])
    # Default-Batch-Groesse strukturell aus n_ctx ableiten -- so nutzt der
    # Categorizer den Kontext maximal aus, ohne zu uebersteuern.
    from finance.categorizer import recommended_batch_size

    llm_for_hint = _get_llm_client()
    n_ctx_hint = getattr(llm_for_hint, "_cached_n_ctx", None) if llm_for_hint else None
    default_batch = recommended_batch_size(int(n_ctx_hint) if isinstance(n_ctx_hint, int) and n_ctx_hint > 0 else 16384)
    default_batch = max(5, min(50, default_batch))  # UI-Slider-Grenzen einhalten
    batch_size = col_a.slider(
        _tr("finance_ui.categorization.batch_size_label", "Batch-Groesse fuer LLM-Vorschlaege"),
        5,
        50,
        default_batch,
        key="finance_cat_batch",
        help=_tr("finance_ui.categorization.batch_size_help", "Default {batch} aus n_ctx={nctx} abgeleitet (maximale Kontext-Auslastung).", batch=default_batch, nctx=n_ctx_hint or '?'),
    )
    if col_b.button(_tr("finance_ui.categorization.suggest_button", "🤖 LLM-Vorschlaege holen"), type="primary", key="finance_cat_suggest"):
        llm = _get_llm_client()
        if llm is None:
            st.error(_tr("finance_ui.categorization.llm_missing", "❌ LLM nicht initialisiert. Bitte AI-System in der Sidebar laden."))
        else:
            try:
                from finance.categorizer import FinanceCategorizer

                cat = FinanceCategorizer(llm_client=llm, db=db)
                with st.spinner(_tr("finance_ui.categorization.llm_spinner", "LLM kategorisiert (strukturiert via GBNF)...")):
                    suggestions = cat.suggest(limit=batch_size)
                st.session_state["finance_cat_suggestions"] = [s.model_dump() for s in suggestions]
                if not suggestions:
                    st.warning(_tr("finance_ui.categorization.no_suggestions", "Keine Vorschlaege erhalten."))
            except Exception as exc:
                logger.exception("LLM categorization failed")
                st.error(_tr("finance_ui.categorization.llm_failed", "❌ LLM-Kategorisierung fehlgeschlagen: {error}", error=exc))

    # Bestehende Vorschläge anzeigen + Editier-/Übernahme-Workflow
    sug_state = st.session_state.get("finance_cat_suggestions") or []
    if sug_state:
        st.markdown(_tr("finance_ui.categorization.suggestions_header", "#### LLM-Vorschlaege - pruefen, ggf. korrigieren, uebernehmen"))
        sug_by_id = {int(s["transaction_id"]): s for s in sug_state}
        # Mapping tx_id  Buchungs-Anzeige
        tx_by_id = {t.id: t for t in uncategorized}
        editable_rows = []
        for tx_id, sug in sug_by_id.items():
            tx = tx_by_id.get(tx_id)
            if tx is None:
                continue
            editable_rows.append(
                {
                    "tx_id": tx_id,
                    "Datum": tx.booking_date,
                    "Betrag": tx.amount_cents / 100.0,
                    "Natur": tx.transaction_nature,
                    "Counterparty": tx.counterparty or "",
                    "Zweck": (tx.purpose or "")[:80],
                    "LLM-Kategorie": sug["category"],
                    "Confidence": sug.get("confidence", 0.7),
                    "Als Regel": bool(sug.get("create_rule", False)),
                    "Übernehmen": True,
                }
            )
        if editable_rows:
            import pandas as pd

            df_sug = pd.DataFrame(editable_rows)
            edited = st.data_editor(
                df_sug,
                width='stretch',
                hide_index=True,
                key="finance_cat_editor",
                disabled=["tx_id", "Datum", "Betrag", "Natur", "Counterparty", "Zweck", "Confidence"],
            )
            if st.button(_tr("finance_ui.categorization.apply_selected", "✅ Markierte uebernehmen"), type="primary", key="finance_cat_apply"):
                applied_count = 0
                rules_count = 0
                for _, row in edited.iterrows():
                    if not bool(row["Übernehmen"]):
                        continue
                    tx_id = int(row["tx_id"])
                    cat_name = str(row["LLM-Kategorie"]).strip()
                    if not cat_name:
                        continue
                    tx = tx_by_id.get(tx_id)
                    kind = "income" if tx and tx.amount_cents > 0 else "expense"
                    cat_id = db.upsert_category(name=cat_name, kind=kind)
                    db.assign_category(tx_id, cat_id, source="user", confidence=1.0)
                    applied_count += 1
                    if bool(row["Als Regel"]) and tx is not None:
                        if tx.counterparty_iban:
                            db.upsert_rule(category_id=cat_id, match_iban=tx.counterparty_iban)
                            rules_count += 1
                        elif tx.counterparty:
                            db.upsert_rule(category_id=cat_id, match_counterparty=tx.counterparty)
                            rules_count += 1
                if rules_count:
                    extra = db.apply_rules(only_uncategorized=True)
                    st.success(
                        _tr(
                            "finance_ui.categorization.success_with_rules",
                            "✅ {applied} uebernommen, {rules} Regel(n) angelegt, {extra} weitere Buchungen rueckwirkend zugewiesen.",
                            applied=applied_count,
                            rules=rules_count,
                            extra=extra,
                        )
                    )
                else:
                    st.success(_tr("finance_ui.categorization.success_applied", "✅ {count} Buchung(en) kategorisiert.", count=applied_count))
                st.session_state.pop("finance_cat_suggestions", None)
                st.rerun()

    # --- Manuelle Schnell-Zuweisung ohne LLM ---
    with st.expander(_tr("finance_ui.categorization.manual_assignment", "Manuelle Zuweisung"), expanded=False):
        import pandas as pd

        df_uc = pd.DataFrame(
            [
                {
                    "ID": t.id,
                    "Datum": t.booking_date,
                    "Betrag": t.amount_cents / 100.0,
                    "Natur": t.transaction_nature,
                    "Counterparty": t.counterparty or "",
                    "Zweck": (t.purpose or "")[:80],
                }
                for t in uncategorized[:50]
            ]
        )
        st.dataframe(df_uc, width='stretch', hide_index=True)
        cats = db.list_categories()
        cat_names = [c.name for c in cats]
        if not cat_names:
            st.info(_tr("finance_ui.categorization.create_categories_first", "Bitte zuerst Kategorien anlegen (oben)."))
            return
        cols = st.columns([1, 2, 1, 1])
        manual_id = cols[0].number_input(_tr("finance_ui.categorization.manual_tx", "Tx-ID"), min_value=0, step=1, value=0, key="finance_manual_tx")
        manual_cat = cols[1].selectbox(_tr("finance_ui.categorization.manual_category", "Kategorie"), cat_names, key="finance_manual_cat")
        manual_rule = cols[2].checkbox(_tr("finance_ui.categorization.manual_rule", "Als Regel"), key="finance_manual_rule")
        if cols[3].button(_tr("finance_ui.categorization.assign_button", "Zuweisen"), key="finance_manual_btn"):
            if manual_id > 0:
                tx = db.get_transaction(int(manual_id))
                if tx is None:
                    st.error(_tr("finance_ui.categorization.tx_not_found", "Tx-ID nicht gefunden."))
                else:
                    kind = "income" if tx.amount_cents > 0 else "expense"
                    cat_id = db.upsert_category(name=manual_cat, kind=kind)
                    db.assign_category(tx.id, cat_id, source="user", confidence=1.0)
                    rules_extra = 0
                    if manual_rule:
                        if tx.counterparty_iban:
                            db.upsert_rule(category_id=cat_id, match_iban=tx.counterparty_iban)
                            rules_extra = db.apply_rules(only_uncategorized=True)
                        elif tx.counterparty:
                            db.upsert_rule(category_id=cat_id, match_counterparty=tx.counterparty)
                            rules_extra = db.apply_rules(only_uncategorized=True)
                    msg = _tr("finance_ui.categorization.assigned_with_rules", "Zugewiesen. {count} weitere durch Regel.", count=rules_extra)
                    st.success(msg)
                    st.rerun()


# ---------------------------------------------------------------------------
# Sub-Tab: Budgets
# ---------------------------------------------------------------------------


def _render_budgets_tab(db: FinanceDB) -> None:
    st.subheader(_tr("finance_ui.budgets.subheader", "🎯 Budgets"))
    st.caption(
        _tr(
            "finance_ui.budgets.caption",
            "Monatliche Soll-Werte pro Kategorie. Ausgaben-Budgets sind negativ (z.B. -400 EUR fuer Lebensmittel-Limit), Einnahmen-Budgets positiv.",
        )
    )
    cats = db.list_categories()
    if not cats:
        st.info(_tr("finance_ui.budgets.need_categories", "Bitte zuerst Kategorien im Tab 'Kategorisierung' anlegen."))
        return

    # --- Neues / aktualisiertes Budget ---
    with st.form("finance_budget_form"):
        cols = st.columns([3, 2, 2, 1])
        cat_name = cols[0].selectbox(
            _tr("finance_ui.budgets.category_label", "Kategorie"), [c.name for c in cats], key="finance_budget_cat"
        )
        from datetime import date as _date

        month = cols[1].text_input(
            _tr("finance_ui.budgets.month_label", "Monat (YYYY-MM)"),
            value=_date.today().strftime("%Y-%m"),
            key="finance_budget_month",
        )
        amount = cols[2].number_input(
            _tr("finance_ui.budgets.amount_label", "Betrag (EUR) (signed)"),
            value=-400.0,
            step=10.0,
            format="%.2f",
            key="finance_budget_amount",
        )
        ok = cols[3].form_submit_button(_tr("finance_ui.budgets.set_budget_button", "💾 Setzen"))
        if ok:
            from finance.db_schema import _to_cents

            cat = next((c for c in cats if c.name == cat_name), None)
            if cat is None:
                st.error(_tr("finance_ui.budgets.category_not_found", "Kategorie nicht gefunden."))
            else:
                db.upsert_budget(
                    category_id=cat.id, month=month, budget_cents=_to_cents(amount)
                )
                st.success(_tr("finance_ui.budgets.budget_set_success", "Budget gesetzt: {category} / {month} = {amount}", category=cat_name, month=month, amount=_format_eur(amount)))
                st.rerun()

    # --- Budget-Liste ---
    budgets = db.list_budgets()
    if not budgets:
        st.info(_tr("finance_ui.budgets.none", "Noch keine Budgets gesetzt."))
        return
    import pandas as pd

    df_b = pd.DataFrame(
        [
            {
                "ID": b.id,
                "Monat": b.month,
                "Kategorie": b.category_name or "?",
                "Budget (€)": b.budget_cents / 100.0,
            }
            for b in budgets
        ]
    )
    st.dataframe(df_b, width='stretch', hide_index=True)

    # --- Soll/Ist für aktuellen / wählbaren Monat ---
    st.markdown(_tr("finance_ui.budgets.target_actual", "### Soll / Ist"))
    sel_month = st.text_input(
        _tr("finance_ui.budgets.eval_month_label", "Monat fuer Auswertung"),
        value=date.today().strftime("%Y-%m"),
        key="finance_budget_status_month",
    )
    status = db.budget_status(sel_month)
    if not status:
        st.info(_tr("finance_ui.budgets.no_data_for_month", "Keine Budget-Daten fuer {month}.", month=sel_month))
        return
    df_st = pd.DataFrame(
        [
            {
                "Kategorie": s["category"],
                "Typ": s["kind"],
                "Budget (€)": s["budget_cents"] / 100.0,
                "Ist (€)": s["actual_cents"] / 100.0,
                "Verbleibend (€)": s["remaining_cents"] / 100.0,
                "Buchungen": s["tx_count"],
            }
            for s in status
        ]
    )
    st.dataframe(df_st, width='stretch', hide_index=True)

    import plotly.graph_objects as go

    # Plot: Soll vs Ist je Kategorie
    fig = go.Figure()
    fig.add_bar(name="Budget", x=df_st["Kategorie"], y=df_st["Budget (€)"], marker_color="#3498db")
    fig.add_bar(name="Ist", x=df_st["Kategorie"], y=df_st["Ist (€)"], marker_color="#e67e22")
    fig.update_layout(
        barmode="group", title=_tr("finance_ui.budgets.chart_title", "Soll vs. Ist - {month}", month=sel_month), yaxis_title=DEFAULT_CURRENCY
    )
    st.plotly_chart(fig, width='stretch')


# ---------------------------------------------------------------------------
# Sub-Tab: Buchungen (Detail-Liste)
# ---------------------------------------------------------------------------


def _render_transactions_tab(db: FinanceDB) -> None:
    st.subheader(_tr("finance_ui.transactions.subheader", "📋 Buchungen"))
    accounts = db.list_accounts()
    account_options = {f"{a.bank_name or '?'} — {a.iban}": a.id for a in accounts}
    account_options = {"(alle Konten)": None, **account_options}
    selected_label = st.selectbox(
        _tr("finance_ui.transactions.account_label", "Konto"), list(account_options.keys()), key="finance_tx_account"
    )
    account_id: Optional[int] = account_options[selected_label]

    col1, col2, col3 = st.columns(3)
    start = col1.date_input(_tr("finance_ui.transactions.date_from", "Von"), value=None, key="finance_tx_start")
    end = col2.date_input(_tr("finance_ui.transactions.date_to", "Bis"), value=None, key="finance_tx_end")
    counterparty = col3.text_input(_tr("finance_ui.transactions.counterparty_filter", "Gegenseite/Zweck enthaelt"), key="finance_tx_filter")

    rows = db.query_transactions(
        account_id=account_id,
        start_date=start.isoformat() if isinstance(start, date) else None,
        end_date=end.isoformat() if isinstance(end, date) else None,
        counterparty_like=counterparty or None,
        limit=500,
    )
    if not rows:
        st.info(_tr("finance_ui.transactions.empty", "Keine Buchungen passen zum Filter."))
        return
    import pandas as pd

    df = pd.DataFrame(
        [
            {
                "ID": t.id,
                "Löschen": False,
                "Datum": t.booking_date,
                "Wertstellung": t.value_date or "",
                "Betrag": t.amount_cents / 100.0,
                "Währ.": t.currency,
                "Natur": t.transaction_nature,
                "Gegenseite": t.counterparty or "",
                "Zweck": t.purpose or "",
                "Typ": t.booking_type or "",
                "Kategorie": t.category or "",
            }
            for t in rows
        ]
    )
    edited = st.data_editor(
        df,
        width='stretch',
        hide_index=True,
        key="finance_tx_editor",
        disabled=[
            "ID",
            "Datum",
            "Wertstellung",
            "Betrag",
            "Währ.",
            "Natur",
            "Gegenseite",
            "Zweck",
            "Typ",
            "Kategorie",
        ],
        column_config={
            "Löschen": st.column_config.CheckboxColumn(
                "Löschen",
                help="Markiere Buchungen, die dauerhaft aus der Finanz-DB gelöscht werden sollen.",
                default=False,
            ),
        },
    )

    selected_ids = [int(row["ID"]) for _, row in edited.iterrows() if bool(row["Löschen"])]
    if selected_ids:
        st.warning(
            _tr(
                "finance_ui.transactions.delete_warning",
                "{count} Buchung(en) zum Loeschen markiert. Loeschen ist dauerhaft und kann verknuepfte Kategorien/Transfers entfernen.",
                count=len(selected_ids),
            )
        )
        confirm = st.text_input(
            _tr("finance_ui.transactions.delete_confirm_input", "Sicherheitsbestaetigung: tippe LOESCHEN"),
            key="finance_tx_delete_confirm",
        )
        if st.button(_tr("finance_ui.transactions.delete_button", "🗑️ Markierte Buchungen loeschen"), type="secondary", key="finance_tx_delete_btn"):
            if confirm.strip().upper() != "LOESCHEN":
                st.error(_tr("finance_ui.transactions.delete_confirm_error", "Bitte exakt LOESCHEN eingeben, um den Vorgang zu bestaetigen."))
            else:
                deleted = 0
                for tx_id in selected_ids:
                    if db.delete_transaction(tx_id):
                        deleted += 1
                st.success(_tr("finance_ui.transactions.deleted_success", "{deleted} von {selected} markierten Buchungen geloescht.", deleted=deleted, selected=len(selected_ids)))
                st.rerun()

    st.caption(_tr("finance_ui.transactions.caption_rows", "{count} Buchungen (Limit: 500)", count=len(rows)))


# ---------------------------------------------------------------------------
# Sub-Tab: Transfers (Kreditkarten-Sammelbelastung u.a.)
# ---------------------------------------------------------------------------


def _render_transfers_tab(db: FinanceDB) -> None:
    st.subheader(_tr("finance_ui.transfers.subheader", "🔁 Transfers zwischen eigenen Konten"))
    st.caption(
        _tr(
            "finance_ui.transfers.caption_overview",
            "Erkennt Geldbewegungen, die nur **zwischen deinen eigenen Konten** stattfinden (z.B. Kreditkartenabrechnung: -450 € auf Girokonto = +450 € auf Kreditkartenkonto). Wenn auf der Kreditkarte kein explizites +450 als Einzelbuchung vorliegt, wird der Ausgleich strukturell ueber Statement-Saldo + Datum erkannt. Verlinkte Buchungen werden in Cashflow-Berichten **nicht doppelt gezaehlt**, in den Buchungslisten aber weiter angezeigt. Auto-Erkennung: rein numerisch (Betrag, Vorzeichen, Statement-Saldo und Datum-Distanz), kein Pattern-Matching.",
        )
    )
    import pandas as pd

    # --- Aktive Verknüpfungen ---
    links = db.list_transfer_links()
    st.markdown(_tr("finance_ui.transfers.active_links", "### Aktive Verknuepfungen ({count})", count=len(links)))
    if links:
        df_links = pd.DataFrame(
            [
                {
                    "Link-ID": link.id,
                    "Datum (out)": link.outgoing_booking_date,
                    "Datum (in)": link.incoming_booking_date,
                    "Betrag (€)": (link.outgoing_amount_cents or 0) / 100.0,
                    "Aus IBAN": link.outgoing_account_iban,
                    "→ Auf IBAN": link.incoming_account_iban,
                    "Counterparty (out)": link.outgoing_counterparty or "—",
                    "Counterparty (in)": link.incoming_counterparty or "—",
                    "Confidence": f"{link.confidence:.2f}",
                    "Quelle": link.source,
                }
                for link in links
            ]
        )
        st.dataframe(df_links, width='stretch', hide_index=True)
        cols = st.columns([2, 1])
        del_id = cols[0].number_input(
            _tr("finance_ui.transfers.link_delete_input", "Link-ID loeschen"), min_value=0, step=1, value=0, key="finance_link_del_id"
        )
        if cols[1].button(_tr("finance_ui.transfers.link_delete_button", "🗑️ Verknuepfung loesen"), key="finance_link_del_btn"):
            if del_id > 0 and db.unlink_transfer(int(del_id)):
                st.success(_tr("finance_ui.transfers.link_deleted_success", "Verknuepfung {id} geloest.", id=int(del_id)))
                st.rerun()
    else:
        st.info(_tr("finance_ui.transfers.none_active", "Noch keine aktiven Transfer-Verknuepfungen."))

    # --- Kandidaten ---
    st.markdown(_tr("finance_ui.transfers.suggestions", "### Vorschlaege zur Verknuepfung"))
    cols = st.columns([2, 1])
    max_days = cols[0].slider(
        _tr("finance_ui.transfers.tolerance_window", "Toleranzfenster (Tage zwischen den beiden Buchungen)"),
        1, 14, 5, key="finance_link_window",
    )
    if cols[1].button(_tr("finance_ui.transfers.rescan_button", "🔍 Erneut suchen"), key="finance_link_rescan"):
        st.rerun()

    candidates = db.detect_transfer_candidates(max_days=max_days)
    if not candidates:
        st.success(_tr("finance_ui.transfers.no_candidates", "✅ Keine offenen Transfer-Kandidaten - alles erkannt oder nichts zu paaren."))
    else:
        st.caption(_tr("finance_ui.transfers.candidates_found", "{count} Kandidat(en) gefunden — verschiedene Konten, exakt entgegengesetzte Betraege.", count=len(candidates)))
        df_c = pd.DataFrame(
            [
                {
                    "out_id": c["outgoing_tx_id"],
                    "in_id": c["incoming_tx_id"],
                    "Betrag (€)": c["amount_cents"] / 100.0,
                    "Datum (out)": c["outgoing_date"],
                    "Datum (in)": c["incoming_date"],
                    "Aus IBAN": c["outgoing_iban"],
                    "→ Auf IBAN": c["incoming_iban"],
                    "Counterparty (out)": c["outgoing_counterparty"] or "—",
                    "Counterparty (in)": c["incoming_counterparty"] or "—",
                    "Tage": int(c["day_diff"]),
                    "Verknüpfen": False,
                }
                for c in candidates
            ]
        )
        edited = st.data_editor(
            df_c,
            width='stretch',
            hide_index=True,
            key="finance_link_editor",
            disabled=[
                "out_id", "in_id", "Betrag (€)", "Datum (out)", "Datum (in)",
                "Aus IBAN", "→ Auf IBAN", "Counterparty (out)", "Counterparty (in)", "Tage",
            ],
        )
        if st.button(_tr("finance_ui.transfers.apply_marked", "✅ Markierte als Transfer verknuepfen"), type="primary", key="finance_link_apply"):
            linked = 0
            errors: List[str] = []
            for _, row in edited.iterrows():
                if not bool(row["Verknüpfen"]):
                    continue
                try:
                    db.link_transfer(
                        outgoing_tx_id=int(row["out_id"]),
                        incoming_tx_id=int(row["in_id"]),
                        source="user",
                    )
                    linked += 1
                except ValueError as exc:
                    errors.append(f"out={row['out_id']}/in={row['in_id']}: {exc}")
            if linked:
                st.success(_tr("finance_ui.transfers.links_created", "✅ {count} Verknuepfung(en) angelegt.", count=linked))
            if errors:
                for e in errors:
                    st.error(e)
            if linked:
                st.rerun()

    st.markdown(_tr("finance_ui.transfers.open_settlement", "### Offene Kreditkarten-Ausgleiche (Ursachenanalyse)"))
    st.caption(
        _tr(
            "finance_ui.transfers.open_settlement_caption",
            "Diagnose offener Statement-Ausgleiche: keine Kandidaten, Kandidaten ausserhalb des Zuordnungsfensters oder Mehrdeutigkeiten. Rein strukturell, ohne Keywords.",
        )
    )
    gap_cols = st.columns([2, 2, 1])
    gap_window = gap_cols[0].slider(
        _tr("finance_ui.transfers.productive_window", "Produktives Zuordnungsfenster (Tage)"),
        14,
        120,
        45,
        key="finance_settlement_gap_window",
    )
    gap_extended = gap_cols[1].slider(
        _tr("finance_ui.transfers.extended_window", "Erweitertes Diagnosefenster (Tage)"),
        gap_window,
        365,
        180,
        key="finance_settlement_gap_extended",
    )
    if gap_cols[2].button(_tr("finance_ui.transfers.relink_button", "♻️ Re-Link"), key="finance_settlement_relink_btn"):
        relinked = _relink_transfers(db, productive_window_days=gap_window)
        st.success(_tr("finance_ui.transfers.relink_success", "{count} neue Verknuepfung(en) durch Re-Linking angelegt.", count=relinked))
        st.rerun()

    gaps = db.detect_statement_settlement_gaps(
        max_days_after_statement=gap_window,
        extended_search_days=gap_extended,
    )
    if not gaps:
        st.success(_tr("finance_ui.transfers.no_open_settlement", "✅ Keine offenen Kreditkarten-Statements ohne Ausgleichszuordnung."))
    else:
        status_labels = {
            "no_candidate": "Kein Kandidat",
            "candidate_out_of_window": "Nur außerhalb Fenster",
            "ambiguous_in_window": "Mehrdeutig im Fenster",
            "single_candidate_in_window": "Eindeutig im Fenster",
        }
        summary = {k: 0 for k in status_labels}
        for g in gaps:
            s = str(g.get("status") or "")
            if s in summary:
                summary[s] += 1

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Kein Kandidat", summary["no_candidate"])
        m2.metric("Außerhalb Fenster", summary["candidate_out_of_window"])
        m3.metric("Mehrdeutig", summary["ambiguous_in_window"])
        m4.metric("Eindeutig", summary["single_candidate_in_window"])

        df_gaps = pd.DataFrame(
            [
                {
                    "Statement-ID": g["statement_id"],
                    "Periodenende": g["period_end"],
                    "Saldo abs (€)": g["abs_closing_balance_cents"] / 100.0,
                    "Status": status_labels.get(g["status"], g["status"]),
                    "Kandidaten im Fenster": len(g["in_window_candidates"]),
                    "Kandidaten erweitert": len(g["extended_candidates"]),
                }
                for g in gaps
            ]
        )
        st.dataframe(df_gaps, width='stretch', hide_index=True)

        with st.expander(_tr("finance_ui.transfers.open_details", "Details zu offenen Faellen"), expanded=False):
            for g in gaps:
                st.markdown(
                    f"**Statement {g['statement_id']}** | Ende: {g['period_end']} | "
                    f"Saldo abs: {g['abs_closing_balance_cents'] / 100.0:.2f} € | "
                    f"Status: {status_labels.get(g['status'], g['status'])}"
                )
                if g["in_window_candidates"]:
                    st.caption(_tr("finance_ui.transfers.candidates_productive", "Kandidaten im produktiven Fenster"))
                    st.dataframe(pd.DataFrame(g["in_window_candidates"]), width='stretch', hide_index=True)
                if g["extended_candidates"] and not g["in_window_candidates"]:
                    st.caption(_tr("finance_ui.transfers.candidates_extended", "Nur erweiterte Kandidaten (ausserhalb produktivem Fenster)"))
                    st.dataframe(pd.DataFrame(g["extended_candidates"]), width='stretch', hide_index=True)


# ---------------------------------------------------------------------------
# Sub-Tab: Chat (Finance-only Function Calling)
# ---------------------------------------------------------------------------


_FINANCE_CHAT_HISTORY_KEY = "finance_chat_messages"
_FINANCE_DRAFT_INPUT_KEY = "finance_chat_draft_input"


def _normalize_followup_questions(raw: object) -> List[str]:
    if not isinstance(raw, list):
        return []
    normalized: List[str] = []
    for item in raw:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                normalized.append(cleaned)
    return normalized


def _hydrate_assistant_message_for_ui(msg: dict) -> tuple[str, List[str]]:
    display_content = msg.get("display_content")
    followup_questions = _normalize_followup_questions(msg.get("followup_questions"))

    if isinstance(display_content, str) and display_content.strip() and "followup_questions" in msg:
        return display_content, followup_questions

    cleaned_answer, extracted_followups = extract_followup_questions(msg.get("content", "") or "")
    display_content = (cleaned_answer or "").strip()
    followup_questions = _normalize_followup_questions(extracted_followups)
    msg["display_content"] = display_content
    msg["followup_questions"] = followup_questions
    return display_content, followup_questions


def _render_followup_buttons(followup_questions: List[str], *, key_prefix: str) -> None:
    if not followup_questions:
        return

    def _set_finance_chat_draft(text: str) -> None:
        st.session_state[_FINANCE_DRAFT_INPUT_KEY] = text

    column_count = min(2, len(followup_questions))
    cols = st.columns(column_count)
    for fq_idx, fq_text in enumerate(followup_questions):
        with cols[fq_idx % column_count]:
            button_label = fq_text if len(fq_text) <= 90 else fq_text[:87] + "..."
            st.button(
                f"➔ {button_label}",
                key=f"{key_prefix}_{fq_idx}",
                help=fq_text,
                width='stretch',
                on_click=_set_finance_chat_draft,
                args=(fq_text,),
            )


def _render_chat_tab() -> None:
    """Multi-Turn-Chat strikt auf finance_*-Tools beschränkt.

    Nutzt eine eigene FinanceChatEngine -- keine Verbindung zum Haupt-Chat-
    State, keine Berührung des RAG-Stores. Eigener Session-State-Key
    (``finance_chat_messages``) verhindert Vermischung mit dem normalen
    Chatverlauf.
    """
    st.subheader(_tr("finance_ui.chat.subheader", "💬 Finanz-Chat"))
    st.caption(
        _tr(
            "finance_ui.chat.caption",
            "Stelle Fragen zu deinen Konten, Buchungen, Budgets oder Transfers. Der Assistent nutzt ausschliesslich lokale Finance-Werkzeuge (kein Internet, kein RAG).",
        )
    )

    llm = _get_llm_client()
    if llm is None:
        st.warning(
            _tr(
                "finance_ui.chat.llm_missing",
                "LLM-Client noch nicht initialisiert. Oeffne den Haupt-Chat einmal, damit das Modell geladen wird.",
            )
        )
        return

    # Toolkit lazy holen (singleton aus chat_logic, falls vorhanden)
    chat_logic = st.session_state.get("chat_logic")
    toolkit = getattr(chat_logic, "agent_toolkit", None)
    if toolkit is None:
        st.error(
            _tr(
                "finance_ui.chat.toolkit_missing",
                "AgentToolkit nicht verfuegbar -- Hauptanwendung muss zuerst vollstaendig initialisiert sein.",
            )
        )
        return

    history: List[dict] = st.session_state.setdefault(_FINANCE_CHAT_HISTORY_KEY, [])

    def _reset_finance_chat_history() -> None:
        st.session_state[_FINANCE_CHAT_HISTORY_KEY] = []
        st.session_state[_FINANCE_DRAFT_INPUT_KEY] = ""

    # Chat-Verlauf rendern
    for i, msg in enumerate(history):
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                display_content, followup_questions = _hydrate_assistant_message_for_ui(msg)
                st.markdown(display_content)
                _render_followup_buttons(followup_questions, key_prefix=f"finance_followup_{i}")
            else:
                st.markdown(msg.get("content", ""))

    # Reset-Button neben Eingabe
    _, col_b = st.columns([5, 1])
    with col_b:
        st.button(
            _tr("finance_ui.chat.clear", "🧹 Verlauf"),
            help=_tr("finance_ui.chat.clear_help", "Finanz-Chat-Verlauf loeschen"),
            width='stretch',
            on_click=_reset_finance_chat_history,
        )

    # Freie Eingabe muss immer moeglich sein; Follow-up-Klicks befuellen nur den Draft.
    with st.form("finance_chat_input_form", clear_on_submit=False):
        user_input = st.text_input(
            _tr("finance_ui.chat.input", "Eigene Frage"),
            key=_FINANCE_DRAFT_INPUT_KEY,
            placeholder=_tr("finance_ui.chat.placeholder", "Frage zu deinen Finanzen ... (z.B. 'Wie viel habe ich im April fuer Lebensmittel ausgegeben?')"),
        )
        send_now = st.form_submit_button(_tr("finance_ui.chat.send", "Senden"), type="primary")

    if not send_now:
        return

    user_input = (user_input or "").strip()
    if not user_input:
        return

    # User message anzeigen + speichern
    history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Engine ausfuehren
    from finance.chat import FinanceChatEngine

    engine = FinanceChatEngine(
        llm_client=llm,
        toolkit=toolkit,
        max_tokens=4096,
        allow_python=False,
    )

    with st.chat_message("assistant"):
        with st.spinner("Analysiere Finanz-Daten ..."):
            # nur user/assistant-Verlauf an die Engine reichen
            result = engine.respond(user_input, history=history[:-1])

        display_answer = result.answer
        _, followup_questions = _hydrate_assistant_message_for_ui({"content": display_answer})

        st.markdown(display_answer)

        if result.trace.tool_calls:
            with st.expander(
                f"🔧 Tool-Aufrufe ({len(result.trace.tool_calls)} in "
                f"{result.trace.iterations} Iterationen)",
                expanded=False,
            ):
                for tc in result.trace.tool_calls:
                    icon = "✅" if tc["ok"] else "⚠️"
                    st.markdown(
                        f"{icon} **{tc['name']}** -- args: `{tc['args']}`"
                    )
        if result.trace.rejected_tools:
            st.warning(
                f"Modell hat versucht, nicht erlaubte Tools aufzurufen: "
                f"{', '.join(result.trace.rejected_tools)} (blockiert)."
            )

        _render_followup_buttons(
            followup_questions,
            key_prefix=f"finance_followup_live_{len(history)}",
        )

    history.append(
        {
            "role": "assistant",
            "content": display_answer,
            "display_content": display_answer,
            "followup_questions": followup_questions,
        }
    )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def render_finance_tab() -> None:
    """Rendert den kompletten Finanz-Tab. Wird aus enhanced_streamlit_bot.main() aufgerufen."""
    st.header(_tr("finance_ui.main.header", "💳 Finanzen"))
    st.caption(
        _tr(
            "finance_ui.main.caption",
            "Bank-Kontoauszuege in dedizierter SQLite-DB analysieren. Strikt getrennt von RAG-Wissensbasis und Knowledge-Graph.",
        )
    )
    # Rechtlicher Hinweis (Pflicht fuer oeffentliche Freigabe — siehe
    # docs/19_LICENSES_AND_COMPLIANCE.md): keine Steuer-/Rechts-/Anlageberatung.
    st.info(
        _tr(
            "finance_ui.main.disclaimer",
            "⚠️ This tool provides financial information and analysis only — it is "
            "not tax, legal, or professional investment advice. All data stays on your device.",
        )
    )
    db = FinanceDB.get_instance()
    sub_tabs = st.tabs(
        [
            _tr("finance_ui.tabs.chat", "💬 Chat"),
            _tr("finance_ui.tabs.import", "📥 Import"),
            _tr("finance_ui.tabs.accounts", "🏦 Konten"),
            _tr("finance_ui.tabs.categorization", "🏷️ Kategorisierung"),
            _tr("finance_ui.tabs.budgets", "🎯 Budgets"),
            _tr("finance_ui.tabs.transfers", "🔁 Transfers"),
            _tr("finance_ui.tabs.analytics", "📊 Auswertungen"),
            _tr("finance_ui.tabs.transactions", "📋 Buchungen"),
        ]
    )
    with sub_tabs[0]:
        _render_chat_tab()
    with sub_tabs[1]:
        _render_import_tab(db)
    with sub_tabs[2]:
        _render_accounts_tab(db)
    with sub_tabs[3]:
        _render_categorization_tab(db)
    with sub_tabs[4]:
        _render_budgets_tab(db)
    with sub_tabs[5]:
        _render_transfers_tab(db)
    with sub_tabs[6]:
        _render_analytics_tab(db)
    with sub_tabs[7]:
        _render_transactions_tab(db)


__all__ = ["render_finance_tab"]
