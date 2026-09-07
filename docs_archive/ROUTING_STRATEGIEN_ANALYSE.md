# Analyse: Intelligente Routing-Strategien für Web-Search vs. RAG

## Problem
Das aktuelle System verwendet keyword-basierte Trigger für Websuche, was bei neuen Fragetypen versagt. Benötigt wird ein intelligentes, LLM-gesteuertes Routing-System.

## Lösungsansätze

### 1. **LLM-basierte Reflexion (Empfohlen)**

**Konzept**: Das LLM analysiert zunächst die Frage und entscheidet selbst, welche Informationsquellen benötigt werden.

**Implementation**:
```python
def analyze_information_needs(self, query: str) -> Dict[str, Any]:
    """LLM analysiert Informationsbedarf der Anfrage"""
    
    analysis_prompt = f"""
Analysiere diese Benutzeranfrage und bestimme die optimale Informationsstrategie:

ANFRAGE: "{query}"

Bewerte folgende Aspekte:
1. ZEITLICHKEIT: Sind aktuelle/neueste Informationen erforderlich?
2. LOKALITÄT: Sind lokale Dokumente wahrscheinlich ausreichend?
3. SPEZIALWISSEN: Benötigt es Expertenwissen aus dem Internet?
4. ALLGEMEINWISSEN: Kann das mit Grundwissen beantwortet werden?

Antworte im JSON-Format:
{{
    "needs_web_search": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "Begründung",
    "search_strategy": "web_only|rag_only|hybrid",
    "suggested_query": "optimierte Suchquery falls web_search=true"
}}
"""
    
    response = self.model.generate(analysis_prompt)
    return json.loads(response)
```

**Vorteile**:
- ✅ Vollständig dynamisch, keine Keywords
- ✅ Nutzt LLM-Intelligenz für Kontextverständnis
- ✅ Kann komplexe Entscheidungen treffen
- ✅ Selbstlernend durch Erfahrung

**Nachteile**:
- ⚠️ Zusätzlicher LLM-Call (Latenz)
- ⚠️ Möglicherweise inconsistent

### 2. **Embeddings-basierte Ähnlichkeitsanalyse**

**Konzept**: Vergleiche die Anfrage mit bekannten Kategorien von Fragen via Embeddings.

**Implementation**:
```python
class IntelligentRouter:
    def __init__(self):
        # Vordefinierte Kategorien mit Beispielen
        self.categories = {
            "current_events": {
                "examples": ["aktuelle Nachrichten", "neueste Entwicklungen", "was passiert heute"],
                "route": "web_search"
            },
            "salary_market": {
                "examples": ["Gehalt", "verdienen", "Lohn", "Bezahlung"],
                "route": "web_search"
            },
            "document_analysis": {
                "examples": ["analysiere Dokument", "was steht in", "erkläre Inhalt"],
                "route": "rag_only"
            }
        }
        self._compute_category_embeddings()
    
    def route_query(self, query: str) -> str:
        query_embedding = self.embed_text(query)
        similarities = {}
        
        for category, data in self.categories.items():
            category_embedding = data["embedding"]
            similarity = cosine_similarity(query_embedding, category_embedding)
            similarities[category] = similarity
        
        best_category = max(similarities.items(), key=lambda x: x[1])
        if best_category[1] > 0.7:  # Threshold
            return self.categories[best_category[0]]["route"]
        
        return "hybrid"  # Fallback
```

**Vorteile**:
- ✅ Schnell (keine extra LLM-Calls)
- ✅ Semantisches Verständnis
- ✅ Erweiterbar durch neue Beispiele

**Nachteile**:
- ⚠️ Benötigt Training/Kalibrierung
- ⚠️ Statische Kategorien
- ⚠️ Schwierig für Edge Cases

### 3. **Zwei-Stufen-Ansatz: Quick Check + Deep Analysis**

**Konzept**: Erste schnelle Heuristik, bei Unsicherheit detaillierte LLM-Analyse.

**Implementation**:
```python
def intelligent_routing(self, query: str) -> RoutingDecision:
    # Stufe 1: Schnelle Heuristiken
    quick_decision = self._quick_heuristics(query)
    
    if quick_decision.confidence > 0.8:
        return quick_decision
    
    # Stufe 2: LLM-basierte Tiefenanalyse
    return self._llm_deep_analysis(query)

def _quick_heuristics(self, query: str) -> RoutingDecision:
    """Schnelle regelbasierte Checks"""
    
    # Zeitindikatoren
    time_indicators = ["heute", "aktuell", "neueste", "2024", "2025", "letzte Woche"]
    if any(indicator in query.lower() for indicator in time_indicators):
        return RoutingDecision("web_search", confidence=0.9, reason="Zeitindikator")
    
    # Dokumenten-Referenzen
    doc_indicators = ["dokument", "datei", "in der wissensbasis", "laut meinen unterlagen"]
    if any(indicator in query.lower() for indicator in doc_indicators):
        return RoutingDecision("rag_only", confidence=0.9, reason="Dokument-Referenz")
    
    # Unsicher - braucht tiefere Analyse
    return RoutingDecision("unknown", confidence=0.3, reason="Unklare Anfrage")
```

**Vorteile**:
- ✅ Optimiert für Geschwindigkeit UND Genauigkeit
- ✅ Klare Cases werden schnell erkannt
- ✅ Fallback für komplexe Fälle

**Nachteile**:
- ⚠️ Komplexere Implementation
- ⚠️ Wartung beider Systeme

### 4. **RAG-First mit Konfidenz-Bewertung**

**Konzept**: Versuche zuerst RAG, bewerte die Antwortqualität, bei schlechter Konfidenz → Websuche.

**Implementation**:
```python
def adaptive_search(self, query: str) -> str:
    # 1. RAG-Suche versuchen
    rag_results = self.rag_store.search(query, top_k=5)
    
    # 2. Konfidenz bewerten
    confidence_score = self._evaluate_rag_confidence(query, rag_results)
    
    if confidence_score > 0.7:
        # RAG-Ergebnisse sind gut genug
        return self._generate_from_rag(query, rag_results)
    
    # 3. Bei niedrigerer Konfidenz: Websuche
    web_results = self._web_search(query)
    
    # 4. Hybrid-Antwort aus beiden Quellen
    return self._generate_hybrid_response(query, rag_results, web_results)

def _evaluate_rag_confidence(self, query: str, results: List) -> float:
    """Bewertet wie gut RAG-Ergebnisse zur Anfrage passen"""
    
    if not results:
        return 0.0
    
    # Faktoren:
    # - Anzahl relevanter Ergebnisse
    # - Durchschnittlicher Similarity Score
    # - Inhaltliche Vollständigkeit (LLM-basiert)
    
    avg_score = sum(r.get('score', 0) for r in results) / len(results)
    coverage_score = self._assess_topic_coverage(query, results)
    
    return (avg_score + coverage_score) / 2
```

**Vorteile**:
- ✅ Nutzt lokale Ressourcen optimal
- ✅ Adaptive, selbstlernende Entscheidungen
- ✅ Keine falsch-positiven Websuchen

**Nachteile**:
- ⚠️ Latenz durch doppelte Suche
- ⚠️ Komplexe Konfidenz-Bewertung

### 5. **Meta-Learning Ansatz**

**Konzept**: System lernt aus Benutzer-Feedback, welche Routing-Entscheidungen gut waren.

**Implementation**:
```python
class LearningRouter:
    def __init__(self):
        self.decision_history = []
        self.feedback_scores = {}
    
    def route_with_learning(self, query: str) -> str:
        # Basis-Entscheidung treffen
        decision = self._make_routing_decision(query)
        
        # Entscheidung loggen
        decision_id = self._log_decision(query, decision)
        
        return decision, decision_id
    
    def record_feedback(self, decision_id: str, user_satisfaction: float):
        """Lerne aus Benutzer-Feedback"""
        self.feedback_scores[decision_id] = user_satisfaction
        
        # Aktualisiere Routing-Parameter basierend auf Feedback
        self._update_routing_weights()
    
    def _update_routing_weights(self):
        """Passt Routing-Parameter basierend auf historischen Erfolg an"""
        # Machine Learning zur Optimierung der Entscheidungsparameter
        pass
```

**Vorteile**:
- ✅ Kontinuierliche Verbesserung
- ✅ Anpassung an Nutzerverhalten
- ✅ Langfristig optimal

**Nachteile**:
- ⚠️ Komplexe Implementation
- ⚠️ Benötigt viel Trainingszeit
- ⚠️ Cold-start Problem

## Bewertungsmatrix

| Ansatz | Genauigkeit | Geschwindigkeit | Implementierungsaufwand | Wartbarkeit | Skalierbarkeit |
|--------|-------------|-----------------|-------------------------|-------------|----------------|
| LLM-Reflexion | 9/10 | 6/10 | 7/10 | 8/10 | 9/10 |
| Embeddings | 7/10 | 9/10 | 8/10 | 6/10 | 7/10 |
| Zwei-Stufen | 8/10 | 8/10 | 6/10 | 7/10 | 8/10 |
| RAG-First | 8/10 | 7/10 | 8/10 | 8/10 | 8/10 |
| Meta-Learning | 9/10 | 8/10 | 4/10 | 5/10 | 10/10 |

## **EMPFEHLUNG: LLM-basierte Reflexion**

**Begründung**:
1. **Höchste Genauigkeit**: LLM kann komplexe Kontexte verstehen
2. **Vollständig generisch**: Funktioniert für alle Fragetypen ohne Keywords
3. **Gute Skalierbarkeit**: Neue Fragetypen werden automatisch verstanden
4. **Moderate Komplexität**: Implementierung ist machbar
5. **Transparenz**: Reasoning ist nachvollziehbar

**Optimierungen**:
- Caching häufiger Entscheidungen
- Parallelisierung von Reflexion und RAG-Suche
- Fallback auf schnelle Heuristiken bei API-Problemen

**Implementation Plan**:
1. Reflexions-Prompt entwickeln und testen
2. JSON-Parser für strukturierte Antworten
3. Integration in bestehenden Chat-Flow
4. A/B-Testing gegen aktuelles System
5. Iterative Verbesserung basierend auf Logs
