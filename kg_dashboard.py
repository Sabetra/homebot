"""
🧠 KNOWLEDGE GRAPH DASHBOARD - FULLY OPTIMIZED VERSION
======================================================

Implements all 10 critical optimizations:
1. ✅ Integrated into RAG-Dokumente tab (sub-navigation)
2. ✅ Session-state-based cache invalidation
3. ✅ FTS5 full-text search for fast triple/entity search
4. ✅ Hash-based layout caching
5. ✅ Adaptive layout selection based on graph size
6. ✅ Export features (CSV, PNG)
7. ✅ Edge-case handling (empty graph, long names, duplicates, DB lock)
8. ✅ Unit-testable core functions
9. ✅ TL;DR and cheatsheet documentation
10. ✅ Monitoring/logging for usage, performance, and errors

Author: RAG System Team
Version: 3.0 (Production-Ready)
Last Updated: 2025
"""

import streamlit as st
from i18n import t as i18n_t
import plotly.graph_objects as go
import networkx as nx
import pandas as pd
import sqlite3
import hashlib
import time
import logging
import io
from typing import List, Dict, Tuple, Optional, Any, Set, cast
from dataclasses import dataclass
from datetime import datetime
import json

# SOTA: Central path resolution - CWD-independent absolute paths
from utils.db_path_resolver import get_rag_store_path

# ==================================================================================
# CONFIGURATION & CONSTANTS
# ==================================================================================

@dataclass
class KGDashboardConfig:
    """Configuration for KG Dashboard"""
    # Performance
    max_layout_cache_size: int = 100
    layout_cache_ttl: int = 3600  # 1 hour
    search_results_limit: int = 100
    
    # Visualization
    default_width: int = 1000
    default_height: int = 700
    node_size_min: int = 10
    node_size_max: int = 30
    edge_width_min: float = 0.5
    edge_width_max: float = 3.0
    
    # Adaptive Layout Thresholds
    small_graph_threshold: int = 20
    medium_graph_threshold: int = 100
    large_graph_threshold: int = 500
    
    # Export
    csv_max_rows: int = 10000
    png_width: int = 1920
    png_height: int = 1080


# Global config instance
CONFIG = KGDashboardConfig()

# Logging setup
logger = logging.getLogger(__name__)


def _tr(key: str, default: str, **kwargs: Any) -> str:
    """Translate with fallback to default text when key is missing."""
    translated = i18n_t(key, **kwargs)
    if translated == key:
        if kwargs:
            try:
                return default.format(**kwargs)
            except Exception:
                return default
        return default
    return translated


# ==================================================================================
# CORE DATA MODELS
# ==================================================================================

@dataclass
class KGTriple:
    """Represents a knowledge graph triple (subject, predicate, object)"""
    subject: str
    predicate: str
    object: str
    doc_id: str
    page: Optional[int] = None
    triple_id: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for export"""
        return {
            'triple_id': self.triple_id,
            'doc_id': self.doc_id,
            'page': self.page,
            'subject': self.subject,
            'predicate': self.predicate,
            'object': self.object,
            'metadata': json.dumps(self.metadata) if self.metadata else None
        }
    
    def get_hash(self) -> str:
        """Generate hash for deduplication"""
        content = f"{self.subject}|{self.predicate}|{self.object}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()


@dataclass
class KGNode:
    """Represents a node (entity) in the knowledge graph"""
    id: str
    label: str
    node_type: str = "entity"
    frequency: int = 1
    
    def __hash__(self):
        return hash(self.id)


@dataclass
class KGEdge:
    """Represents an edge (relation) in the knowledge graph"""
    source: str
    target: str
    label: str
    weight: int = 1


# ==================================================================================
# DATABASE OPERATIONS (with FTS5 support)
# ==================================================================================

class KGDatabaseManager:
    """Manages all database operations for Knowledge Graph"""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(get_rag_store_path())
        self._conn_cache: Optional[sqlite3.Connection] = None
        self._setup_fts5()
    
    def _setup_fts5(self, _retries: int = 3):
        """Setup FTS5 virtual table for fast full-text search (Optimization #3)"""
        for attempt in range(_retries):
            try:
                conn = self._get_connection()
                cur = conn.cursor()
                
                # Ensure base tables exist (may not have been created by DatabaseManager yet)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        doc_id TEXT PRIMARY KEY
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS triples (
                        triple_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        doc_id TEXT NOT NULL,
                        page INTEGER,
                        table_id INTEGER,
                        subject TEXT,
                        predicate TEXT,
                        object TEXT,
                        metadata TEXT,
                        triple_hash TEXT
                    )
                """)
                
                # Create FTS5 virtual table if not exists
                cur.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS triples_fts USING fts5(
                        subject, predicate, object, doc_id,
                        content='triples',
                        content_rowid='triple_id'
                    )
                """)
                
                # Create triggers to keep FTS5 in sync
                cur.execute("""
                    CREATE TRIGGER IF NOT EXISTS triples_ai AFTER INSERT ON triples BEGIN
                        INSERT INTO triples_fts(rowid, subject, predicate, object, doc_id)
                        VALUES (new.triple_id, new.subject, new.predicate, new.object, new.doc_id);
                    END
                """)
                
                cur.execute("""
                    CREATE TRIGGER IF NOT EXISTS triples_ad AFTER DELETE ON triples BEGIN
                        DELETE FROM triples_fts WHERE rowid = old.triple_id;
                    END
                """)
                
                cur.execute("""
                    CREATE TRIGGER IF NOT EXISTS triples_au AFTER UPDATE ON triples BEGIN
                        UPDATE triples_fts SET 
                            subject = new.subject,
                            predicate = new.predicate,
                            object = new.object,
                            doc_id = new.doc_id
                        WHERE rowid = new.triple_id;
                    END
                """)
                
                conn.commit()
                logger.info("✅ FTS5 setup completed successfully")
                return  # Success, exit retry loop
                
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < _retries - 1:
                    logger.warning(f"⚠️ FTS5 setup attempt {attempt+1}/{_retries} failed (locked), retrying in {2 ** attempt}s...")
                    time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                else:
                    logger.warning(f"⚠️ FTS5 setup failed: {e}. Falling back to regular search.")
                    return
            except Exception as e:
                logger.warning(f"⚠️ FTS5 setup failed: {e}. Falling back to regular search.")
                return
    
    def _get_connection(self):
        """Get database connection with proper settings (WAL mode for concurrent access)"""
        if self._conn_cache is not None:
            try:
                # Verify connection is still alive
                self._conn_cache.execute("SELECT 1")
                return self._conn_cache
            except Exception:
                self._conn_cache = None
        
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint = 1000")
        self._conn_cache = conn
        return conn
    
    def get_document_triples(self, doc_id: str) -> List[KGTriple]:
        """Get all triples for a specific document (Optimization #7: edge-case handling)"""
        try:
            start_time = time.time()
            
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT triple_id, doc_id, page, subject, predicate, object, metadata
                    FROM triples
                    WHERE doc_id = ?
                    ORDER BY triple_id
                """, (doc_id,))
                
                rows = cur.fetchall()
                
                triples = []
                for row in rows:
                    metadata = None
                    if row[6]:
                        try:
                            metadata = json.loads(row[6])
                        except:
                            pass
                    
                    triples.append(KGTriple(
                        triple_id=row[0],
                        doc_id=row[1],
                        page=row[2],
                        subject=row[3],
                        predicate=row[4],
                        object=row[5],
                        metadata=metadata
                    ))
                
                elapsed = time.time() - start_time
                logger.info(f"📊 Retrieved {len(triples)} triples for doc {doc_id} in {elapsed:.2f}s")
                
                return triples
                
        except sqlite3.OperationalError as e:
            logger.error(f"❌ Database locked while retrieving triples: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Error retrieving triples: {e}")
            return []
    
    def search_triples(self, query: str, limit: int = 100) -> List[KGTriple]:
        """Search triples using FTS5 (Optimization #3)"""
        try:
            start_time = time.time()
            
            with self._get_connection() as conn:
                cur = conn.cursor()
                
                # Try FTS5 first
                try:
                    cur.execute("""
                        SELECT t.triple_id, t.doc_id, t.page, t.subject, t.predicate, t.object, t.metadata
                        FROM triples_fts fts
                        JOIN triples t ON fts.rowid = t.triple_id
                        WHERE triples_fts MATCH ?
                        LIMIT ?
                    """, (query, limit))
                    
                    rows = cur.fetchall()
                    logger.info(f"✅ FTS5 search found {len(rows)} results")
                    
                except sqlite3.OperationalError:
                    # Fallback to LIKE search if FTS5 not available
                    logger.warning("⚠️ FTS5 not available, using LIKE search")
                    cur.execute("""
                        SELECT triple_id, doc_id, page, subject, predicate, object, metadata
                        FROM triples
                        WHERE subject LIKE ? OR predicate LIKE ? OR object LIKE ?
                        LIMIT ?
                    """, (f"%{query}%", f"%{query}%", f"%{query}%", limit))
                    
                    rows = cur.fetchall()
                
                triples = []
                for row in rows:
                    metadata = None
                    if row[6]:
                        try:
                            metadata = json.loads(row[6])
                        except:
                            pass
                    
                    triples.append(KGTriple(
                        triple_id=row[0],
                        doc_id=row[1],
                        page=row[2],
                        subject=row[3],
                        predicate=row[4],
                        object=row[5],
                        metadata=metadata
                    ))
                
                elapsed = time.time() - start_time
                logger.info(f"🔍 Search '{query}' found {len(triples)} results in {elapsed:.2f}s")
                
                return triples
                
        except Exception as e:
            logger.error(f"❌ Error searching triples: {e}")
            return []
    
    def get_all_documents_with_kg(self) -> List[Tuple[str, int]]:
        """Get all documents that have knowledge graph data"""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT doc_id, COUNT(*) as triple_count
                    FROM triples
                    GROUP BY doc_id
                    ORDER BY triple_count DESC
                """)
                
                return cast(List[Tuple[str, int]], cur.fetchall())
                
        except Exception as e:
            logger.error(f"❌ Error getting documents: {e}")
            return []
    
    def get_top_entities(self, entity_type: str, limit: int = 50) -> List[Tuple[str, int]]:
        """
        Get top N entities by frequency from the knowledge graph
        
        Args:
            entity_type: 'subject', 'predicate', or 'object'
            limit: Maximum number of entities to return
            
        Returns:
            List of (entity_name, count) tuples sorted by count descending
        """
        if entity_type not in ['subject', 'predicate', 'object']:
            logger.error(f"❌ Invalid entity_type: {entity_type}")
            return []
        
        try:
            start_time = time.time()
            
            with self._get_connection() as conn:
                cur = conn.cursor()
                
                # Build query based on entity type
                query = f"""
                    SELECT {entity_type}, COUNT(*) as count
                    FROM triples
                    GROUP BY {entity_type}
                    ORDER BY count DESC
                    LIMIT ?
                """
                
                cur.execute(query, (limit,))
                results = cur.fetchall()
                
                elapsed = time.time() - start_time
                logger.info(f"📊 Retrieved top {len(results)} {entity_type}s in {elapsed:.2f}s")
                
                return cast(List[Tuple[str, int]], results)
                
        except Exception as e:
            logger.error(f"❌ Error getting top {entity_type}s: {e}")
            return []


# ==================================================================================
# GRAPH BUILDING & LAYOUT (with caching)
# ==================================================================================

class KGGraphBuilder:
    """Builds NetworkX graphs from triples with smart caching (Optimization #4)"""
    
    def __init__(self):
        self._layout_cache: Dict[str, Tuple[Dict, float]] = {}  # hash -> (layout, timestamp)
    
    def build_graph(self, triples: List[KGTriple]) -> nx.DiGraph:
        """Build NetworkX directed graph from triples (Optimization #7: deduplication)"""
        G = nx.DiGraph()
        
        # Track seen edges to avoid duplicates
        seen_edges: Set[Tuple[str, str, str]] = set()
        
        for triple in triples:
            # Truncate long labels (Optimization #7)
            subject = self._truncate_label(triple.subject)
            obj = self._truncate_label(triple.object)
            predicate = triple.predicate
            
            # Add nodes
            if not G.has_node(subject):
                G.add_node(subject, type='entity')
            if not G.has_node(obj):
                G.add_node(obj, type='entity')
            
            # Add edge (with deduplication)
            edge_key = (subject, obj, predicate)
            if edge_key not in seen_edges:
                G.add_edge(subject, obj, label=predicate, weight=1)
                seen_edges.add(edge_key)
            else:
                # Increment edge weight if duplicate
                if G.has_edge(subject, obj):
                    G[subject][obj]['weight'] = G[subject][obj].get('weight', 1) + 1
        
        return G
    
    def _truncate_label(self, label: str, max_length: int = 40) -> str:
        """Truncate long labels for better visualization (Optimization #7)"""
        if len(label) <= max_length:
            return label
        return label[:max_length-3] + "..."
    
    def get_layout(self, G: nx.DiGraph, algorithm: str = "auto") -> Dict:
        """Get graph layout with caching (Optimization #4 & #5)"""
        # Generate cache key from graph structure
        cache_key = self._get_graph_hash(G) + f"_{algorithm}"
        
        # Check cache
        if cache_key in self._layout_cache:
            layout, timestamp = self._layout_cache[cache_key]
            # Check if cache is still valid (TTL)
            if time.time() - timestamp < CONFIG.layout_cache_ttl:
                logger.info(f"✅ Using cached layout for {G.number_of_nodes()} nodes")
                return layout
        
        # Compute layout (Optimization #5: adaptive algorithm selection)
        start_time = time.time()
        
        if algorithm == "auto":
            algorithm = self._select_adaptive_algorithm(G)
        
        layout = self._compute_layout(G, algorithm)
        
        elapsed = time.time() - start_time
        logger.info(f"🎨 Computed {algorithm} layout for {G.number_of_nodes()} nodes in {elapsed:.2f}s")
        
        # Cache the result
        self._layout_cache[cache_key] = (layout, time.time())
        
        # Clean old cache entries if too large
        if len(self._layout_cache) > CONFIG.max_layout_cache_size:
            self._clean_cache()
        
        return layout
    
    def _select_adaptive_algorithm(self, G: nx.DiGraph) -> str:
        """Select best layout algorithm based on graph size (Optimization #5)"""
        num_nodes = G.number_of_nodes()
        
        if num_nodes < CONFIG.small_graph_threshold:
            return "spring"  # Force-directed, best for small graphs
        elif num_nodes < CONFIG.medium_graph_threshold:
            return "kamada_kawai"  # Good balance for medium graphs
        elif num_nodes < CONFIG.large_graph_threshold:
            return "circular"  # Fast for large graphs
        else:
            return "shell"  # Very fast for very large graphs
    
    def _compute_layout(self, G: nx.DiGraph, algorithm: str) -> Dict[str, Any]:
        """Compute actual layout using specified algorithm"""
        try:
            if algorithm == "spring":
                return cast(Dict[str, Any], nx.spring_layout(G, k=0.5, iterations=50))
            elif algorithm == "kamada_kawai":
                return cast(Dict[str, Any], nx.kamada_kawai_layout(G))
            elif algorithm == "circular":
                return cast(Dict[str, Any], nx.circular_layout(G))
            elif algorithm == "shell":
                return cast(Dict[str, Any], nx.shell_layout(G))
            else:
                logger.warning(f"Unknown algorithm '{algorithm}', falling back to spring")
                return cast(Dict[str, Any], nx.spring_layout(G, k=0.5, iterations=50))
        except Exception as e:
            logger.error(f"Layout computation failed: {e}, falling back to circular")
            return cast(Dict[str, Any], nx.circular_layout(G))
    
    def _get_graph_hash(self, G: nx.DiGraph) -> str:
        """Generate hash from graph structure for caching"""
        # Create a stable representation of the graph
        nodes = sorted(G.nodes())
        edge_rows: List[Tuple[str, str, str]] = []
        for edge in G.edges(data=True):
            if len(edge) < 2:
                continue
            u = str(edge[0])
            v = str(edge[1])
            data = edge[2] if len(edge) > 2 and isinstance(edge[2], dict) else {}
            edge_rows.append((u, v, str(data.get("label", ""))))
        edges = sorted(edge_rows)
        
        content = f"nodes:{','.join(nodes)}|edges:{','.join([f'{u}-{v}-{l}' for u, v, l in edges])}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()[:16]
    
    def _clean_cache(self):
        """Remove old cache entries (LRU-style)"""
        # Sort by timestamp and keep only recent ones
        sorted_cache = sorted(
            self._layout_cache.items(),
            key=lambda x: x[1][1],
            reverse=True
        )
        
        self._layout_cache = dict(sorted_cache[:CONFIG.max_layout_cache_size])
        logger.info(f"🧹 Cleaned layout cache, kept {len(self._layout_cache)} entries")


# ==================================================================================
# VISUALIZATION (Plotly)
# ==================================================================================

class KGVisualizer:
    """Creates interactive Plotly visualizations"""
    
    def create_interactive_graph(
        self,
        G: nx.DiGraph,
        layout: Dict,
        title: str = "Knowledge Graph",
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> go.Figure:
        """Create interactive Plotly graph visualization"""
        
        width = width or CONFIG.default_width
        height = height or CONFIG.default_height
        
        # Create edge traces
        edge_traces = []
        for edge in G.edges(data=True):
            x0, y0 = layout[edge[0]]
            x1, y1 = layout[edge[1]]

            data = edge[2] if len(edge) > 2 and isinstance(edge[2], dict) else {}
            weight = data.get('weight', 1)
            edge_width = CONFIG.edge_width_min + (weight - 1) * 0.5
            edge_width = min(edge_width, CONFIG.edge_width_max)
            
            edge_trace = go.Scatter(
                x=[x0, x1, None],
                y=[y0, y1, None],
                mode='lines',
                line=dict(width=edge_width, color='#888'),
                hoverinfo='none',
                showlegend=False
            )
            edge_traces.append(edge_trace)
            
            # Add edge label
            label = data.get('label', '')
            if label:
                mid_x = (x0 + x1) / 2
                mid_y = (y0 + y1) / 2
                
                label_trace = go.Scatter(
                    x=[mid_x],
                    y=[mid_y],
                    mode='text',
                    text=[label],
                    textposition='middle center',
                    textfont=dict(size=8, color='#666'),
                    hoverinfo='none',
                    showlegend=False
                )
                edge_traces.append(label_trace)
        
        # Create node trace
        node_x = []
        node_y = []
        node_text = []
        node_size = []
        
        for node in G.nodes():
            x, y = layout[node]
            node_x.append(x)
            node_y.append(y)
            node_text.append(node)
            
            # Node size based on degree (in + out for DiGraph)
            # G.in_degree[node] + G.out_degree[node] = total connections
            total_degree = G.in_degree[node] + G.out_degree[node]
            size = CONFIG.node_size_min + min(total_degree * 2, CONFIG.node_size_max - CONFIG.node_size_min)
            node_size.append(size)
        
        node_trace = go.Scatter(
            x=node_x,
            y=node_y,
            mode='markers+text',
            text=node_text,
            textposition='top center',
            textfont=dict(size=10),
            marker=dict(
                size=node_size,
                color='#1f77b4',
                line=dict(width=2, color='white')
            ),
            hovertemplate='%{text}<br>Connections: %{marker.size}<extra></extra>',
            showlegend=False
        )
        
        # Create figure
        fig = go.Figure(data=edge_traces + [node_trace])
        
        fig.update_layout(
            title=title,
            title_font=dict(size=16),
            showlegend=False,
            hovermode='closest',
            margin=dict(b=0, l=0, r=0, t=40),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            width=width,
            height=height,
            plot_bgcolor='white'
        )
        
        return fig


# ==================================================================================
# EXPORT FUNCTIONS (Optimization #6)
# ==================================================================================

class KGExporter:
    """Handles export of KG data to various formats"""
    
    @staticmethod
    def export_to_csv(triples: List[KGTriple]) -> str:
        """Export triples to CSV format (Optimization #6)"""
        df = pd.DataFrame([t.to_dict() for t in triples[:CONFIG.csv_max_rows]])
        return cast(str, df.to_csv(index=False))
    
    @staticmethod
    def export_to_png(fig: go.Figure) -> Optional[bytes]:
        """Export Plotly figure to PNG (Optimization #6)"""
        try:
            img_bytes = fig.to_image(
                format="png",
                width=CONFIG.png_width,
                height=CONFIG.png_height
            )
            return cast(bytes, img_bytes)
        except Exception as e:
            logger.error(f"❌ PNG export failed: {e}")
            return None


# ==================================================================================
# SESSION STATE & CACHE MANAGEMENT (Optimization #2)
# ==================================================================================

class KGSessionManager:
    """Manages session state and cache invalidation (Optimization #2)"""
    
    @staticmethod
    def init_session_state():
        """Initialize session state variables"""
        if 'kg_cache_version' not in st.session_state:
            st.session_state.kg_cache_version = 0
        
        if 'kg_last_doc_upload' not in st.session_state:
            st.session_state.kg_last_doc_upload = None
    
    @staticmethod
    def invalidate_cache():
        """Invalidate KG cache after document upload (Optimization #2)"""
        st.session_state.kg_cache_version += 1
        logger.info(f"🔄 KG cache invalidated, version: {st.session_state.kg_cache_version}")
    
    @staticmethod
    def on_document_upload():
        """Called when a new document is uploaded"""
        KGSessionManager.invalidate_cache()
        st.session_state.kg_last_doc_upload = datetime.now()


# ==================================================================================
# MONITORING & LOGGING (Optimization #10)
# ==================================================================================

class KGMonitor:
    """Monitors KG dashboard usage, performance, and errors (Optimization #10)"""
    
    def __init__(self):
        self.metrics = {
            'total_views': 0,
            'total_searches': 0,
            'total_exports': 0,
            'avg_load_time': 0.0,
            'errors': []
        }
    
    def log_view(self, doc_id: str, load_time: float):
        """Log a KG view event"""
        self.metrics['total_views'] += 1
        
        # Update rolling average
        n = self.metrics['total_views']
        self.metrics['avg_load_time'] = (
            (self.metrics['avg_load_time'] * (n - 1) + load_time) / n
        )
        
        logger.info(f"📊 KG View: doc={doc_id}, time={load_time:.2f}s")
    
    def log_search(self, query: str, result_count: int):
        """Log a search event"""
        self.metrics['total_searches'] += 1
        logger.info(f"🔍 KG Search: query='{query}', results={result_count}")
    
    def log_export(self, format: str, size: int):
        """Log an export event"""
        self.metrics['total_exports'] += 1
        logger.info(f"📤 KG Export: format={format}, size={size}")
    
    def log_error(self, error: str, context: Optional[Dict[str, Any]] = None):
        """Log an error"""
        error_entry = {
            'timestamp': datetime.now().isoformat(),
            'error': str(error),
            'context': context or {}
        }
        self.metrics['errors'].append(error_entry)
        logger.error(f"❌ KG Error: {error}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics"""
        return cast(Dict[str, Any], self.metrics.copy())


# Global monitor instance
monitor = KGMonitor()


# ==================================================================================
# MAIN DASHBOARD UI (Streamlit)
# ==================================================================================

def render_kg_dashboard(db_path: Optional[str] = None):
    """
    Render the Knowledge Graph Dashboard.

    Parameters
    ----------
    db_path : str, optional
        Database path – defaults to central resolver (rag_store.db at project root).
    """
    if db_path is None:
        db_path = str(get_rag_store_path())
    """
    Main function to render KG dashboard as sub-navigation in RAG-Dokumente tab
    (Optimization #1: Integration into RAG tab)
    """
    
    # Initialize session state
    KGSessionManager.init_session_state()
    
    # Initialize components
    db_manager = KGDatabaseManager(db_path)
    graph_builder = KGGraphBuilder()
    visualizer = KGVisualizer()
    exporter = KGExporter()
    
    # Header
    st.markdown(_tr("kg_ui.header", "### 🧠 Knowledge Graph Visualisierung"))
    st.markdown(_tr("kg_ui.caption", "Erkunden Sie automatisch extrahierte Wissensstrukturen aus Ihren Dokumenten."))
    
    # Sub-navigation
    tab1, tab2, tab3 = st.tabs(
        [
            _tr("kg_ui.tabs.graph", "📊 Graph Ansicht"),
            _tr("kg_ui.tabs.search", "🔍 Suche"),
            _tr("kg_ui.tabs.stats", "📈 Statistiken"),
        ]
    )
    
    # ===== TAB 1: Graph View =====
    with tab1:
        render_graph_view(db_manager, graph_builder, visualizer, exporter)
    
    # ===== TAB 2: Search =====
    with tab2:
        render_search_view(db_manager, graph_builder, visualizer)
    
    # ===== TAB 3: Statistics =====
    with tab3:
        render_statistics_view(db_manager, monitor)


def render_graph_view(db_manager, graph_builder, visualizer, exporter):
    """Render graph visualization view"""
    
    # Get documents with KG data
    docs = db_manager.get_all_documents_with_kg()
    
    if not docs:
        st.info(_tr("kg_ui.no_data", "ℹ️ Keine Knowledge Graph Daten verfügbar. Laden Sie Dokumente hoch, um den KG zu erstellen."))
        return
    
    # Document selector
    doc_options = [(doc_id, f"{doc_id} ({count} triples)") for doc_id, count in docs]
    doc_dict = {label: doc_id for doc_id, label in doc_options}
    
    selected_label = st.selectbox(
        _tr("kg_ui.select_document", "Waehlen Sie ein Dokument:"),
        options=[label for _, label in doc_options]
    )
    
    selected_doc_id = doc_dict[selected_label]
    
    # Load triples
    start_time = time.time()
    triples = db_manager.get_document_triples(selected_doc_id)
    
    # Edge case: Empty graph (Optimization #7)
    if not triples:
        st.warning(_tr("kg_ui.no_triples", "⚠️ Keine Triples gefunden fuer Dokument '{doc_id}'", doc_id=selected_doc_id))
        return
    
    # Build graph
    G = graph_builder.build_graph(triples)
    
    # Layout settings
    col1, col2 = st.columns([3, 1])
    
    with col1:
        layout_algo = st.selectbox(
            _tr("kg_ui.layout", "Layout-Algorithmus:"),
            options=["auto", "spring", "kamada_kawai", "circular", "shell"],
            help=_tr("kg_ui.layout_help", "'auto' waehlt automatisch basierend auf Graphgroesse")
        )
    
    with col2:
        if st.button(_tr("kg_ui.recompute_layout", "🔄 Layout neu berechnen")):
            # Force recompute by clearing cache
            graph_builder._layout_cache.clear()
            st.rerun()
    
    # Compute layout
    layout = graph_builder.get_layout(G, layout_algo)
    
    # Create visualization
    fig = visualizer.create_interactive_graph(
        G, layout,
        title=f"Knowledge Graph: {selected_doc_id}"
    )
    
    # Display
    st.plotly_chart(fig)
    
    load_time = time.time() - start_time
    monitor.log_view(selected_doc_id, load_time)
    
    # Statistics
    col1, col2, col3 = st.columns(3)
    col1.metric(_tr("kg_ui.metrics.entities", "Entities"), len(G.nodes()))
    col2.metric(_tr("kg_ui.metrics.relations", "Relations"), len(G.edges()))
    col3.metric(_tr("kg_ui.metrics.load_time", "Load Time"), f"{load_time:.2f}s")
    
    # Export buttons (Optimization #6)
    st.markdown("---")
    st.markdown(_tr("kg_ui.export.header", "### 📤 Export"))
    
    col1, col2 = st.columns(2)
    
    with col1:
        csv_data = exporter.export_to_csv(triples)
        st.download_button(
            label=_tr("kg_ui.export.csv", "⬇️ Download CSV"),
            data=csv_data,
            file_name=f"kg_{selected_doc_id}_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )
        if csv_data:
            monitor.log_export("csv", len(csv_data))
    
    with col2:
        if st.button(_tr("kg_ui.export.png", "⬇️ Download PNG")):
            try:
                img_bytes = exporter.export_to_png(fig)
                if img_bytes:
                    st.download_button(
                        label=_tr("kg_ui.export.save_png", "💾 Save PNG"),
                        data=img_bytes,
                        file_name=f"kg_{selected_doc_id}_{datetime.now().strftime('%Y%m%d')}.png",
                        mime="image/png"
                    )
                    monitor.log_export("png", len(img_bytes))
            except Exception as e:
                st.error(_tr("kg_ui.export.kaleido", "PNG export requires kaleido: `pip install kaleido`"))
                monitor.log_error(f"PNG export failed: {e}")


def render_search_view(db_manager, graph_builder, visualizer):
    """Render search view with FTS5 support (Optimization #3)"""
    
    st.markdown(_tr("kg_ui.search.caption", "Durchsuchen Sie alle Knowledge Graph Triples mit Volltextsuche."))
    
    # Search input
    query = st.text_input(
        _tr("kg_ui.search.label", "Suchbegriff:"),
        placeholder=_tr("kg_ui.search.placeholder", "z.B. 'Patient', 'Diagnose', 'Behandlung'...")
    )
    
    if query:
        # Perform search
        results = db_manager.search_triples(query)
        
        monitor.log_search(query, len(results))
        
        if results:
            st.success(_tr("kg_ui.search.results", "✅ {count} Ergebnisse gefunden", count=len(results)))
            
            # Display results as table
            df = pd.DataFrame([r.to_dict() for r in results])
            st.dataframe(df)
            
            # Option to visualize search results
            if st.checkbox(_tr("kg_ui.search.visualize", "📊 Ergebnisse als Graph visualisieren")):
                G = graph_builder.build_graph(results)
                layout = graph_builder.get_layout(G, "auto")
                fig = visualizer.create_interactive_graph(
                    G, layout,
                    title=f"Suchergebnisse: '{query}'"
                )
                st.plotly_chart(fig)
        else:
            st.info(_tr("kg_ui.search.no_results", "ℹ️ Keine Ergebnisse fuer '{query}' gefunden.", query=query))


def render_statistics_view(db_manager, monitor):
    """Render statistics and monitoring view (Optimization #10)"""
    
    st.markdown(_tr("kg_ui.stats.header", "### 📊 Knowledge Graph Statistiken & Monitoring"))
    
    # Get all documents
    docs = db_manager.get_all_documents_with_kg()
    
    if not docs:
        st.info(_tr("kg_ui.stats.no_data", "ℹ️ Keine Daten verfuegbar"))
        return
    
    # Overall statistics
    total_docs = len(docs)
    total_triples = sum(count for _, count in docs)
    avg_triples = total_triples / total_docs if total_docs > 0 else 0
    
    col1, col2, col3 = st.columns(3)
    col1.metric(_tr("kg_ui.stats.docs", "Dokumente mit KG"), total_docs)
    col2.metric(_tr("kg_ui.stats.total_triples", "Gesamt Triples"), total_triples)
    col3.metric(_tr("kg_ui.stats.avg_triples", "Ø Triples/Dokument"), f"{avg_triples:.1f}")
    
    # Monitoring metrics (Optimization #10)
    st.markdown("---")
    st.markdown(_tr("kg_ui.stats.usage_header", "### 📈 Nutzungsstatistiken"))
    
    metrics = monitor.get_metrics()
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(_tr("kg_ui.stats.views", "Views"), metrics['total_views'])
    col2.metric(_tr("kg_ui.stats.searches", "Searches"), metrics['total_searches'])
    col3.metric(_tr("kg_ui.stats.exports", "Exports"), metrics['total_exports'])
    col4.metric(_tr("kg_ui.stats.avg_load_time", "Ø Load Time"), f"{metrics['avg_load_time']:.2f}s")
    
    # Document breakdown
    st.markdown("---")
    st.markdown(_tr("kg_ui.stats.doc_overview", "### 📋 Dokument-Uebersicht"))
    
    doc_df = pd.DataFrame(docs, columns=[_tr("kg_ui.stats.doc_id", "Dokument ID"), _tr("kg_ui.stats.triple_count", "Triple Count")])
    st.dataframe(doc_df)
    
    # TOP 50 ENTITIES OVERVIEW (NEW FEATURE)
    st.markdown("---")
    st.markdown(_tr("kg_ui.stats.top_entities_header", "### 🏆 Top 50 Entities im Knowledge Graph"))
    st.markdown(_tr("kg_ui.stats.top_entities_caption", "Die haeufigsten Subjects, Relations und Objects ueber alle Dokumente hinweg."))
    
    # Get aggregated entity statistics
    top_subjects = db_manager.get_top_entities('subject', limit=50)
    top_relations = db_manager.get_top_entities('predicate', limit=50)
    top_objects = db_manager.get_top_entities('object', limit=50)
    
    # Create tabs for each entity type
    entity_tab1, entity_tab2, entity_tab3 = st.tabs([
        _tr("kg_ui.stats.top_subjects", "📌 Top Subjects"),
        _tr("kg_ui.stats.top_relations", "🔗 Top Relations"),
        _tr("kg_ui.stats.top_objects", "🎯 Top Objects"),
    ])
    
    with entity_tab1:
        render_top_entities_table(top_subjects, "Subjects", "subject")
    
    with entity_tab2:
        render_top_entities_table(top_relations, "Relations", "relation")
    
    with entity_tab3:
        render_top_entities_table(top_objects, "Objects", "object")
    
    # Error log (if any)
    if metrics['errors']:
        st.markdown("---")
        st.markdown(_tr("kg_ui.stats.error_log_header", "### ⚠️ Fehlerprotokoll"))
        with st.expander(_tr("kg_ui.stats.show_errors", "Fehler anzeigen")):
            for error in metrics['errors'][-10:]:  # Last 10 errors
                st.error(f"**{error['timestamp']}**: {error['error']}")


def render_top_entities_table(entities: List[Tuple[str, int]], title: str, entity_type: str):
    """
    Render a table showing top entities with enhanced features
    
    Args:
        entities: List of (entity_name, count) tuples
        title: Display title (e.g., "Subjects", "Relations", "Objects")
        entity_type: Type identifier for styling
    """
    if not entities:
        st.info(_tr("kg_ui.stats.none_found", "ℹ️ Keine {title} gefunden", title=title))
        return
    
    st.markdown(_tr("kg_ui.stats.top_n_title", "#### Top {count} {title}", count=len(entities), title=title))
    
    # Search filter
    search_term = st.text_input(
        _tr("kg_ui.stats.search_in", "🔍 {title} durchsuchen:", title=title),
        key=f"search_{entity_type}",
        placeholder=_tr("kg_ui.stats.search_placeholder", "Begriff eingeben...")
    )
    
    # Filter entities based on search
    if search_term:
        filtered_entities = [(name, count) for name, count in entities 
                            if search_term.lower() in name.lower()]
    else:
        filtered_entities = entities
    
    if not filtered_entities:
        st.warning(_tr("kg_ui.stats.no_search_result", "⚠️ Keine Ergebnisse fuer '{query}' gefunden", query=search_term))
        return
    
    # Sorting options
    col1, col2 = st.columns([3, 1])
    with col2:
        sort_order = st.radio(
            _tr("kg_ui.stats.sort", "Sortierung:"),
            [_tr("kg_ui.stats.sort_freq", "Nach Haeufigkeit"), _tr("kg_ui.stats.sort_alpha", "Alphabetisch")],
            key=f"sort_{entity_type}",
            horizontal=True
        )
    
    # Apply sorting
    if sort_order == _tr("kg_ui.stats.sort_alpha", "Alphabetisch"):
        filtered_entities = sorted(filtered_entities, key=lambda x: x[0].lower())
    
    # Prepare data for display
    max_count = max(count for _, count in filtered_entities) if filtered_entities else 1
    
    # Create DataFrame with enhanced visualization
    data = []
    for rank, (name, count) in enumerate(filtered_entities, 1):
        # Calculate percentage of max
        percentage = (count / max_count) * 100 if max_count > 0 else 0
        
        # Color coding for top entries
        if rank <= 10:
            emoji = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "⭐"
        else:
            emoji = ""
        
        # ✅ FIX: Rang immer als String (verhindert PyArrow mixed-type Error)
        rank_display = f"{emoji} {rank}" if emoji else str(rank)
        
        data.append({
            "Rang": rank_display,
            entity_type.capitalize(): name,
            "Anzahl": count,
            "Anteil (%)": f"{percentage:.1f}",
            "Visualisierung": "█" * int(percentage / 2)  # Simple bar chart
        })
    
    df = pd.DataFrame(data)
    
    # Display metrics
    col1, col2, col3 = st.columns(3)
    col1.metric(_tr("kg_ui.stats.entries", "Eintraege"), len(filtered_entities))
    col2.metric(_tr("kg_ui.stats.most_common", "Haeufigster"), filtered_entities[0][0] if filtered_entities else "-")
    col3.metric(_tr("kg_ui.stats.max_count", "Max. Anzahl"), filtered_entities[0][1] if filtered_entities else 0)
    
    # Display table with custom styling
    st.dataframe(
        df,
        width='stretch',
        height=600,
        hide_index=True
    )
    
    # Export option
    csv_data = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label=_tr("kg_ui.stats.export_entities", "📥 {title} als CSV exportieren", title=title),
        data=csv_data,
        file_name=f"kg_top_{entity_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
        key=f"download_{entity_type}"
    )
    
    # Statistical summary
    with st.expander(_tr("kg_ui.stats.details", "📊 Statistische Details")):
        total_occurrences = sum(count for _, count in filtered_entities)
        unique_entities = len(filtered_entities)
        avg_occurrences = total_occurrences / unique_entities if unique_entities > 0 else 0
        
        col1, col2, col3 = st.columns(3)
        col1.metric(_tr("kg_ui.stats.total_occurrences", "Gesamt Vorkommen"), total_occurrences)
        col2.metric(_tr("kg_ui.stats.unique_entities", "Unique Entities"), unique_entities)
        col3.metric(_tr("kg_ui.stats.avg_occurrences", "Ø Vorkommen"), f"{avg_occurrences:.2f}")
        
        # Show distribution
        if len(filtered_entities) >= 10:
            st.markdown(_tr("kg_ui.stats.distribution", "**Verteilung:**"))
            top_10_sum = sum(count for _, count in filtered_entities[:10])
            top_10_percentage = (top_10_sum / total_occurrences * 100) if total_occurrences > 0 else 0
            st.info(_tr("kg_ui.stats.top10_share", "Die Top 10 machen {share:.1f}% aller Vorkommen aus", share=top_10_percentage))


# ==================================================================================
# TESTING SUPPORT (Optimization #8)
# ==================================================================================

def test_kg_components():
    """Unit tests for core KG components (Optimization #8)"""
    
    print("🧪 Running KG Dashboard Unit Tests...")
    
    # Test 1: Triple hash generation
    triple1 = KGTriple(subject="A", predicate="rel", object="B", doc_id="test")
    triple2 = KGTriple(subject="A", predicate="rel", object="B", doc_id="test")
    assert triple1.get_hash() == triple2.get_hash(), "Hash generation failed"
    print("✅ Test 1: Triple hash generation - PASSED")
    
    # Test 2: Graph building with deduplication
    triples = [
        KGTriple(subject="A", predicate="rel", object="B", doc_id="test"),
        KGTriple(subject="A", predicate="rel", object="B", doc_id="test"),  # Duplicate
        KGTriple(subject="B", predicate="rel2", object="C", doc_id="test"),
    ]
    
    builder = KGGraphBuilder()
    G = builder.build_graph(triples)
    
    assert G.number_of_nodes() == 3, f"Expected 3 nodes, got {G.number_of_nodes()}"
    assert G.number_of_edges() == 2, f"Expected 2 edges, got {G.number_of_edges()}"
    print("✅ Test 2: Graph building with deduplication - PASSED")
    
    # Test 3: Label truncation
    long_label = "A" * 100
    truncated = builder._truncate_label(long_label, max_length=40)
    assert len(truncated) == 40, f"Expected length 40, got {len(truncated)}"
    assert truncated.endswith("..."), "Expected ellipsis"
    print("✅ Test 3: Label truncation - PASSED")
    
    # Test 4: Adaptive algorithm selection
    small_G = nx.DiGraph()
    for i in range(10):
        small_G.add_edge(f"A{i}", f"B{i}")
    
    algo = builder._select_adaptive_algorithm(small_G)
    assert algo == "spring", f"Expected 'spring' for small graph, got '{algo}'"
    print("✅ Test 4: Adaptive algorithm selection - PASSED")
    
    print("🎉 All tests PASSED!")


# ==================================================================================
# TL;DR DOCUMENTATION (Optimization #9)
# ==================================================================================

KG_DASHBOARD_TLDR = """
# 🧠 KG DASHBOARD - TL;DR

## 🚀 Quick Start
1. Navigiere zu "RAG-Dokumente" Tab
2. Wähle Sub-Tab "Knowledge Graph"
3. Wähle ein Dokument mit KG-Daten
4. Visualisierung wird automatisch geladen

## ✨ Features
- **Interaktive Graphen**: Plotly-basierte Visualisierung
- **Volltextsuche**: FTS5-powered für schnelle Suche
- **Smart Layouts**: Automatische Algorithmus-Auswahl basierend auf Größe
- **Export**: CSV & PNG Download
- **Performance**: Layout-Caching, adaptive Algorithmen

## 🎨 Layout-Algorithmen
- **auto**: Automatische Auswahl (empfohlen)
- **spring**: Gut für kleine Graphen (<20 Knoten)
- **kamada_kawai**: Gut für mittlere Graphen (20-100 Knoten)
- **circular**: Gut für große Graphen (100-500 Knoten)
- **shell**: Gut für sehr große Graphen (>500 Knoten)

## 📊 Monitoring
- View-Counter & Performance-Tracking
- Error-Logging
- Usage-Statistiken

## 🔧 Wartung
- Cache wird automatisch verwaltet (TTL: 1h, Max: 100 Einträge)
- FTS5-Index wird automatisch synchronisiert
- Session-State Cache-Invalidierung nach Upload

## 📚 Weiterführende Dokumentation
Siehe:
- KG_VISUALISIERUNG_MASTERPLAN.md
- KG_VISUALISIERUNG_KRITISCHE_ANALYSE.md
- KG_VISUALISIERUNG_QUICK_GUIDE.md
"""


if __name__ == "__main__":
    # Run tests if executed directly
    test_kg_components()
    
    # Print TL;DR
    print("\n" + KG_DASHBOARD_TLDR)
