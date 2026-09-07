#!/usr/bin/env python3
"""
PSYCHOLOGISCHE ORCHESTRATOR-INTEGRATION
========================================

Erweitert den AgentOrchestrator um psychologische Profil-Integration:
1. Lädt User-Profile aus der psychologischen Datenbank (PERSISTENT CACHED)
2. Integriert Familiendaten in RAG-Prompts (LLM-BASED, NO HARDCODE)
3. Nutzt Session-übergreifende psychologische Kontexte
4. Erweitert Evidence-Selection um psychologische Relevanz

Diese Integration erfolgt sauber über den bestehenden Orchestrator,
ohne die Kern-Architektur zu verletzen.

NEUE FEATURES (State-of-the-Art):
- Persistent profile caching (TTL: 30min, DB-backed)
- LLM-based profile synthesis (no hardcoded keywords)
- Smart invalidation triggers (session end, KG update)
- 20-30x faster profile loading (cached)
"""

import logging
import threading
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# NEU: Persistent Profile Imports
try:
    from wellbeing.profile_synthesizer import create_profile_synthesizer
    from wellbeing.profile_cache_manager import create_profile_cache_manager
    PERSISTENT_PROFILE_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Persistent profile system not available: {e}")
    PERSISTENT_PROFILE_AVAILABLE = False
    create_profile_synthesizer = None  # type: ignore
    create_profile_cache_manager = None  # type: ignore

# Thread-lokaler Kontext für User-ID
_thread_local = threading.local()

def set_current_user_id(user_id: str):
    """Setzt die User-ID für den aktuellen Thread"""
    _thread_local.user_id = user_id


def set_wellbeing_context_enabled(enabled: bool):
    """Enable/disable psychological query enrichment for current request/thread."""
    _thread_local.wellbeing_context_enabled = bool(enabled)


def get_wellbeing_context_enabled() -> bool:
    """Return whether current request/thread allows psychological enrichment."""
    return bool(getattr(_thread_local, 'wellbeing_context_enabled', False))

def get_thread_local_user_id() -> Optional[str]:
    """Holt die User-ID für den aktuellen Thread"""
    return getattr(_thread_local, 'user_id', None)

class WellbeingOrchestratorIntegration:
    """
    Psychologische Integration für den AgentOrchestrator
    
    Erweitert den Orchestrator um:
    - User-Profil-basierte Prompt-Anpassung (PERSISTENT CACHED)
    - Familiendaten-Integration in RAG-Suchen (LLM-BASED, NO HARDCODE)
    - Session-übergreifende psychologische Kontexte
    - Psychologisch-relevante Evidence-Priorisierung
    
    NEUE FEATURES (State-of-the-Art):
    - Persistent profile caching (20-30x faster)
    - LLM-based profile synthesis (no hardcoded keywords)
    - Smart invalidation triggers
    """
    
    def __init__(self, orchestrator, wellbeing_db=None, user_insight_extractor=None):
        """
        Initialisiert die psychologische Integration
        
        Args:
            orchestrator: AgentOrchestrator Instanz
            wellbeing_db: WellbeingDatabase Instanz
            user_insight_extractor: WellbeingUserInsightExtractor Instanz
        """
        self.orchestrator = orchestrator
        self.wellbeing_db = wellbeing_db
        self.user_insight_extractor = user_insight_extractor
        self.enabled = wellbeing_db is not None
        
        # NEU: Setup persistent profile caching (State-of-the-Art)
        self.profile_cache_manager = None
        # ✅ FIXED: Check both functions are available (not None)
        if (self.enabled and PERSISTENT_PROFILE_AVAILABLE and 
            create_profile_synthesizer is not None and 
            create_profile_cache_manager is not None):
            try:
                if hasattr(orchestrator, 'model_loader') and orchestrator.model_loader:
                    synthesizer = create_profile_synthesizer(wellbeing_db, orchestrator.model_loader)
                    self.profile_cache_manager = create_profile_cache_manager(
                        wellbeing_db, synthesizer, ttl_minutes=30, max_cache_size=100
                    )
                    logger.info("✅ Persistent profile caching enabled (TTL: 30min, LLM-based synthesis)")
                else:
                    logger.warning("⚠️ model_loader not available - persistent caching disabled")
            except Exception as e:
                logger.error(f"❌ Failed to setup persistent profile caching: {e}")
                self.profile_cache_manager = None
        
        if self.enabled:
            logger.info("🧠 Psychologische Orchestrator-Integration aktiviert")
        else:
            logger.warning("⚠️ Psychologische Integration nicht verfügbar - kein wellbeing_db")
    
    def enhance_query_with_wellbeing_context(self, query: str, user_id: str, tool_type: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        """
        Erweitert eine Anfrage um psychologischen Kontext - ONLY FÜR RAG/KNOWLEDGE TOOLS
        
        NEUE VERSION: Nutzt persistent cached profiles (LLM-synthesized, 20-30x faster)
        ALTE VERSION: Dynamic loading (3+ DB queries, hardcoded keywords) → FALLBACK
        
        Args:
            query: Original-Anfrage
            user_id: Benutzer-ID
            tool_type: Art des Tools (web_search, rag, etc.)
            
        Returns:
            Tuple[erweiterte_anfrage, psychologischer_kontext]
        """
        if not self.enabled:
            return query, {}
        
        # DATENSCHUTZ: Psychologische Daten NUR für RAG/Knowledge Tools, NICHT für Web-Suche
        if tool_type and tool_type.lower() in ['web_search', 'websearch', 'search_web', 'web_scraper', 'browser']:
            logger.info(f"🔒 DATENSCHUTZ: Psychologische Daten werden NICHT an {tool_type} weitergegeben")
            return query, {}
        
        try:
            # ✅ Primärer Pfad: persistent cached profile
            if self.profile_cache_manager:
                enhanced_query, wellbeing_context = self._enhance_with_cached_profile(query, user_id, tool_type)
                if wellbeing_context:
                    return enhanced_query, wellbeing_context

                # Kein verwertbares Profil im Cache-Pfad → dynamischer Fallback
                logger.debug("⚠️ Cached profile path returned no context - falling back to dynamic loading")

            # ⚠️ FALLBACK: Dynamic loading
            logger.debug("⚠️ Using fallback dynamic profile loading")
            return self._enhance_with_dynamic_loading(query, user_id, tool_type)

        except Exception as e:
            logger.error(f"❌ Psychologische Kontext-Erweiterung fehlgeschlagen: {e}")
            return query, {}

    def _context_metadata(self, source: str, is_degraded_mode: bool) -> Dict[str, Any]:
        """Build standardized metadata for psychological context payloads."""
        return {
            'context_source': source,
            'is_degraded_mode': is_degraded_mode,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

    def _with_metadata(self, context: Dict[str, Any], source: str, is_degraded_mode: bool) -> Dict[str, Any]:
        """Attach standardized metadata to a context payload."""
        merged = dict(context)
        merged['metadata'] = self._context_metadata(source, is_degraded_mode)
        return merged
    
    def _enhance_with_cached_profile(self, query: str, user_id: str, tool_type: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        """
        ✅ NEW: Enhanced query with CACHED persistent profile (State-of-the-Art)
        - Single DB query (or memory cache hit)
        - LLM-synthesized holistic profile
        - No hardcoded keywords
        - 20-30x faster than dynamic loading
        """
        try:
            # ✅ FIXED: Check if profile cache manager is available
            if self.profile_cache_manager is None:
                logger.warning("⚠️ Profile cache manager not available - using fallback")
                return query, {}
            
            # Load cached profile (fast: <100ms if cached, ~3s if cache miss)
            profile = self.profile_cache_manager.get_cached_profile(user_id)
            
            if not profile:
                logger.warning(f"⚠️ No profile available for {user_id[:10]}... - trying dynamic fallback")
                return self._enhance_with_dynamic_loading(query, user_id, tool_type)
            
            logger.info(f"✅ Cached profile loaded for {user_id[:10]}... (confidence: {profile.overall_confidence:.2f}, Tool: {tool_type or 'RAG'})")
            
            # Build context from profile
            wellbeing_context = {
                'core_personality': profile.core_personality,
                'current_state': profile.current_state,
                'relationships': profile.relationships,  # ✅ Includes family (LLM-extracted, NO hardcode!)
                'goals_and_growth': profile.goals_and_growth,
                'coping_and_resources': profile.coping_and_resources,
                'therapeutic_focus': profile.therapeutic_focus,
                'confidence': profile.overall_confidence,
                'data_sources': profile.data_sources
            }
            
            # Build enhanced query from profile
            enhanced_query = self._build_enhanced_query_from_cached_profile(query, profile)
            
            return enhanced_query, self._with_metadata(
                wellbeing_context,
                source='persistent_cache',
                is_degraded_mode=False,
            )
            
        except Exception as e:
            logger.error(f"❌ Cached profile enhancement failed: {e}")
            return self._enhance_with_dynamic_loading(query, user_id, tool_type)
    
    def _enhance_with_dynamic_loading(self, query: str, user_id: str, tool_type: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        """
        ⚠️ FALLBACK: Old dynamic loading method (if cache not available)
        - Multiple DB queries (3+)
        - Hardcoded keywords
        - Slower
        """
        wellbeing_context = {}
        
        # 1. LADE USER-PROFIL
        if self.user_insight_extractor:
            profile = self.user_insight_extractor.get_personality_profile(user_id)
            if profile:
                wellbeing_context['profile'] = {
                    'core_traits': profile.core_traits or [],
                    'primary_concerns': profile.primary_concerns or [],
                    'relationship_patterns': profile.relationship_patterns or [],
                    'communication_style': profile.communication_style,
                    'current_mood': profile.current_mood
                }
                logger.info(f"👤 User-Profil für {user_id[:10]}... geladen (Tool: {tool_type or 'RAG'})")
        
        # 2. LADE FAMILIENDATEN AUS SESSIONS
        family_context = self._extract_family_context(user_id)
        if family_context:
            wellbeing_context['family'] = family_context
            logger.info(f"👨‍👩‍👧‍👦 Familiendaten für {user_id[:10]}... geladen (Tool: {tool_type or 'RAG'})")
        
        # 3. LADE AKTUELLE SESSION-HISTORIE
        session_context = self._extract_recent_session_context(user_id)
        if session_context:
            wellbeing_context['recent_sessions'] = session_context
            logger.info(f"📝 Session-Kontext für {user_id[:10]}... geladen")
        
        # 4. ERWEITERE QUERY MIT PSYCHOLOGISCHEM KONTEXT
        if wellbeing_context:
            enhanced_query = self._build_wellbeing_enhanced_query(query, wellbeing_context)
            return enhanced_query, self._with_metadata(
                wellbeing_context,
                source='dynamic_fallback',
                is_degraded_mode=True,
            )
        
        return query, {}
    
    def _extract_family_context(self, user_id: str) -> Dict[str, Any]:
        """Extrahiert Familiendaten aus der psychologischen Datenbank"""
        if not self.wellbeing_db:
            return {}
        
        try:
            family_context = {}
            
            # Suche nach Familie-relevanten Interaktionen
            # (nur generische Beziehungs-Wörter — keine konkreten Personen-Namen,
            #  PII-sicher, 2026-08-28)
            family_keywords = ['mama', 'mutter', 'vater', 'papa', 'dad', 'mom',
                             'cousine', 'cousin', 'familie', 'family', 'eltern']
            
            with self.wellbeing_db.get_connection() as conn:
                # Finde Familie-relevante Interaktionen aus allen Sessions
                cursor = conn.execute("""
                    SELECT si.content, si.content_encrypted, si.role, si.created_at
                    FROM session_interactions si
                    JOIN wellbeing_sessions ps ON si.session_id = ps.id
                    WHERE ps.user_id = ?
                    ORDER BY si.created_at DESC
                    LIMIT 20
                """, (user_id,))
                
                family_interactions = []
                for row in cursor.fetchall():
                    content = row['content']
                    if row['content_encrypted']:
                        try:
                            content = self.wellbeing_db._decrypt_data(row['content'])
                        except Exception as exc:
                            logger.debug(f"Failed decrypting family interaction content: {exc}")
                            continue
                    
                    # Prüfe auf Familie-Keywords und Kontext
                    content_lower = content.lower()
                    if any(keyword in content_lower for keyword in family_keywords):
                        # Zusätzliche Kontextprüfung für echte Familiengespräche
                        if self._is_personal_family_content(content):
                            family_interactions.append({
                                'content': content[:300],  # Begrenzte Länge
                                'role': row['role'],
                                'created_at': row['created_at']
                            })
                
                if family_interactions:
                    family_context['interactions'] = family_interactions[:5]  # Max 5 relevante
                    
                    # Extrahiere Familie-Entitäten
                    family_entities = self._extract_family_entities(family_interactions)
                    if family_entities:
                        family_context['entities'] = family_entities
            
            return family_context
            
        except Exception as e:
            logger.error(f"❌ Familie-Kontext-Extraktion fehlgeschlagen: {e}")
            return {}
    
    def _is_personal_family_content(self, content: str) -> bool:
        """Prüft ob Inhalt persönliche Familiengespräche enthält (nicht technisch)"""
        content_lower = content.lower()
        
        # Ausschlusskriterien - technische Inhalte
        technical_indicators = [
            'analyse', 'chatbot', 'metadaten', 'feedback', 'system:', 'algorithmus'
        ]
        
        if any(indicator in content_lower for indicator in technical_indicators):
            return False
        
        # Einschlusskriterien - persönliche Familienbezüge
        personal_indicators = [
            'meine', 'mein', 'mit meiner', 'mit meinem', 'ich', 'mir', 'mich'
        ]
        
        return any(indicator in content_lower for indicator in personal_indicators)
    
    def _extract_family_entities(self, family_interactions: List[Dict]) -> Dict[str, List[str]]:
        """Extrahiert Familie-Entitäten aus Interaktionen"""
        entities = {
            'family_members': [],
            'relationships': [],
            'concerns': []
        }
        
        for interaction in family_interactions:
            content = interaction['content'].lower()
            
            # Familie-Mitglieder (nur generische Beziehungs-Rollen, keine konkreten
            # Personen-Namen — PII-sicher, 2026-08-28)
            if any(word in content for word in ['vater', 'papa', 'dad']):
                entities['family_members'].append('Vater')
            if any(word in content for word in ['mutter', 'mama', 'mom']):
                entities['family_members'].append('Mutter')
            if any(word in content for word in ['cousine', 'cousin']):
                entities['family_members'].append('Cousin/in')

            # Beziehungs-Muster
            if 'konflikt' in content and any(word in content for word in ['vater', 'papa', 'dad']):
                entities['relationships'].append('Konflikt mit Vater')
        
        # Entferne Duplikate
        for key in entities:
            entities[key] = list(set(entities[key]))
        
        return entities
    
    def _extract_recent_session_context(self, user_id: str) -> List[Dict[str, Any]]:
        """Extrahiert jüngsten Session-Kontext für bessere Kontinuität"""
        if not self.wellbeing_db:
            return []
        
        try:
            with self.wellbeing_db.get_connection() as conn:
                # Hole letzte 3 Sessions mit ihren letzten Interaktionen
                cursor = conn.execute("""
                    SELECT id, start_time, session_summary
                    FROM wellbeing_sessions
                    WHERE user_id = ? AND start_time >= datetime('now', '-7 days')
                    ORDER BY start_time DESC
                    LIMIT 3
                """, (user_id,))
                
                sessions = []
                for session_row in cursor.fetchall():
                    session_id = session_row['id']
                    
                    # Hole letzte Interaktion dieser Session
                    interaction_cursor = conn.execute("""
                        SELECT content, content_encrypted, role, created_at
                        FROM session_interactions
                        WHERE session_id = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (session_id,))
                    
                    interaction = interaction_cursor.fetchone()
                    if interaction:
                        content = interaction['content']
                        if interaction['content_encrypted']:
                            try:
                                content = self.wellbeing_db._decrypt_data(interaction['content'])
                            except Exception as exc:
                                logger.debug(f"Failed decrypting session context interaction: {exc}")
                                continue
                        
                        sessions.append({
                            'session_id': session_id,
                            'start_time': session_row['start_time'],
                            'summary': session_row['session_summary'],
                            'last_interaction': content[:200]  # Begrenzt
                        })
                
                return sessions
                
        except Exception as e:
            logger.error(f"❌ Session-Kontext-Extraktion fehlgeschlagen: {e}")
            return []
    
    def _build_wellbeing_enhanced_query(self, original_query: str, wellbeing_context: Dict[str, Any]) -> str:
        """
        Baut eine psychologisch erweiterte Anfrage
        
        🔒 KRITISCH: Diese Funktion darf NUR für INTERNE LLM-Prompts verwendet werden!
        NIEMALS für Web-Suchen, externe APIs oder andere externe Services!
        """
        
        enhanced_parts = [original_query]
        
        # 🔒 DATENSCHUTZ-WARNUNG: Psychologische Daten folgen (NUR für interne Verarbeitung!)
        # Füge psychologischen Kontext hinzu
        if wellbeing_context:
            enhanced_parts.append("\n\n🔒 INTERNER PSYCHOLOGISCHER BENUTZER-KONTEXT (NICHT FÜR EXTERNE NUTZUNG):")
            
            # User-Profil
            if 'profile' in wellbeing_context:
                profile = wellbeing_context['profile']
                if profile.get('core_traits'):
                    enhanced_parts.append(f"Persönlichkeits-Traits: {', '.join(profile['core_traits'][:3])}")
                if profile.get('primary_concerns'):
                    enhanced_parts.append(f"Hauptanliegen: {', '.join(profile['primary_concerns'][:2])}")
                if profile.get('communication_style'):
                    enhanced_parts.append(f"Kommunikationsstil: {profile['communication_style']}")
            
            # Familie-Kontext
            if 'family' in wellbeing_context:
                family = wellbeing_context['family']
                if family.get('entities', {}).get('family_members'):
                    members = ', '.join(family['entities']['family_members'])
                    enhanced_parts.append(f"Familie: {members}")
                if family.get('entities', {}).get('concerns'):
                    concerns = ', '.join(family['entities']['concerns'])
                    enhanced_parts.append(f"Familienthemen: {concerns}")
            
            # Session-Kontinuität
            if 'recent_sessions' in wellbeing_context:
                sessions = wellbeing_context['recent_sessions'][:2]  # Max 2
                if sessions:
                    enhanced_parts.append("Letzte Gesprächsthemen:")
                    for session in sessions:
                        if session.get('summary'):
                            enhanced_parts.append(f"- {session['summary'][:100]}")
        
        return '\n'.join(enhanced_parts)
    
    def _build_enhanced_query_from_cached_profile(self, query: str, profile) -> str:
        """
        ✅ NEW: Build enhanced query from CACHED profile (LLM-synthesized)
        - No hardcoded keywords
        - Holistic profile structure
        - Includes family dynamics (LLM-extracted)
        """
        enhanced_parts = [query]
        
        # 🔒 DATENSCHUTZ-WARNUNG: Psychologische Daten folgen (NUR für interne Verarbeitung!)
        enhanced_parts.append("\n\n🔒 INTERNER PSYCHOLOGISCHER BENUTZER-KONTEXT (NICHT FÜR EXTERNE NUTZUNG):")
        
        # Core personality
        if profile.core_personality and profile.core_personality.get('traits'):
            traits = profile.core_personality['traits']
            confidence = profile.core_personality.get('confidence', 0.0)
            enhanced_parts.append(f"\nKern-Persönlichkeit (Confidence: {confidence:.2f}):")
            enhanced_parts.append(f"  - Traits: {', '.join(traits)}")
            if 'communication_style' in profile.core_personality:
                enhanced_parts.append(f"  - Kommunikationsstil: {profile.core_personality['communication_style']}")
        
        # Current state
        if profile.current_state and profile.current_state.get('primary_concerns'):
            concerns = profile.current_state['primary_concerns']
            confidence = profile.current_state.get('confidence', 0.0)
            enhanced_parts.append(f"\nAktueller Zustand (Confidence: {confidence:.2f}):")
            enhanced_parts.append(f"  - Hauptanliegen: {', '.join(concerns)}")
            if 'emotional_tone' in profile.current_state:
                enhanced_parts.append(f"  - Emotionaler Ton: {profile.current_state['emotional_tone']}")
        
        # Relationships (✅ includes family - LLM-extracted, NO hardcoded keywords!)
        if profile.relationships and profile.relationships.get('family_dynamics'):
            family = profile.relationships['family_dynamics']
            confidence = family.get('confidence', 0.0)
            enhanced_parts.append(f"\nBeziehungen/Familie (Confidence: {confidence:.2f}):")
            if 'members' in family and family['members']:
                enhanced_parts.append(f"  - Familienmitglieder: {', '.join(family['members'])}")
            if 'current_concerns' in family and family['current_concerns']:
                enhanced_parts.append(f"  - Familiäre Anliegen: {', '.join(family['current_concerns'])}")
        
        # Goals
        if profile.goals_and_growth and profile.goals_and_growth.get('current_goals'):
            goals = profile.goals_and_growth['current_goals']
            confidence = profile.goals_and_growth.get('confidence', 0.0)
            enhanced_parts.append(f"\nZiele (Confidence: {confidence:.2f}):")
            enhanced_parts.append(f"  - {', '.join(goals)}")
        
        # Therapeutic focus
        if profile.therapeutic_focus and profile.therapeutic_focus.get('priority_areas'):
            priority_areas = profile.therapeutic_focus['priority_areas']
            enhanced_parts.append(f"\nTherapeutischer Fokus:")
            enhanced_parts.append(f"  - Prioritätsbereiche: {', '.join(priority_areas)}")
        
        return "\n".join(enhanced_parts)
    
    def enhance_evidence_selection(self, evidence_sources: List[Any], wellbeing_context: Dict[str, Any]) -> List[Any]:
        """Erweitert Evidence-Selection um psychologische Relevanz"""
        if not wellbeing_context or not evidence_sources:
            return evidence_sources
        
        try:
            # Priorisiere Evidence-Sources basierend auf psychologischem Kontext
            enhanced_sources = []
            
            for source in evidence_sources:
                relevance_score = self._calculate_wellbeing_relevance(source, wellbeing_context)
                source.psychological_relevance = relevance_score
                enhanced_sources.append(source)
            
            # Sortiere nach psychologischer Relevanz (falls vorhanden)
            enhanced_sources.sort(
                key=lambda s: getattr(s, 'psychological_relevance', 0.5),
                reverse=True
            )
            
            return enhanced_sources
            
        except Exception as e:
            logger.error(f"❌ Psychologische Evidence-Selection fehlgeschlagen: {e}")
            return evidence_sources
    
    def _calculate_wellbeing_relevance(self, source: Any, wellbeing_context: Dict[str, Any]) -> float:
        """Berechnet psychologische Relevanz einer Evidence-Source"""
        try:
            relevance = 0.5  # Base score
            
            source_content = getattr(source, 'content', '').lower()
            if not source_content:
                return relevance
            
            # Familie-Relevanz
            if 'family' in wellbeing_context:
                family_keywords = ['familie', 'family', 'beziehung', 'relation', 'konflikt', 'eltern']
                if any(keyword in source_content for keyword in family_keywords):
                    relevance += 0.3
            
            # Persönlichkeits-Relevanz
            if 'profile' in wellbeing_context:
                profile = wellbeing_context['profile']
                if profile.get('primary_concerns'):
                    for concern in profile['primary_concerns']:
                        if concern.lower() in source_content:
                            relevance += 0.2
            
            return min(1.0, relevance)
            
        except Exception as e:
            logger.debug(f"Psychologische Relevanz-Berechnung fehlgeschlagen: {e}")
            return 0.5

    # ===== INVALIDATION HOOKS (State-of-the-Art) =====
    
    def invalidate_profile_on_session_end(self, user_id: str, session_id: str):
        """
        ✅ NEW: Invalidate cached profile when session ends
        Triggers profile regeneration on next access
        
        Call this from session_manager.end_session()
        """
        if not self.profile_cache_manager:
            return
        
        try:
            self.profile_cache_manager.invalidate_profile(
                user_id=user_id,
                trigger_type='session_end',
                trigger_source_id=session_id
            )
            logger.info(f"🗑️ Profile invalidated for {user_id[:10]}... (session end: {session_id[:10]}...)")
        except Exception as e:
            logger.error(f"❌ Profile invalidation failed: {e}")
    
    def invalidate_profile_on_kg_update(self, user_id: str, triple_id: Optional[str] = None):
        """
        ✅ NEW: Invalidate cached profile when new KG triples are added
        Triggers profile regeneration on next access
        
        Call this from wellbeing_db.store_kg_triple()
        """
        if not self.profile_cache_manager:
            return
        
        try:
            self.profile_cache_manager.invalidate_profile(
                user_id=user_id,
                trigger_type='kg_update',
                trigger_source_id=triple_id
            )
            logger.debug(f"🗑️ Profile invalidated for {user_id[:10]}... (KG update)")
        except Exception as e:
            logger.error(f"❌ Profile invalidation failed: {e}")
    
    def get_cache_stats(self) -> Optional[Dict[str, Any]]:
        """
        ✅ NEW: Get cache statistics (hit rate, evictions, etc.)
        Useful for monitoring and debugging
        """
        if not self.profile_cache_manager:
            return None
        
        try:
            return self.profile_cache_manager.get_cache_stats()
        except Exception as e:
            logger.error(f"❌ Failed to get cache stats: {e}")
            return None


def integrate_wellbeing_orchestrator(orchestrator, wellbeing_db=None, user_insight_extractor=None):
    """
    Integriert psychologische Funktionalität in einen bestehenden Orchestrator
    
    Args:
        orchestrator: AgentOrchestrator Instanz
        wellbeing_db: WellbeingDatabase Instanz
        user_insight_extractor: WellbeingUserInsightExtractor Instanz
        
    Returns:
        WellbeingOrchestratorIntegration Instanz
    """
    integration = WellbeingOrchestratorIntegration(
        orchestrator=orchestrator,
        wellbeing_db=wellbeing_db,
        user_insight_extractor=user_insight_extractor
    )
    
    # Erweitere Orchestrator um psychologische Methoden
    orchestrator.wellbeing_integration = integration
    
    # Monkeypatch: Erweitere run_tools_and_summarize
    original_run_tools_and_summarize = orchestrator.run_tools_and_summarize
    
    def enhanced_run_tools_and_summarize(query: str, planned_calls, history, 
                                       reasoning=None, critique=None, planner_ms=None, 
                                       planner_raw=None, user_id=None):
        """Erweiterte run_tools_and_summarize mit psychologischer Integration"""
        
        # User-ID aus Thread-Local-Context oder Parameter holen
        current_user_id = user_id or get_thread_local_user_id()
        
        # 🔒 KRITISCHER DATENSCHUTZ: Prüfe ob Web-Suche/Externe Tools verwendet werden
        # DEFAULT: Annahme dass es externe Tools gibt (Safe by Default!)
        has_external_tools = True
        tool_types = []
        
        if planned_calls:
            has_external_tools = False  # Reset - wir prüfen jetzt genau
            for call in planned_calls:
                tool_name = ""
                
                # Extrahiere Tool-Namen aus verschiedenen Call-Formaten
                if hasattr(call, 'function') and hasattr(call.function, 'name'):
                    tool_name = call.function.name.lower()
                elif hasattr(call, 'tool'):
                    tool_name = call.tool.lower()
                elif isinstance(call, dict) and 'tool' in call:
                    tool_name = call['tool'].lower()
                
                # 🔒 STRIKTE WHITELIST: NUR explizit sichere Tools erlauben psychologische Daten
                safe_internal_tools = [
                    'rag_search',      # RAG ist intern
                    'rag_upsert',      # RAG ist intern
                    'knowledge_graph', # KG ist intern
                    'db_query',        # Lokale DB
                ]
                
                # Prüfe ob Tool sicher ist
                is_safe = any(safe_tool in tool_name for safe_tool in safe_internal_tools)
                
                if not is_safe:
                    # JEDES Tool das nicht explizit sicher ist = extern!
                    has_external_tools = True
                    logger.warning(f"🔒 DATENSCHUTZ: Tool '{tool_name}' ist nicht in Safe-Whitelist - psychologische Daten werden NICHT verwendet")
                    break  # Ein externes Tool reicht
                else:
                    tool_types.append(tool_name)
        
        # Psychologische Kontext-Erweiterung NUR wenn:
        # 1. user_id verfügbar
        # 2. Integration aktiviert  
        # 3. KEINE externen Tools (Safe by Default!)
        wellbeing_context_enabled = get_wellbeing_context_enabled()

        if (
            current_user_id
            and integration.enabled
            and wellbeing_context_enabled
            and not has_external_tools
        ):
            try:
                # Verwende RAG/Knowledge Tools - psychologische Daten erlaubt
                enhanced_query, wellbeing_context = integration.enhance_query_with_wellbeing_context(
                    query, current_user_id, tool_type='rag'
                )
                logger.info(f"🧠 Query psychologisch erweitert für User {current_user_id[:10]}... (RAG-Tools)")
                
                # Nutze erweiterte Query
                result = original_run_tools_and_summarize(
                    enhanced_query, planned_calls, history, reasoning, critique, planner_ms, planner_raw
                )
                
                # Erweitere Result um psychologischen Kontext
                if hasattr(result, 'sources') and result.sources and wellbeing_context:
                    result.sources = integration.enhance_evidence_selection(result.sources, wellbeing_context)
                
                return result
                
            except Exception as e:
                logger.error(f"❌ Psychologische Integration in run_tools_and_summarize fehlgeschlagen: {e}")
                # Fallback auf Original
                return original_run_tools_and_summarize(query, planned_calls, history, reasoning, critique, planner_ms, planner_raw)
        else:
            # Externe Tools oder ohne user_id: KEINE psychologischen Daten verwenden
            if has_external_tools and current_user_id:
                logger.info(f"🔒 DATENSCHUTZ: Externe Tools erkannt - psychologische Daten werden NICHT weitergegeben")
            elif integration.enabled and not wellbeing_context_enabled:
                logger.debug(
                    "Psychological context disabled for this request; running without enrichment"
                )
            
            return original_run_tools_and_summarize(query, planned_calls, history, reasoning, critique, planner_ms, planner_raw)
    
    # Ersetze die Methode
    orchestrator.run_tools_and_summarize = enhanced_run_tools_and_summarize
    
    logger.info("✅ Psychologische Orchestrator-Integration abgeschlossen")
    return integration


if __name__ == "__main__":
    print("Psychologische Orchestrator-Integration")
    print("Verwende integrate_wellbeing_orchestrator() um einen Orchestrator zu erweitern")
