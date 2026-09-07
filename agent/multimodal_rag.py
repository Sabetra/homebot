"""
P4-2: MULTI-MODAL RAG – RAG Quality Pipeline Component

Extended chunking for tables, diagrams, formulas and figures.
Enables indexing of structured content beyond plain text.

SOTA Features:
- Table-aware chunking with structured metadata
- Figure/diagram description indexing
- Formula extraction and indexing
- Cross-modal reference linking
- Content-type-aware retrieval

Author: SOTA RAG Quality Pipeline
Date: 2026-06-24
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ====================================================================
# CONTENT TYPES
# ====================================================================

class ContentType(Enum):
    TEXT = "text"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"
    HEADER = "header"
    CODE = "code"
    MIXED = "mixed"

# ====================================================================
# DATA MODELS
# ====================================================================

@dataclass
class TableStructure:
    """Structured table data."""
    table_id: str
    columns: List[str]
    rows: List[List[str]]
    source_file: str = ""
    source_page: Optional[int] = None
    caption: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def markdown_export(self) -> str:
        """Export table as markdown."""
        if not self.columns:
            return ""

        lines = []
        # Header
        lines.append("| " + " | ".join(self.columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(self.columns)) + " |")
        # Rows
        for row in self.rows:
            padded_row = row + [""] * (len(self.columns) - len(row))
            lines.append("| " + " | ".join(padded_row[:len(self.columns)]) + " |")
        return "\n".join(lines)

    @property
    def natural_language(self) -> str:
        """Convert table to natural language description."""
        if not self.rows:
            return f"Table: {self.caption or self.table_id}"

        sentences = []
        for row in self.rows:
            parts = []
            for col, val in zip(self.columns, row):
                if val and val.strip():
                    parts.append(f"{col}: {val}")
            if parts:
                sentences.append(", ".join(parts))
        return f"Table '{self.caption or self.table_id}': " + ". ".join(sentences)


@dataclass
class FigureDescription:
    """Description of a figure/diagram."""
    figure_id: str
    caption: str
    description: str
    alt_text: str = ""
    source_file: str = ""
    source_page: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def index_content(self) -> str:
        """Content to index for this figure."""
        parts = [self.caption, self.description, self.alt_text]
        return " ".join(p for p in parts if p)


@dataclass
class FormulaBlock:
    """Mathematical formula."""
    formula_id: str
    latex: str
    plain_text: str
    description: str = ""
    source_file: str = ""
    source_page: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def index_content(self) -> str:
        """Content to index for this formula."""
        parts = []
        if self.description:
            parts.append(self.description)
        parts.append(self.plain_text if self.plain_text else self.latex)
        return " ".join(parts)


@dataclass
class MultiModalChunk:
    """A chunk that can contain multiple content types."""
    chunk_id: str
    source_file: str
    primary_content: str
    content_type: ContentType
    page_number: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    hash_sha256: str = ""
    sub_chunks: List[Dict[str, Any]] = field(default_factory=list)
    cross_references: List[str] = field(default_factory=list)

    def to_vector_payload(self) -> Dict[str, Any]:
        """Prepare payload for vector indexing."""
        return {
            "chunk_id": self.chunk_id,
            "content": self.primary_content,
            "content_type": self.content_type.value,
            "source": self.source_file,
            "page": self.page_number,
            "metadata": self.metadata,
            "hash": self.hash_sha256,
            "cross_refs": self.cross_references,
        }

# ====================================================================
# MULTI-MODAL CHUNKER
# ====================================================================

class MultiModalChunker:
    """
    SOTA chunker for multi-modal content.

    Handles:
    - Text with sentence-boundary splitting
    - Tables as structured data + natural language
    - Figures with descriptions
    - Formulas in LaTeX and plain text
    - Mixed content with cross-references
    """

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200,
                 language: str = "de"):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.language = language
        self._chunk_counter = 0

    def _next_chunk_id(self, source: str) -> str:
        self._chunk_counter += 1
        return f"mm_{Path(source).stem}_{self._chunk_counter}"

    # ----------------------------------------------------------------
    # Text Chunking
    # ----------------------------------------------------------------

    def chunk_text(self, text: str, source: str, page: Optional[int] = None) -> List[MultiModalChunk]:
        """Chunk plain text with sentence-boundary splitting."""
        if not text or not text.strip():
            return []

        text = text.strip()

        # If text fits in one chunk, return it directly
        if len(text) <= self.chunk_size:
            content_hash = hashlib.sha256(text.encode()).hexdigest()
            return [MultiModalChunk(
                chunk_id=self._next_chunk_id(source),
                source_file=source,
                primary_content=text,
                content_type=ContentType.TEXT,
                page_number=page,
                hash_sha256=content_hash,
            )]

        # Split into smaller chunks
        return self._split_large_text(text, source, page)

    def _create_text_chunk(self, text: str, source: str, page: Optional[int]) -> MultiModalChunk:
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        return MultiModalChunk(
            chunk_id=self._next_chunk_id(source),
            source_file=source,
            primary_content=text,
            content_type=ContentType.TEXT,
            page_number=page,
            hash_sha256=content_hash,
        )

    def _split_large_text(self, text: str, source: str, page: Optional[int]) -> List[MultiModalChunk]:
        """Split large text using sentence boundaries with overlap."""
        sentences = self._split_sentences(text)
        if not sentences:
            return []

        chunks: List[MultiModalChunk] = []
        current_sentences: List[str] = []
        current_length = 0

        for sentence in sentences:
            sentence_len = len(sentence)

            # If adding this sentence exceeds chunk size
            if current_length + sentence_len > self.chunk_size and current_sentences:
                # Finalize current chunk
                chunk_text = " ".join(current_sentences)
                chunks.append(self._create_text_chunk(chunk_text, source, page))

                # Start new chunk with overlap
                overlap_count = max(1, len(current_sentences) // 4)
                current_sentences = current_sentences[-overlap_count:]
                current_length = sum(len(s) for s in current_sentences)

            current_sentences.append(sentence)
            current_length += sentence_len

        # Final chunk
        if current_sentences:
            chunk_text = " ".join(current_sentences)
            chunks.append(self._create_text_chunk(chunk_text, source, page))

        return chunks

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences respecting abbreviations."""
        import re
        # Simple but effective: split on sentence-ending punctuation
        # followed by space and uppercase letter
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z\xC0-\xD6\xD8-\xDE])', text)
        return [s.strip() for s in sentences if s.strip()]

    # ----------------------------------------------------------------
    # Table Chunking
    # ----------------------------------------------------------------

    def chunk_table(self, table: TableStructure) -> List[MultiModalChunk]:
        """Create chunks from a structured table."""
        if not table.rows:
            return []

        chunks: List[MultiModalChunk] = []

        # Main chunk: Natural language description
        nl_content = table.natural_language
        main_chunk = MultiModalChunk(
            chunk_id=self._next_chunk_id(table.source_file or table.table_id),
            source_file=table.source_file or table.table_id,
            primary_content=nl_content,
            content_type=ContentType.TABLE,
            page_number=table.source_page,
            metadata={
                "table_id": table.table_id,
                "row_count": len(table.rows),
                "column_count": len(table.columns),
                "columns": table.columns,
                "caption": table.caption,
            },
            hash_sha256=hashlib.sha256(nl_content.encode()).hexdigest(),
        )

        # Sub-chunks: Individual rows as queryable units
        for row_idx, row in enumerate(table.rows):
            row_text = self._row_to_sentence(row, table.columns)
            if row_text:
                main_chunk.sub_chunks.append({
                    "type": "table_row",
                    "row_index": row_idx,
                    "content": row_text,
                })

        chunks.append(main_chunk)

        # Also create a markdown chunk for structured retrieval
        if table.markdown_export:
            chunks.append(MultiModalChunk(
                chunk_id=self._next_chunk_id(table.source_file or table.table_id),
                source_file=table.source_file or table.table_id,
                primary_content=table.markdown_export,
                content_type=ContentType.TABLE,
                page_number=table.source_page,
                metadata={"table_id": table.table_id, "format": "markdown"},
                hash_sha256=hashlib.sha256(table.markdown_export.encode()).hexdigest(),
            ))

        return chunks

    def _row_to_sentence(self, row: List[str], columns: List[str]) -> str:
        """Convert a row to a natural language sentence."""
        parts = []
        for col, val in zip(columns, row):
            if val and val.strip():
                parts.append(f"{col}: {val}")
        return "; ".join(parts) if parts else ""

    # ----------------------------------------------------------------
    # Figure Chunking
    # ----------------------------------------------------------------

    def chunk_figure(self, figure: FigureDescription) -> MultiModalChunk:
        """Create a chunk for a figure/diagram."""
        content = figure.index_content
        return MultiModalChunk(
            chunk_id=self._next_chunk_id(figure.source_file or figure.figure_id),
            source_file=figure.source_file or figure.figure_id,
            primary_content=content,
            content_type=ContentType.FIGURE,
            page_number=figure.source_page,
            metadata={
                "figure_id": figure.figure_id,
                "caption": figure.caption,
                "description": figure.description,
            },
            hash_sha256=hashlib.sha256(content.encode()).hexdigest(),
        )

    # ----------------------------------------------------------------
    # Formula Chunking
    # ----------------------------------------------------------------

    def chunk_formula(self, formula: FormulaBlock) -> MultiModalChunk:
        """Create a chunk for a mathematical formula."""
        content = formula.index_content
        return MultiModalChunk(
            chunk_id=self._next_chunk_id(formula.source_file or formula.formula_id),
            source_file=formula.source_file or formula.formula_id,
            primary_content=content,
            content_type=ContentType.FORMULA,
            page_number=formula.source_page,
            metadata={
                "formula_id": formula.formula_id,
                "latex": formula.latex,
                "description": formula.description,
            },
            hash_sha256=hashlib.sha256(content.encode()).hexdigest(),
        )

    # ----------------------------------------------------------------
    # Mixed Content Chunking
    # ----------------------------------------------------------------

    def chunk_mixed_content(self, sections: List[Dict[str, Any]], source: str) -> List[MultiModalChunk]:
        """Process mixed content (text + tables + figures + formulas)."""
        chunks: List[MultiModalChunk] = []

        for section in sections:
            section_type = section.get("type", "text")
            page = section.get("page")

            if section_type == "text":
                text = section.get("content", "")
                chunks.extend(self.chunk_text(text, source, page))

            elif section_type == "table":
                table_data = section.get("table")
                if table_data:
                    # Convert dict to TableStructure if needed
                    if isinstance(table_data, dict):
                        table = TableStructure(**table_data)
                    else:
                        table = table_data
                    table.source_file = source
                    chunks.extend(self.chunk_table(table))

            elif section_type == "figure":
                figure_data = section.get("figure")
                if figure_data:
                    if isinstance(figure_data, dict):
                        figure = FigureDescription(**figure_data)
                    else:
                        figure = figure_data
                    figure.source_file = source
                    chunks.append(self.chunk_figure(figure))

            elif section_type == "formula":
                formula_data = section.get("formula")
                if formula_data:
                    if isinstance(formula_data, dict):
                        formula = FormulaBlock(**formula_data)
                    else:
                        formula = formula_data
                    formula.source_file = source
                    chunks.append(self.chunk_formula(formula))

        # Link cross-references between chunks on same page
        self._link_cross_references(chunks)

        return chunks

    def _link_cross_references(self, chunks: List[MultiModalChunk]):
        """Create cross-references between chunks from the same page."""
        page_chunks: Dict[int, List[MultiModalChunk]] = {}

        for chunk in chunks:
            if chunk.page_number is not None:
                page_chunks.setdefault(chunk.page_number, []).append(chunk)

        for page, page_chunk_list in page_chunks.items():
            if len(page_chunk_list) > 1:
                ref_ids = [c.chunk_id for c in page_chunk_list]
                for chunk in page_chunk_list:
                    chunk.cross_references = [rid for rid in ref_ids if rid != chunk.chunk_id]

# ====================================================================
# MULTI-MODAL RAG INDEX
# ====================================================================

class MultiModalRAGIndex:
    """
    Index for multi-modal chunks with content-type-aware retrieval.

    Provides:
    - Content-type filtering
    - Cross-modal query expansion
    - Source and page tracking
    - Hash-based deduplication
    """

    def __init__(self):
        self._index: Dict[str, MultiModalChunk] = {}
        self._type_index: Dict[str, List[str]] = {}
        self._source_index: Dict[str, List[str]] = {}
        self._page_index: Dict[int, List[str]] = {}
        self._hash_index: Dict[str, str] = {}  # hash -> chunk_id

    # ----------------------------------------------------------------
    # Indexing
    # ----------------------------------------------------------------

    def add_chunk(self, chunk: MultiModalChunk) -> bool:
        """Add a chunk. Returns False if duplicate."""
        if chunk.chunk_id in self._index:
            return False

        self._index[chunk.chunk_id] = chunk

        # Type index
        type_key = chunk.content_type.value
        self._type_index.setdefault(type_key, []).append(chunk.chunk_id)

        # Source index
        source_key = chunk.source_file
        self._source_index.setdefault(source_key, []).append(chunk.chunk_id)

        # Page index
        if chunk.page_number is not None:
            self._page_index.setdefault(chunk.page_number, []).append(chunk.chunk_id)

        # Hash index (for deduplication)
        if chunk.hash_sha256:
            self._hash_index[chunk.hash_sha256] = chunk.chunk_id

        return True

    def add_chunks(self, chunks: List[MultiModalChunk]) -> int:
        """Add multiple chunks. Returns count of new chunks."""
        added = 0
        for chunk in chunks:
            if self.add_chunk(chunk):
                added += 1
        return added

    def remove_by_source(self, source: str) -> int:
        """Remove all chunks from a source. Returns count removed."""
        chunk_ids = self._source_index.get(source, [])
        for cid in chunk_ids:
            chunk = self._index.pop(cid, None)
            if chunk:
                # Remove from type index
                type_key = chunk.content_type.value
                if type_key in self._type_index:
                    self._type_index[type_key] = [
                        c for c in self._type_index[type_key] if c != cid
                    ]
                # Remove from page index
                if chunk.page_number is not None:
                    page_key = chunk.page_number
                    if page_key in self._page_index:
                        self._page_index[page_key] = [
                            c for c in self._page_index[page_key] if c != cid
                        ]
                # Remove from hash index
                if chunk.hash_sha256:
                    self._hash_index.pop(chunk.hash_sha256, None)
        # Clean source index
        self._source_index.pop(source, None)
        return len(chunk_ids)

    # ----------------------------------------------------------------
    # Retrieval
    # ----------------------------------------------------------------

    def get_chunk(self, chunk_id: str) -> Optional[MultiModalChunk]:
        """Get a chunk by ID."""
        return self._index.get(chunk_id)

    def get_by_source(self, source: str) -> List[MultiModalChunk]:
        """Get all chunks from a source."""
        return [self._index[cid] for cid in self._source_index.get(source, [])
                if cid in self._index]

    def get_by_type(self, content_type: ContentType) -> List[MultiModalChunk]:
        """Get all chunks of a specific type."""
        return [self._index[cid] for cid in self._type_index.get(content_type.value, [])
                if cid in self._index]

    def get_by_page(self, page: int) -> List[MultiModalChunk]:
        """Get all chunks from a specific page."""
        return [self._index[cid] for cid in self._page_index.get(page, [])
                if cid in self._index]

    def get_with_sub_chunks(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        """Get a chunk with its sub-chunks expanded."""
        chunk = self._index.get(chunk_id)
        if not chunk:
            return None

        result = chunk.to_vector_payload()
        result["sub_chunks"] = chunk.sub_chunks
        return result

    # ----------------------------------------------------------------
    # Query Expansion
    # ----------------------------------------------------------------

    def expand_query(self, query: str, include_types: Optional[List[ContentType]] = None) -> str:
        """
        Expand a query to include cross-modal context.

        For example, if a query mentions a table, also search for
        related text on the same page.
        """
        # Simple expansion: add content-type hints
        expansions = []

        # Check if query mentions table-related terms
        table_terms = ["tabelle", "table", "daten", "data", "zahlen", "numbers", "werte", "values"]
        if any(term in query.lower() for term in table_terms):
            expansions.append("tabellarische Daten")

        # Check if query mentions figure-related terms
        figure_terms = ["abbildung", "figure", "diagramm", "diagram", "grafik", "chart", "bild", "image"]
        if any(term in query.lower() for term in figure_terms):
            expansions.append("Bildbeschreibung")

        # Check if query mentions formula-related terms
        formula_terms = ["formel", "formula", "gleichung", "equation", "berechnung", "calculation"]
        if any(term in query.lower() for term in formula_terms):
            expansions.append("mathematische Formel")

        if expansions:
            return query + " " + " ".join(expansions)
        return query

    # ----------------------------------------------------------------
    # Statistics
    # ----------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        type_counts: Dict[str, int] = {}
        for type_key, cids in self._type_index.items():
            type_counts[type_key] = len([c for c in cids if c in self._index])

        source_count = len([s for s, cids in self._source_index.items()
                          if any(c in self._index for c in cids)])

        return {
            "total_chunks": len(self._index),
            "by_type": type_counts,
            "sources": source_count,
            "pages_indexed": len(self._page_index),
        }

    def clear(self):
        """Clear the entire index."""
        self._index.clear()
        self._type_index.clear()
        self._source_index.clear()
        self._page_index.clear()
        self._hash_index.clear()


class MultiModalRAG(MultiModalRAGIndex):
    """Compatibility wrapper used by the SOTA pipeline."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200,
                 include_tables: bool = True, include_diagrams: bool = True,
                 include_formulas: bool = True, language: str = "de"):
        super().__init__()
        self.chunker = MultiModalChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            language=language,
        )
        self.include_tables = include_tables
        self.include_diagrams = include_diagrams
        self.include_formulas = include_formulas

    def chunk_document(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Chunk a document into pipeline-friendly dictionaries."""
        if not content:
            return []

        metadata = metadata or {}
        source = metadata.get("source_path") or metadata.get("file_path") or metadata.get("source_file") or metadata.get("doc_id") or "document"
        page = metadata.get("page") or metadata.get("page_number")

        chunks = self.chunker.chunk_text(content, source=str(source), page=page)
        return [chunk.to_vector_payload() for chunk in chunks]


# Backwards-compatible aliases used across the codebase.
MultimodalRAG = MultiModalRAG

# ====================================================================
# MODULE ENTRY POINT
# ====================================================================

def create_chunker(chunk_size: int = 1000, chunk_overlap: int = 200) -> MultiModalChunker:
    """Create a MultiModalChunker with default settings."""
    return MultiModalChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)


def create_index() -> MultiModalRAGIndex:
    """Create an empty MultiModalRAGIndex."""
    return MultiModalRAGIndex()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    chunker = create_chunker()
    index = create_index()

    # Test with sample mixed content
    sections = [
        {
            "type": "text",
            "content": "Das Quartalsergebnis zeigt eine signifikante Verbesserung. "
                      "Der Umsatz stieg um 15 Prozent gegenüber dem Vorjahr. "
                      "Die Kosten wurden um 5 Prozent reduziert.",
            "page": 1,
        },
        {
            "type": "table",
            "table": {
                "table_id": "quarterly_results",
                "columns": ["Quartal", "Umsatz", "Kosten", "Gewinn"],
                "rows": [
                    ["Q1", "1.2M", "0.8M", "0.4M"],
                    ["Q2", "1.4M", "0.75M", "0.65M"],
                    ["Q3", "1.5M", "0.7M", "0.8M"],
                ],
                "caption": "Quartalszahlen 2026",
            },
            "page": 1,
        },
        {
            "type": "figure",
            "figure": {
                "figure_id": "revenue_chart",
                "caption": "Umsatzentwicklung",
                "description": "Liniendiagramm zeigt steigenden Trend von Q1 zu Q3",
            },
            "page": 2,
        },
    ]

    chunks = chunker.chunk_mixed_content(sections, source="test_report.pdf")
    added = index.add_chunks(chunks)

    print(f"Created {len(chunks)} chunks, added {added} to index")
    print(f"Index stats: {index.stats()}")

    for chunk in chunks:
        print(f"  [{chunk.content_type.value}] {chunk.chunk_id}: {chunk.primary_content[:80]}...")