"""Finanz-Modul: Kontoauszug-Analyse mit dedizierter SQLite-DB.

Strikt getrennt vom RAG-/KG-Stack: Finanz-Daten leben ausschliesslich in
``database/finance.db`` und werden niemals in den Vektor-Index oder
Knowledge-Graph einfgespeist. Kontoauszug-PDFs werden via Docling +
strukturiertem LLM-Output deterministisch in normalisierte Buchungs-Records
extrahiert; Bank- und Konto-Identifikation erfolgt ber IBAN-Eindeutigkeit
(ISO 13616), nicht ber Pattern-Heuristiken auf Bank-Namen.

Architektur:
    finance.db_schema    -- Schema, DAO, Migrations
    finance.models       -- Pydantic-Schemata für LLM-strukturierte Extraktion
    finance.extractor    -- Docling + LLMStructuredWrapper Pipeline
    finance.tools        -- Orchestrator-Tool-Block für Q&A im Chat
    finance.tab          -- Streamlit-UI (Import/Konten/Auswertungen/Q&A)
"""

__all__ = [
    "FinanceDB",
    "Bank",
    "Account",
    "Statement",
    "Transaction",
    "Category",
    "CounterpartyRule",
    "Budget",
    "TransferLink",
    "ExtractedStatement",
    "ExtractedTransaction",
    "CategorySuggestion",
    "CategorySuggestions",
    "FinanceExtractor",
    "FinanceCategorizer",
    "FinanceTools",
]


def __getattr__(name):  # lazy attribute import to avoid heavy deps at module load
    if name == "FinanceDB":
        from finance.db_schema import FinanceDB
        return FinanceDB
    if name in {"Bank", "Account", "Statement", "Transaction", "Category", "CounterpartyRule", "Budget", "TransferLink"}:
        from finance import db_schema as _ds
        return getattr(_ds, name)
    if name in {"ExtractedStatement", "ExtractedTransaction", "CategorySuggestion", "CategorySuggestions"}:
        from finance import models as _m
        return getattr(_m, name)
    if name == "FinanceExtractor":
        from finance.extractor import FinanceExtractor
        return FinanceExtractor
    if name == "FinanceCategorizer":
        from finance.categorizer import FinanceCategorizer
        return FinanceCategorizer
    if name == "FinanceTools":
        from finance.tools import FinanceTools
        return FinanceTools
    raise AttributeError(f"module 'finance' has no attribute {name!r}")
