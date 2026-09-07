"""
Universal Entity Consistency Validator
======================================

Vollständig domänen-agnostische Lösung für Entitäts-Konsistenz und Quellen-Validierung.
Funktioniert für beliebige Entitäten: Firmen, Länder, Produkte, Personen, etc.

Kern-Prinzip: LLM-basierte Entitäts-Erkennung und Konsistenz-Prüfung ohne 
domänen-spezifische Annahmen oder hard-coded Regeln.
"""

import logging
from typing import Optional, List, Dict, Any, Tuple, Set
from dataclasses import dataclass
import json
import re

@dataclass
class EntityAnalysis:
    """Universelle Entitäts-Analyse für beliebige Domänen."""
    primary_entities: List[Dict[str, Any]]  # [{"name": "SBB", "type": "company", "confidence": 0.9}]
    secondary_entities: List[Dict[str, Any]]  # Verwandte/erwähnte Entitäten
    domain_context: Optional[str]  # "transportation", "technology", "healthcare", etc.
    geographic_scope: Optional[str]  # "Switzerland", "Europe", "Global", etc.
    temporal_scope: Optional[str]  # "2023", "last 5 years", "historical", etc.
    enhanced_query: str
    confidence_score: float

@dataclass
class ConsistencyValidation:
    """Universelle Konsistenz-Validierung für beliebige Entitäten."""
    is_consistent: bool
    entity_matches: List[Dict[str, Any]]  # Matching entities between query and sources
    entity_conflicts: List[Dict[str, Any]]  # Conflicting entities
    relevance_score: float
    conflict_severity: str  # "none", "minor", "major", "critical"
    warning_message: Optional[str]
    recommendations: List[str]

class UniversalEntityValidator:
    """
    Domänen-agnostische Entitäts-Konsistenz-Validierung.
    
    Funktionsweise:
    1. Erkennt beliebige Entitäten in Query (LLM-basiert)
    2. Extrahiert Entitäten aus Quellen-Texten  
    3. Prüft Konsistenz zwischen Query-Entitäten und Quellen-Entitäten
    4. Warnt vor Entitäts-Konflikten (SBB vs DB, iPhone vs Samsung, etc.)
    5. Enhanced Queries für bessere RAG-Suche
    
    Vorteile:
    - Funktioniert für ALLE Domänen (nicht nur Firmen)
    - Erkennt beliebige Entitäts-Typen automatisch
    - Keine domänen-spezifischen Annahmen
    - Skaliert mit neuen Domänen ohne Code-Änderungen
    """
    
    def __init__(self, model_loader: Any = None) -> None:
        self.model_loader = model_loader
        self.logger = logging.getLogger(__name__)
        
        # Performance-Cache
        self._entity_cache: Dict[str, EntityAnalysis] = {}
        self._consistency_cache: Dict[str, ConsistencyValidation] = {}
        
    def analyze_entities(self, query: str) -> EntityAnalysis:
        """
        Universelle Entitäts-Analyse für beliebige Queries.
        
        Args:
            query: User-Query zur Analyse
            
        Returns:
            EntityAnalysis mit erkannten Entitäten und Kontext
        """
        # Cache-Check
        if query in self._entity_cache:
            return self._entity_cache[query]
            
        try:
            analysis = self._llm_analyze_entities(query)
            
            # Cache Result
            self._entity_cache[query] = analysis
            return analysis
            
        except Exception as e:
            self.logger.warning(f"LLM-Entitäts-Analyse fehlgeschlagen: {e}")
            return self._fallback_entity_analysis(query)
    
    def validate_consistency(self, sources: List[str], entity_analysis: EntityAnalysis) -> ConsistencyValidation:
        """
        Universelle Konsistenz-Validierung zwischen Query-Entitäten und Quellen.
        
        Args:
            sources: Liste von Quellen-Texten
            entity_analysis: Erkannte Entitäten aus Query
            
        Returns:
            ConsistencyValidation mit Konsistenz-Bewertung
        """
        cache_key = f"{len(sources)}_{hash(str(entity_analysis.primary_entities[:2]))}"
        
        if cache_key in self._consistency_cache:
            return self._consistency_cache[cache_key]
            
        try:
            validation = self._llm_validate_consistency(sources, entity_analysis)
            
            # Cache Result
            self._consistency_cache[cache_key] = validation
            return validation
            
        except Exception as e:
            self.logger.warning(f"LLM-Konsistenz-Validierung fehlgeschlagen: {e}")
            return self._fallback_consistency_validation(sources, entity_analysis)
    
    def _llm_analyze_entities(self, query: str) -> EntityAnalysis:
        """LLM-basierte universelle Entitäts-Analyse."""
        
        prompt = f"""
Analysiere diese Benutzer-Anfrage und erkenne ALLE relevanten Entitäten:

Query: "{query}"

Aufgaben:
1. Erkenne ALLE Entitäten (Firmen, Länder, Produkte, Personen, Orte, Zeiträume, etc.)
2. Klassifiziere Entitäts-Typen (company, country, product, person, location, time, etc.)
3. Bestimme primäre vs. sekundäre Entitäten
4. Erkenne Domänen-Kontext (transport, tech, healthcare, finance, etc.)
5. Bestimme geografischen und zeitlichen Scope
6. Verbessere Query für bessere Suche

Antworte nur in diesem JSON-Format:
{{
    "primary_entities": [
        {{"name": "Hauptentität", "type": "entity_type", "confidence": 0.9}},
        {{"name": "Weitere", "type": "entity_type", "confidence": 0.8}}
    ],
    "secondary_entities": [
        {{"name": "Erwähnte", "type": "entity_type", "confidence": 0.6}}
    ],
    "domain_context": "domäne oder null",
    "geographic_scope": "geografischer bereich oder null",
    "temporal_scope": "zeitlicher bereich oder null", 
    "enhanced_query": "verbesserte suchquery",
    "confidence_score": 0.8
}}

Beispiele:
- "SBB Passagierzahlen" → primary: [{{"name":"SBB","type":"company","confidence":0.9}}], domain: "transportation"
- "iPhone vs Samsung Verkäufe" → primary: [{{"name":"iPhone","type":"product"}}, {{"name":"Samsung","type":"company"}}], domain: "technology"
- "Zürich Immobilienpreise 2023" → primary: [{{"name":"Zürich","type":"city"}}, {{"name":"2023","type":"year"}}], domain: "real_estate"
"""

        if not self.model_loader or not hasattr(self.model_loader, 'get_response'):
            return self._fallback_entity_analysis(query)
            
        try:
            response = self.model_loader.get_response(prompt, max_tokens=400)
            
            # Parse JSON Response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                
                return EntityAnalysis(
                    primary_entities=data.get('primary_entities', []),
                    secondary_entities=data.get('secondary_entities', []),
                    domain_context=data.get('domain_context'),
                    geographic_scope=data.get('geographic_scope'),
                    temporal_scope=data.get('temporal_scope'),
                    enhanced_query=data.get('enhanced_query', query),
                    confidence_score=float(data.get('confidence_score', 0.5))
                )
                
        except Exception as e:
            self.logger.warning(f"LLM Entity Analysis parsing failed: {e}")
            
        return self._fallback_entity_analysis(query)
    
    def _llm_validate_consistency(self, sources: List[str], entity_analysis: EntityAnalysis) -> ConsistencyValidation:
        """LLM-basierte universelle Konsistenz-Validierung."""
        
        # Nur erste paar Quellen für Performance
        sources_sample = sources[:3]
        sources_text = "\n".join([f"Quelle {i+1}: {src[:200]}..." for i, src in enumerate(sources_sample)])
        
        primary_entities_text = ", ".join([e.get('name', '') for e in entity_analysis.primary_entities])
        domain_context = entity_analysis.domain_context or "unbekannt"
        
        prompt = f"""
Validiere diese Quellen gegen die gewünschten Entitäten:

GEWÜNSCHTE ENTITÄTEN:
Primäre Entitäten: {primary_entities_text}
Domänen-Kontext: {domain_context}
Geografischer Scope: {entity_analysis.geographic_scope or 'unbekannt'}
Zeitlicher Scope: {entity_analysis.temporal_scope or 'unbekannt'}

QUELLEN ZU PRÜFEN:
{sources_text}

Aufgaben:
1. Sind die Quellen konsistent mit den gewünschten Entitäten?
2. Welche Entitäten werden in den Quellen erwähnt?
3. Gibt es Entitäts-Konflikte? (verschiedene Firmen, Länder, Produkte, etc.)
4. Wie schwerwiegend sind die Konflikte?
5. Relevanz-Score der Quellen (0.0-1.0)

Antworte nur in diesem JSON-Format:
{{
    "is_consistent": true/false,
    "entity_matches": [
        {{"name": "Entität", "type": "type", "source_mentions": 2}}
    ],
    "entity_conflicts": [
        {{"query_entity": "Entität1", "source_entity": "Entität2", "conflict_type": "type"}}
    ],
    "relevance_score": 0.8,
    "conflict_severity": "none/minor/major/critical",
    "warning_message": "Warnung oder null",
    "recommendations": ["Empfehlung1", "Empfehlung2"]
}}

Konflik-Kategorien:
- "none": Perfekte Übereinstimmung
- "minor": Verwandte Entitäten (z.B. Apple vs iPhone)
- "major": Verschiedene Entitäten gleicher Kategorie (z.B. SBB vs Deutsche Bahn)
- "critical": Völlig verschiedene Domänen (z.B. Transport vs Healthcare)
"""

        if not self.model_loader or not hasattr(self.model_loader, 'get_response'):
            return self._fallback_consistency_validation(sources, entity_analysis)
            
        try:
            response = self.model_loader.get_response(prompt, max_tokens=400)
            
            # Parse JSON Response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                
                return ConsistencyValidation(
                    is_consistent=bool(data.get('is_consistent', True)),
                    entity_matches=data.get('entity_matches', []),
                    entity_conflicts=data.get('entity_conflicts', []),
                    relevance_score=float(data.get('relevance_score', 0.5)),
                    conflict_severity=data.get('conflict_severity', 'none'),
                    warning_message=data.get('warning_message'),
                    recommendations=data.get('recommendations', [])
                )
                
        except Exception as e:
            self.logger.warning(f"LLM Consistency validation parsing failed: {e}")
            
        return self._fallback_consistency_validation(sources, entity_analysis)
    
    def _fallback_entity_analysis(self, query: str) -> EntityAnalysis:
        """Fallback-Entitäts-Analyse ohne LLM."""
        
        # Sehr einfache Keyword-basierte Erkennung als Fallback
        query_lower = query.lower()
        
        entities = []
        domain = None
        geo_scope = None
        temporal_scope = None
        
        # Basic entity detection (nur als Fallback)
        if any(term in query_lower for term in ['sbb', 'deutsche bahn', 'öbb', 'railways']):
            entities.append({"name": "Railway Company", "type": "company", "confidence": 0.3})
            domain = "transportation"
            
        if any(term in query_lower for term in ['schweiz', 'switzerland', 'deutschland', 'germany']):
            geo_scope = "Europe"
            
        if any(term in query_lower for term in ['2023', '2024', '2025', 'jahr', 'year']):
            temporal_scope = "recent"
        
        return EntityAnalysis(
            primary_entities=entities,
            secondary_entities=[],
            domain_context=domain,
            geographic_scope=geo_scope,
            temporal_scope=temporal_scope,
            enhanced_query=query,
            confidence_score=0.3  # Niedrig, da Fallback
        )
    
    def _fallback_consistency_validation(self, sources: List[str], entity_analysis: EntityAnalysis) -> ConsistencyValidation:
        """Fallback-Konsistenz-Validierung ohne LLM."""
        
        # Conservative Fallback: Alles als konsistent betrachten
        return ConsistencyValidation(
            is_consistent=True,
            entity_matches=[],
            entity_conflicts=[],
            relevance_score=0.5,
            conflict_severity="none",
            warning_message=None,
            recommendations=["LLM nicht verfügbar - manuelle Überprüfung empfohlen"]
        )
    
    def get_universal_validation_summary(self, query: str, sources: List[str]) -> Dict[str, Any]:
        """
        Vollständige universelle Entitäts-Validierung.
        
        Returns:
            Dict mit Entity-Analysis, Consistency-Validation und Recommendations
        """
        # Analysiere Entitäten
        entity_analysis = self.analyze_entities(query)
        
        # Validiere Konsistenz
        consistency_validation = self.validate_consistency(sources, entity_analysis)
        
        # Generiere Zusammenfassung
        summary = {
            'query': query,
            'entity_analysis': {
                'primary_entities': entity_analysis.primary_entities,
                'secondary_entities': entity_analysis.secondary_entities,
                'domain_context': entity_analysis.domain_context,
                'geographic_scope': entity_analysis.geographic_scope,
                'temporal_scope': entity_analysis.temporal_scope,
                'enhanced_query': entity_analysis.enhanced_query,
                'confidence': entity_analysis.confidence_score
            },
            'consistency_validation': {
                'is_consistent': consistency_validation.is_consistent,
                'entity_matches': consistency_validation.entity_matches,
                'entity_conflicts': consistency_validation.entity_conflicts,
                'relevance_score': consistency_validation.relevance_score,
                'conflict_severity': consistency_validation.conflict_severity,
                'warning': consistency_validation.warning_message
            },
            'recommendations': consistency_validation.recommendations,
            'universal_applicability': True,  # Marker für domänen-agnostische Lösung
            'supported_domains': ['any']  # Funktioniert für beliebige Domänen
        }
        
        return summary
    
    def enhance_query_with_entities(self, query: str, entity_analysis: EntityAnalysis) -> str:
        """
        Verbessert Query basierend auf erkannten Entitäten.
        
        Args:
            query: Original Query
            entity_analysis: Erkannte Entitäts-Informationen
            
        Returns:
            Enhanced Query mit Entitäts-Kontext
        """
        return entity_analysis.enhanced_query
    
    def close(self):
        """Cleanup-Methode."""
        # Clear Caches
        self._entity_cache.clear()
        self._consistency_cache.clear()
        
        self.logger.info("UniversalEntityValidator cleaned up")
