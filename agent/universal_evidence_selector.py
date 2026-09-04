"""
Universelle LLM-basierte Evidence-Selection für alle Domänen.

Diese Klasse ersetzt regel-basierte Heuristiken durch semantische LLM-Bewertung
und funktioniert domänen-agnostisch für alle Arten von Fragen.
"""

from __future__ import annotations
from typing import List, Tuple, Dict, Any
import logging
import json
import re
from urllib.parse import urlparse

from agent.agent_types import Source

logger = logging.getLogger(__name__)


class UniversalEvidenceSelector:
    """
    Generische LLM-basierte Evidence-Selection für alle Domänen.
    
    Ersetzt regel-basierte _judge_relevance_rule() durch semantische LLM-Bewertung.
    Funktioniert universell für Ukraine, SBB, Technik, Wissenschaft, etc.
    """
    
    def __init__(self, model_loader):
        """
        Args:
            model_loader: ModelLoader Instanz für LLM-Calls
        """
        self.model_loader = model_loader
        self.logger = logger

    def select_evidence(
        self, 
        query: str, 
        candidates: List[Source], 
        max_candidates: int = 20
    ) -> List[Tuple[Source, float]]:
        """
        Universelle Evidence-Selection mit LLM.
        
        Args:
            query: Die Benutzer-Frage
            candidates: Liste von Quellen-Kandidaten
            max_candidates: Maximale Anzahl für LLM-Bewertung (Token-Limit)
            
        Returns:
            Liste von (Source, relevance_score) Tupeln, sortiert nach Relevanz
        """
        if not candidates:
            return []
        
        # Begrenze Kandidaten wegen Token-Limits
        limited = candidates[:max_candidates]
        
        try:
            # **KRITISCHE VERBESSERUNG**: Bei zeitkritischen Fragen Web-Quellen absolut bevorzugen
            # Aber: Akademische/wissenschaftliche Kontexte neutralisieren time-critical Signale
            # "neueste wissenschaftliche Erkenntnisse" ≠ "neueste Nachrichten"
            query_lower = query.lower()
            
            time_critical_keywords = [
                "heute", "aktuell", "news", "nachrichten",
                "jetzt", "momentan", "derzeit", "kürzlich",
                "neueste",  # nur time-critical wenn KEIN akademischer Kontext
            ]
            academic_neutralizers = [
                "wissenschaft", "forschung", "studie", "literatur",
                "erkenntnisse", "publikation", "modell", "theorie",
                "vergleich", "analyse", "review", "methode",
            ]
            
            has_time_keyword = any(kw in query_lower for kw in time_critical_keywords)
            has_academic_context = any(kw in query_lower for kw in academic_neutralizers)
            
            # Akademischer Kontext neutralisiert time-critical Signale
            # "neueste Forschung" → nicht zeitkritisch (RAG + Web gleich wertvoll)
            # "neueste Nachrichten" → zeitkritisch (Web bevorzugt)
            is_time_critical = has_time_keyword and not has_academic_context
            
            if has_time_keyword and has_academic_context:
                self.logger.info(
                    f"Zeitkritisches Keyword erkannt, aber akademischer Kontext "
                    f"neutralisiert → NICHT zeitkritisch behandelt"
                )
            
            if is_time_critical:
                # Separate Web- und lokale Quellen
                web_sources = []
                local_sources = []
                
                for src in limited:
                    source_type = self._classify_source_type(src)
                    if source_type == "Web":
                        web_sources.append(src)
                    else:
                        local_sources.append(src)
                
                self.logger.info(f"Zeitkritische Frage erkannt! Web-Quellen: {len(web_sources)}, Lokale: {len(local_sources)}")
                
                # Bei zeitkritischen Fragen: ZUERST nur Web-Quellen bewerten
                if web_sources:
                    self.logger.info("Bevorzuge Web-Quellen für zeitkritische Anfrage")
                    web_scores = self._llm_evaluate_relevance(query, web_sources)
                    
                    # Web-Ergebnisse mit Boost
                    scored = []
                    for src, score in zip(web_sources, web_scores):
                        if isinstance(score, (int, float)) and 0 <= score <= 10:
                            # Web-Quellen bekommen automatisch einen starken Boost
                            boosted_score = min(1.0, (float(score) / 10.0) + 0.4)
                            scored.append((src, boosted_score))
                    
                    # Nur wenn Web-Ergebnisse unzureichend sind (alle unter 0.6), auch lokale hinzufügen
                    best_web_score = max([s[1] for s in scored], default=0.0)
                    if best_web_score < 0.6 and local_sources:
                        self.logger.info("Web-Ergebnisse unzureichend, füge lokale Quellen hinzu")
                        local_scores = self._llm_evaluate_relevance(query, local_sources[:5])  # Nur wenige lokale
                        for src, score in zip(local_sources[:5], local_scores):
                            if isinstance(score, (int, float)) and 0 <= score <= 10:
                                # Lokale Quellen bekommen einen Malus
                                penalized_score = max(0.0, (float(score) / 10.0) - 0.3)
                                scored.append((src, penalized_score))
                else:
                    # Keine Web-Quellen verfügbar, nutze lokale mit Warnung
                    self.logger.warning("Keine Web-Quellen für zeitkritische Frage verfügbar!")
                    scores = self._llm_evaluate_relevance(query, limited)
                    scored = []
                    for src, score in zip(limited, scores):
                        if isinstance(score, (int, float)) and 0 <= score <= 10:
                            normalized_score = float(score) / 10.0
                            scored.append((src, normalized_score))
            else:
                # Normale Bewertung für nicht-zeitkritische Fragen
                scores = self._llm_evaluate_relevance(query, limited)
                scored = []
                for src, score in zip(limited, scores):
                    if isinstance(score, (int, float)) and 0 <= score <= 10:
                        normalized_score = float(score) / 10.0
                        scored.append((src, normalized_score))
            
            # Sortiere nach Relevanz (höchste zuerst)
            scored.sort(key=lambda x: x[1], reverse=True)
            
            # Debug-Ausgabe für bessere Transparenz
            if scored:
                self.logger.info(f"Evidence Selection - Top 3 (zeitkritisch={is_time_critical}):")
                for i, (src, score) in enumerate(scored[:3]):
                    source_type = self._classify_source_type(src)
                    domain = self._extract_domain(src.url or "")
                    title = (src.title or "Kein Titel")[:50]
                    self.logger.info(f"  {i+1}. [{source_type}] {title} - Score: {score:.2f} ({domain})")
            
            # Zusätzliche Statistik für zeitkritische Fragen
            if is_time_critical and scored:
                web_count = sum(1 for src, _ in scored if self._classify_source_type(src) == "Web")
                local_count = len(scored) - web_count
                self.logger.info(f"Zeitkritische Auswahl: {web_count} Web-Quellen, {local_count} lokale Quellen")
            
            self.logger.info(
                f"Evidence Selection: {len(scored)} Quellen bewertet, "
                f"beste Score: {scored[0][1]:.2f}" if scored else "keine gültigen Scores"
            )
            
            return scored
            
        except Exception as e:
            self.logger.warning(f"LLM Evidence Selection fehlgeschlagen: {e}")
            # Fallback: Alle Kandidaten mit neutralem Score
            return [(src, 0.5) for src in limited]

    def _llm_evaluate_relevance(self, query: str, sources: List[Source]) -> List[float]:
        """
        LLM bewertet Relevanz jeder Quelle für die Frage.
        
        Args:
            query: Die Benutzer-Frage
            sources: Liste von Source-Objekten
            
        Returns:
            Liste von Relevanz-Scores (0-10) für jede Quelle
        """
        if not sources:
            return []
        
        # Erstelle kompakte Quellen-Beschreibung für LLM
        sources_text = self._format_sources_for_llm(sources)
        
        # Generischer LLM-Prompt (keine domänen-spezifischen Keywords)
        system_prompt = self._get_system_prompt()
        user_prompt = self._get_user_prompt(query, sources_text, len(sources))
        
        # LLM-Call mit niedriger Temperatur für Konsistenz
        response = self.model_loader.generate_response(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=200,
            temperature=0.1  # Niedrig für konsistente Bewertungen
        )
        
        # Parse JSON-Response
        scores = self._parse_llm_response(response, len(sources))
        
        return scores

    def _format_sources_for_llm(self, sources: List[Source]) -> str:
        """
        Formatiert Quellen kompakt für LLM-Input.
        
        Args:
            sources: Liste von Source-Objekten
            
        Returns:
            Formatierte String-Darstellung der Quellen
        """
        sources_text = ""
        
        for i, src in enumerate(sources, 1):
            # Kürze Titel und Snippet für Token-Effizienz
            title = (src.title or "Kein Titel")[:100]
            snippet = (src.snippet or "")[:200]  # Mehr Kontext für bessere Bewertung
            url_domain = self._extract_domain(src.url or "")
            
            # Erkenne Quellentyp für bessere LLM-Bewertung
            source_type = self._classify_source_type(src)
            date_info = f" ({src.date})" if src.date else ""
            
            sources_text += f"{i}. Titel: {title}\n"
            sources_text += f"   Quelle: {url_domain} [{source_type}]{date_info}\n"
            sources_text += f"   Inhalt: {snippet}\n\n"
        
        return sources_text.strip()
    
    def _classify_source_type(self, source: Source) -> str:
        """Klassifiziert den Typ einer Quelle für bessere LLM-Bewertung."""
        url = source.url or ""
        title = source.title or ""
        
        # Web-Quellen erkennen (erweiterte Heuristik)
        web_indicators = [
            'http://', 'https://', 'www.', '.com', '.de', '.org', '.net', '.gov', '.edu',
            'news', 'wikipedia', 'zeit.de', 'spiegel.de', 'faz.net', 'tagesschau.de',
            'sueddeutsche.de', 'welt.de', 'focus.de', 'n-tv.de', 'stern.de', 'dpa-',
            'reuters.com', 'bbc.com', 'cnn.com', 'guardian.com'
        ]
        
        if any(indicator in url.lower() for indicator in web_indicators):
            return "Web"
        
        # Auch über Title/Domain erkennen falls URL nicht aussagekräftig
        if any(indicator in title.lower() for indicator in ['news', 'nachrichten', 'zeitung', 'magazine']):
            return "Web"
        
        # RAG/lokale Quellen erkennen
        local_indicators = ['.pdf', 'localhost', 'internal://', 'file://', 'local:', 'rag_store']
        if any(indicator in url.lower() for indicator in local_indicators):
            return "Lokal"
        
        # Wenn URL leer oder sehr kurz, wahrscheinlich lokale Quelle
        if not url or len(url) < 10:
            return "Lokal"
        
        return "Unbekannt"

    def _get_system_prompt(self) -> str:
        """Generischer System-Prompt für Evidence-Selection."""
        return """Du bist ein Experte für Informationsrelevanz. Bewerte objektiv, wie relevant jede Quelle für die Beantwortung der gegebenen Frage ist.

Bewertungskriterien:
- 0-2: Völlig irrelevant, hilft nicht bei der Beantwortung
- 3-4: Wenig relevant, enthält nur entfernt verwandte Informationen  
- 5-6: Teilweise relevant, enthält nützliche aber nicht zentrale Informationen
- 7-8: Relevant, enthält wichtige Informationen zur Beantwortung
- 9-10: Hochrelevant, enthält direkt zur Beantwortung notwendige Informationen

KRITISCHE REGELN FÜR ZEITKRITISCHE FRAGEN:
- Bei Fragen nach "aktuell", "heute", "neueste", "News", "Nachrichten" sind Web-Quellen fast immer relevanter
- Lokale/PDF-Quellen enthalten meist veraltete Informationen und sollten niedriger bewertet werden
- Web-Quellen von Nachrichtenseiten sind bei Aktualitätsfragen hochrelevant (8-10 Punkte)
- RAG/lokale Quellen bei Aktualitätsfragen sind meist irrelevant (0-3 Punkte)

WICHTIGE PRINZIPIEN:
- Bewerte Web-Quellen und lokale Quellen nach ihrer tatsächlichen Relevanz
- Aktuelle Web-Informationen sind DEUTLICH relevanter als ältere lokale Daten bei zeitkritischen Fragen
- Die Aktualität der Information ist bei News/Nachrichten-Fragen entscheidend
- Bei zeitkritischen Fragen haben aktuelle Web-Quellen absoluten Vorrang
- PDF-Dokumente enthalten fast nie aktuelle Tages-News

Berücksichtige dabei:
- Semantische Übereinstimmung zwischen Frage und Quelleninhalt
- Aktualität der Information (für News-Fragen ist dies der wichtigste Faktor!)
- Vollständigkeit und Detailgrad der Information
- Vertrauenswürdigkeit und Autorität der Quelle

Antworte NUR mit einer JSON-Liste von Zahlen: [score1, score2, score3, ...]"""

    def _get_user_prompt(self, query: str, sources_text: str, num_sources: int) -> str:
        """Erstellt User-Prompt für spezifische Anfrage."""
        # Erkenne zeitkritische Fragen
        time_critical_keywords = ["heute", "aktuell", "neueste", "news", "nachrichten", "jetzt", "momentan", "derzeit", "kürzlich"]
        is_time_critical = any(keyword in query.lower() for keyword in time_critical_keywords)
        
        time_hint = ""
        if is_time_critical:
            time_hint = "\n⚠️ WICHTIG: Diese Frage betrifft aktuelle Informationen! Web-Quellen sind fast immer relevanter als lokale/PDF-Quellen bei zeitkritischen Fragen."
        
        return f"""Frage: {query}{time_hint}

Quellen:
{sources_text}

Bewerte die Relevanz jeder Quelle (1-{num_sources}) für diese Frage auf einer Skala von 0-10.

JSON:"""

    def _parse_llm_response(self, response: str, expected_length: int) -> List[float]:
        """
        Parst LLM-Response zu Relevanz-Scores.
        
        Args:
            response: LLM-Response String
            expected_length: Erwartete Anzahl von Scores
            
        Returns:
            Liste von Relevanz-Scores (0-10)
        """
        try:
            # Extrahiere JSON aus Response (robust gegen zusätzlichen Text)
            json_match = re.search(r'\[(.*?)\]', response, re.DOTALL)
            
            if json_match:
                json_str = f"[{json_match.group(1)}]"
                scores = json.loads(json_str)
                
                # Validiere Scores
                if len(scores) == expected_length:
                    valid_scores = []
                    for s in scores:
                        try:
                            score = float(s)
                            # Clamp zu gültigen Range
                            score = max(0.0, min(10.0, score))
                            valid_scores.append(score)
                        except (ValueError, TypeError):
                            valid_scores.append(5.0)  # Neutral fallback
                    
                    return valid_scores
            
        except (json.JSONDecodeError, ValueError) as e:
            self.logger.warning(f"JSON parsing fehlgeschlagen: {e}")
        
        # Fallback bei Parse-Fehlern: Neutrale Scores
        self.logger.warning("LLM Response parsing fehlgeschlagen, verwende neutrale Scores")
        return [5.0] * expected_length

    def _extract_domain(self, url: str) -> str:
        """
        Extrahiert kompakte Domain-Darstellung aus URL.
        
        Args:
            url: URL String
            
        Returns:
            Kompakte Domain-Darstellung
        """
        if not url:
            return "Unbekannte Quelle"
        
        try:
            parsed = urlparse(url)
            
            if parsed.scheme == "rag":
                # RAG-URLs: Zeige Dateiname
                if "/" in url:
                    filename = url.split("/")[-1]
                    # Entferne Dateiendung für Kompaktheit
                    name = filename.rsplit(".", 1)[0] if "." in filename else filename
                    return name[:40]
                else:
                    return url[:40]
            else:
                # Web-URLs: Zeige Domain
                domain = parsed.netloc.lower()
                if domain:
                    # Entferne www. Prefix
                    clean_domain = domain.replace("www.", "")
                    return clean_domain[:30]
                else:
                    return url[:30]
                    
        except Exception:
            return url[:30]


# Kompatibilitäts-Funktion für einfache Integration
def create_universal_evidence_selector(model_loader) -> UniversalEvidenceSelector:
    """Factory-Funktion für UniversalEvidenceSelector."""
    return UniversalEvidenceSelector(model_loader)

