"""
Knowledge Graph Provider for user context building.

Fetches and formats knowledge graph triples related to the user.
"""

from typing import Optional, Any, List, Dict
import logging
from user_context_builder.base import BaseContextProvider
from user_context_builder.models import KnowledgeGraphData, UserContextRequest

logger = logging.getLogger(__name__)


class KnowledgeGraphProvider(BaseContextProvider):
    """Provider for fetching knowledge graph triples about the user."""
    
    def __init__(
        self,
        max_triples: int = 50,
        priority: int = 10,
    ):
        """
        Initialize the Knowledge Graph Provider.
        
        Args:
            max_triples: Maximum number of triples to fetch
            priority: Provider priority (lower = higher priority)
        """
        super().__init__(name="knowledge_graph", priority=priority)
        self.max_triples = max_triples
    
    def provide(
        self,
        request: UserContextRequest,
        session_manager: Any
    ) -> Optional[KnowledgeGraphData]:
        """
        Fetch knowledge graph triples for the user.
        
        Args:
            request: User context request
            session_manager: Session manager with KG access
            
        Returns:
            KnowledgeGraphData or None if no triples found
        """
        db_manager = self._resolve_db_manager(session_manager)

        if not db_manager or not hasattr(db_manager, 'search_knowledge_graph_semantic'):
            logger.warning("KG DB manager not available or missing semantic search method")
            return KnowledgeGraphData()
        
        try:
            triples = self._get_knowledge_graph_triples(
                db_manager=db_manager,
                request=request,
            )
            
            if not triples:
                logger.debug(f"No KG triples found for user {request.user_id}")
                return KnowledgeGraphData()
            
            # Also try to get insights if available
            insights: List[Dict[str, Any]] = []
            
            return KnowledgeGraphData(
                triples=triples,
                insights=insights,
                total_retrieved=len(triples),
                total_selected=min(len(triples), self.max_triples)
            )
        
        except Exception as e:
            logger.error(f"Error fetching KG triples: {e}", exc_info=True)
            # Re-raise so the builder can record the error and continue
            raise

    def _resolve_db_manager(self, session_manager: Any) -> Optional[Any]:
        """Resolve the psychological DB from either direct or adapter-based managers."""
        db_manager = getattr(session_manager, 'db_manager', None)
        if db_manager is not None:
            return db_manager

        manager = getattr(session_manager, 'manager', None)
        if manager is not None:
            return getattr(manager, 'db', None)

        return None

    def _get_knowledge_graph_triples(
        self,
        db_manager: Any,
        request: UserContextRequest,
    ) -> List[Dict[str, Any]]:
        """Build KG context using the same retrieval strategy as the legacy builder."""
        from wellbeing_session.context import adaptive_triple_selection
        from wellbeing_session.context.family_entity_boost import family_entity_kg_boost

        kg_results = db_manager.search_knowledge_graph_semantic(
            query=request.user_input,
            user_id=request.user_id,
            session_id=None,
            limit=max(50, self.max_triples),
            min_confidence=0.4,
            similarity_threshold=0.40,
        )

        raw_triples: List[Dict[str, Any]] = []
        if kg_results:
            for triple in kg_results:
                raw_triples.append({
                    'subject': triple.get('subject'),
                    'predicate': triple.get('predicate'),
                    'object': triple.get('object'),
                    'confidence': triple.get('confidence', 0),
                    'similarity': triple.get('similarity', 0),
                    'combined_score': triple.get('combined_score', 0),
                    'rerank_score': triple.get('rerank_score', 0),
                    'entity_score': triple.get('entity_score', 0),
                    'source_date': triple.get('interaction_date') or triple.get('created_at') or 'N/A',
                })

        if hasattr(db_manager, 'get_high_confidence_triples'):
            baseline_raw = db_manager.get_high_confidence_triples(
                user_id=request.user_id,
                limit=min(30, self.max_triples),
                min_confidence=0.5,
            )
            existing_keys = {
                (t.get('subject', ''), t.get('predicate', ''), t.get('object', ''))
                for t in raw_triples
            }
            for triple in baseline_raw or []:
                key = (triple.get('subject', ''), triple.get('predicate', ''), triple.get('object', ''))
                if key not in existing_keys:
                    raw_triples.append(triple)
                    existing_keys.add(key)

        raw_triples = family_entity_kg_boost(
            db=db_manager,
            query=request.user_input,
            existing_triples=raw_triples,
            user_id=request.user_id,
        )

        return adaptive_triple_selection(
            raw_triples,
            min_relevance=0.35,
            max_triples=self.max_triples,
            similarity_drop_threshold=0.99,
        )
