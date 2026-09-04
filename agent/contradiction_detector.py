"""
Contradiction Detection & Source Validation für AgentOrchestrator
==================================================================

Dieses Modul implementiert:
- Cross-Source Contradiction Detection
- Fact-Checking zwischen Evidences
- Temporal Validation (Aktualität)
- Source Reliability Tracking
- Conflict Resolution Strategies

Autor: Neue Komponente (2025-10-09)
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class Contradiction:
    """Repräsentiert einen erkannten Widerspruch zwischen zwei Quellen"""
    source_a: Any
    source_b: Any
    contradiction_type: str  # "factual", "temporal", "numerical"
    confidence: float  # 0.0-1.0
    description: str
    suggested_resolution: str
    timestamp: Optional[datetime] = None
    
    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


@dataclass
class SourceReliability:
    """Tracking der Zuverlässigkeit einer Quelle"""
    domain: str
    reliability_score: float  # 0.0-1.0
    contradiction_count: int = 0
    validation_count: int = 0
    last_updated: Optional[datetime] = None
    
    def __post_init__(self) -> None:
        if self.last_updated is None:
            self.last_updated = datetime.now(timezone.utc)


class ContradictionDetector:
    """Erkennt Widersprüche und bewertet Quellen-Zuverlässigkeit"""
    
    def __init__(self, llm_callable: Optional[Callable[..., Any]] = None) -> None:
        """
        Args:
            llm_callable: Optional LLM für semantische Contradiction-Detection
        """
        self.llm = llm_callable
        self.reliability_db: Dict[str, SourceReliability] = {}
        logger.info("✅ ContradictionDetector initialisiert")
    
    # ==================== HAUPTFUNKTIONEN ====================
    
    def detect_contradictions(
        self,
        evidences: List[Any],
        query: str,
        use_llm: bool = True
    ) -> List[Contradiction]:
        """
        Erkennt Widersprüche zwischen Evidence-Quellen
        
        Args:
            evidences: Liste von Evidence-Objekten
            query: Original-Query (für Kontext)
            use_llm: Ob LLM für semantische Prüfung verwendet werden soll
        
        Returns:
            Liste erkannter Widersprüche
        """
        contradictions: List[Contradiction] = []
        if len(evidences) < 2:
            logger.info("[CONTRADICTION] Zu wenige Quellen für Vergleich")
            return []
        
        logger.info(f"[CONTRADICTION] Prüfe {len(evidences)} Evidences auf Widersprüche")
        
        # Paarweise Vergleiche
        for i, ev_a in enumerate(evidences):
            for ev_b in evidences[i+1:]:
                # Rule-based Checks
                rule_contradictions = self._check_rule_based_contradictions(ev_a, ev_b, query)
                contradictions.extend(rule_contradictions)
                
                # LLM-based Check (optional, teuer)
                if use_llm and self.llm:
                    llm_contradiction = self._check_llm_contradiction(ev_a, ev_b, query)
                    if llm_contradiction:
                        contradictions.append(llm_contradiction)
        
        logger.info(f"[CONTRADICTION] {len(contradictions)} Widersprüche erkannt")
        return contradictions
    
    def resolve_contradictions(
        self,
        contradictions: List[Contradiction],
        evidences: List[Any]
    ) -> Tuple[List[Any], Dict[str, Any]]:
        """
        Löst Widersprüche und entfernt unzuverlässige Quellen
        
        Returns:
            (filtered_evidences, resolution_report)
        """
        if not contradictions:
            return evidences, {"resolved": 0, "removed": 0, "strategy": "no_conflicts"}
        
        logger.info(f"[RESOLUTION] Löse {len(contradictions)} Widersprüche auf")
        
        # Score-basierte Resolution
        removed_sources = set()
        resolution_details = []
        
        for contradiction in contradictions:
            winner, loser, reason = self._resolve_single_contradiction(contradiction)
            
            if loser and loser not in removed_sources:
                removed_sources.add(loser)
                resolution_details.append({
                    "removed": self._get_source_id(loser),
                    "kept": self._get_source_id(winner),
                    "reason": reason,
                    "type": contradiction.contradiction_type
                })
        
        # Filter Evidences
        filtered = [ev for ev in evidences if ev not in removed_sources]
        
        report = {
            "resolved": len(contradictions),
            "removed": len(removed_sources),
            "kept": len(filtered),
            "strategy": "authority_temporal_hybrid",
            "details": resolution_details
        }
        
        logger.info(f"[RESOLUTION] Entfernt: {len(removed_sources)}, Behalten: {len(filtered)}")
        return filtered, report
    
    # ==================== RULE-BASED DETECTION ====================
    
    def _check_rule_based_contradictions(
        self,
        ev_a: Any,
        ev_b: Any,
        query: str
    ) -> List[Contradiction]:
        """Rule-based Contradiction Detection"""
        contradictions = []
        
        # 1. Numerical Contradictions
        num_contradiction = self._check_numerical_contradiction(ev_a, ev_b)
        if num_contradiction:
            contradictions.append(num_contradiction)
        
        # 2. Temporal Contradictions
        temp_contradiction = self._check_temporal_contradiction(ev_a, ev_b)
        if temp_contradiction:
            contradictions.append(temp_contradiction)
        
        # 3. Boolean Contradictions (Yes/No)
        bool_contradiction = self._check_boolean_contradiction(ev_a, ev_b, query)
        if bool_contradiction:
            contradictions.append(bool_contradiction)
        
        return contradictions
    
    def _check_numerical_contradiction(self, ev_a: Any, ev_b: Any) -> Optional[Contradiction]:
        """Erkennt widersprüchliche Zahlenangaben"""
        content_a = self._get_content(ev_a).lower()
        content_b = self._get_content(ev_b).lower()
        
        # Extrahiere Zahlen
        numbers_a = self._extract_numbers(content_a)
        numbers_b = self._extract_numbers(content_b)
        
        if not numbers_a or not numbers_b:
            return None
        
        # Vergleiche Zahlen (>20% Differenz = Widerspruch)
        for num_a in numbers_a[:3]:  # Max 3 Zahlen prüfen
            for num_b in numbers_b[:3]:
                if num_a > 0 and abs(num_a - num_b) / num_a > 0.2:
                    return Contradiction(
                        source_a=ev_a,
                        source_b=ev_b,
                        contradiction_type="numerical",
                        confidence=0.7,
                        description=f"Zahlen-Widerspruch: {num_a} vs {num_b}",
                        suggested_resolution="authority_based"
                    )
        
        return None
    
    def _check_temporal_contradiction(self, ev_a: Any, ev_b: Any) -> Optional[Contradiction]:
        """Erkennt zeitliche Widersprüche (veraltete Informationen)"""
        date_a = self._extract_date(ev_a)
        date_b = self._extract_date(ev_b)
        
        if not date_a or not date_b:
            return None
        
        # Mehr als 2 Jahre Unterschied?
        year_diff = abs(date_a.year - date_b.year)
        
        if year_diff > 2:
            newer = ev_a if date_a > date_b else ev_b
            older = ev_b if date_a > date_b else ev_a
            
            return Contradiction(
                source_a=newer,
                source_b=older,
                contradiction_type="temporal",
                confidence=0.6,
                description=f"Zeitlicher Widerspruch: {year_diff} Jahre Differenz",
                suggested_resolution="prefer_newer"
            )
        
        return None
    
    def _check_boolean_contradiction(
        self,
        ev_a: Any,
        ev_b: Any,
        query: str
    ) -> Optional[Contradiction]:
        """Erkennt Ja/Nein-Widersprüche"""
        content_a = self._get_content(ev_a).lower()
        content_b = self._get_content(ev_b).lower()
        
        # Positive Indikatoren
        positive_a = any(word in content_a for word in ["ja", "yes", "korrekt", "wahr", "stimmt"])
        positive_b = any(word in content_b for word in ["ja", "yes", "korrekt", "wahr", "stimmt"])
        
        # Negative Indikatoren
        negative_a = any(word in content_a for word in ["nein", "no", "falsch", "nicht", "unmöglich"])
        negative_b = any(word in content_b for word in ["nein", "no", "falsch", "nicht", "unmöglich"])
        
        # Widerspruch wenn (A sagt Ja, B sagt Nein) oder vice versa
        if (positive_a and negative_b) or (negative_a and positive_b):
            return Contradiction(
                source_a=ev_a,
                source_b=ev_b,
                contradiction_type="factual",
                confidence=0.5,
                description="Boolean-Widerspruch: Gegensätzliche Aussagen",
                suggested_resolution="authority_based"
            )
        
        return None
    
    # ==================== LLM-BASED DETECTION ====================
    
    def _check_llm_contradiction(
        self,
        ev_a: Any,
        ev_b: Any,
        query: str
    ) -> Optional[Contradiction]:
        """LLM-basierte semantische Contradiction Detection"""
        if not self.llm:
            return None
        
        content_a = self._get_content(ev_a)[:500]
        content_b = self._get_content(ev_b)[:500]
        
        prompt = f"""Prüfe, ob die folgenden zwei Quellen sich widersprechen:

Query: {query}

Quelle A: {content_a}

Quelle B: {content_b}

Antwort NUR mit einem JSON:
{{"contradiction": true/false, "confidence": 0.0-1.0, "description": "kurze Erklärung"}}"""
        
        try:
            response = self.llm(prompt, max_tokens=100)
            
            # Parse JSON (simple Regex-basiert)
            if '"contradiction": true' in response.lower():
                confidence_match = re.search(r'"confidence":\s*(0\.\d+|1\.0)', response)
                confidence = float(confidence_match.group(1)) if confidence_match else 0.5
                
                desc_match = re.search(r'"description":\s*"([^"]+)"', response)
                description = desc_match.group(1) if desc_match else "LLM-erkannter Widerspruch"
                
                return Contradiction(
                    source_a=ev_a,
                    source_b=ev_b,
                    contradiction_type="semantic",
                    confidence=confidence,
                    description=description,
                    suggested_resolution="authority_based"
                )
        
        except Exception as e:
            logger.warning(f"[LLM-CONTRADICTION] Fehler: {e}")
        
        return None
    
    # ==================== CONFLICT RESOLUTION ====================
    
    def _resolve_single_contradiction(
        self,
        contradiction: Contradiction
    ) -> Tuple[Any, Any, str]:
        """
        Löst einen einzelnen Widerspruch auf
        
        Returns:
            (winner_source, loser_source, reason)
        """
        ev_a = contradiction.source_a
        ev_b = contradiction.source_b
        
        # Strategie 1: Temporal (neuere Quelle gewinnt)
        if contradiction.contradiction_type == "temporal":
            date_a = self._extract_date(ev_a)
            date_b = self._extract_date(ev_b)
            
            if date_a and date_b:
                if date_a > date_b:
                    return ev_a, ev_b, "newer_source"
                else:
                    return ev_b, ev_a, "newer_source"
        
        # Strategie 2: Authority-based
        score_a = self._get_authority_score(ev_a)
        score_b = self._get_authority_score(ev_b)
        
        if score_a > score_b:
            return ev_a, ev_b, f"higher_authority ({score_a:.2f} > {score_b:.2f})"
        elif score_b > score_a:
            return ev_b, ev_a, f"higher_authority ({score_b:.2f} > {score_a:.2f})"
        
        # Strategie 3: Reliability-Tracking
        reliability_a = self._get_reliability(ev_a)
        reliability_b = self._get_reliability(ev_b)
        
        if reliability_a > reliability_b:
            return ev_a, ev_b, f"higher_reliability ({reliability_a:.2f})"
        else:
            return ev_b, ev_a, f"higher_reliability ({reliability_b:.2f})"
    
    # ==================== HELPER FUNCTIONS ====================
    
    def _get_content(self, evidence: Any) -> str:
        """Extrahiert Content aus Evidence"""
        return getattr(evidence, 'content', '') or getattr(evidence, 'text', '') or getattr(evidence, 'snippet', '')
    
    def _get_source_id(self, evidence: Any) -> str:
        """Generiert eindeutige ID für Evidence"""
        url = getattr(evidence, 'url', '')
        source = getattr(evidence, 'source', '')
        return url or source or str(id(evidence))
    
    def _extract_numbers(self, text: str) -> List[float]:
        """Extrahiert Zahlen aus Text"""
        # Finde alle Zahlen (inkl. Dezimalzahlen)
        pattern = r'\b\d+(?:\.\d+)?\b'
        matches = re.findall(pattern, text)
        return [float(m) for m in matches]
    
    def _extract_date(self, evidence: Any) -> Optional[datetime]:
        """Extrahiert Datum aus Evidence"""
        # Prüfe date-Attribut
        date_attr = getattr(evidence, 'date', None)
        if date_attr:
            if isinstance(date_attr, datetime):
                # ✅ Force timezone-aware to enable consistent comparisons
                if date_attr.tzinfo is None:
                    return date_attr.replace(tzinfo=timezone.utc)
                return date_attr
            if isinstance(date_attr, str):
                # Parse String (z.B. "2023-10-09")
                try:
                    parsed = datetime.strptime(date_attr[:10], "%Y-%m-%d")
                    return parsed.replace(tzinfo=timezone.utc)
                except ValueError as exc:
                    logger.debug(f"Date parse failed for '{date_attr}': {exc}")
        
        # Prüfe timestamp-Attribut
        timestamp = getattr(evidence, 'timestamp', None)
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                return timestamp.replace(tzinfo=timezone.utc)
            return timestamp
        
        return None
    
    def _get_authority_score(self, evidence: Any) -> float:
        """Holt Authority-Score für Evidence"""
        url = getattr(evidence, 'url', '')
        score = getattr(evidence, 'score', 0.5)
        
        # Domain-Authority Bonus
        high_authority_domains = [
            "wikipedia.org", ".gov", ".edu", "nature.com",
            "sciencedirect.com", "arxiv.org"
        ]
        
        for domain in high_authority_domains:
            if domain in url.lower():
                return min(score + 0.3, 1.0)
        
        return score
    
    def _get_reliability(self, evidence: Any) -> float:
        """Holt oder berechnet Reliability-Score"""
        domain = self._extract_domain(evidence)
        
        if domain in self.reliability_db:
            rel = self.reliability_db[domain]
            # Berechne Score basierend auf Historie
            if rel.validation_count > 0:
                success_rate = 1.0 - (rel.contradiction_count / rel.validation_count)
                return success_rate
        
        # Default Reliability
        return 0.5
    
    def _extract_domain(self, evidence: Any) -> str:
        """Extrahiert Domain aus Evidence"""
        url = getattr(evidence, 'url', '')
        domain = getattr(evidence, 'domain', '')
        
        if domain:
            return domain
        
        if url and "://" in url:
            try:
                domain_part = url.split("://")[1].split("/")[0]
                parts = domain_part.split(".")
                if len(parts) >= 2:
                    return ".".join(parts[-2:])
                return domain_part
            except (IndexError, AttributeError) as exc:
                logger.debug(f"Domain extraction failed for '{url}': {exc}")
        
        return "unknown"
    
    def update_reliability(self, evidence: Any, had_contradiction: bool) -> None:
        """Aktualisiert Reliability-Tracking für eine Quelle"""
        domain = self._extract_domain(evidence)
        
        if domain not in self.reliability_db:
            self.reliability_db[domain] = SourceReliability(
                domain=domain,
                reliability_score=0.5
            )
        
        rel = self.reliability_db[domain]
        rel.validation_count += 1
        
        if had_contradiction:
            rel.contradiction_count += 1
        
        # Update Score
        rel.reliability_score = 1.0 - (rel.contradiction_count / rel.validation_count)
        rel.last_updated = datetime.now()
        
        logger.debug(f"[RELIABILITY] {domain}: {rel.reliability_score:.2f} ({rel.validation_count} validations)")
