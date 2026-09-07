"""
Source Processor Module

Verarbeitet, rankt und scored Quellen für Evidence-Selection.
Wiederverwendbar in RAG-Pipeline, Evidence-Manager, Web-Search.
"""
from typing import List, Dict, Tuple, Any
import re
import logging
from agent.agent_types import Source

logger = logging.getLogger(__name__)


class SourceProcessor:
    """Verarbeitet, rankt und validiert Quellen."""
    
    def __init__(self) -> None:
        """Initialisiert den Source Processor."""
        # Domain Authority Scores (Wikipedia, Gov, Edu = höher)
        self.domain_authority: Dict[str, float] = {
            'wikipedia.org': 0.15,
            'britannica.com': 0.12,
            '.gov': 0.10,
            '.edu': 0.08,
            'arxiv.org': 0.08,
            'nature.com': 0.10,
            'sciencedirect.com': 0.08,
        }
        logger.info("SourceProcessor initialisiert")
    
    def rank_sources(self, query: str, sources: List[Source]) -> List[Tuple[Source, float]]:
        """
        Rankt Quellen nach Relevanz für Query.
        
        Args:
            query: User Query
            sources: Liste von Quellen
            
        Returns:
            Liste von (Source, Score) Tupeln, sortiert nach Score (absteigend)
        """
        if not sources:
            return []
        
        scored = [(src, self.score_source(query, src)) for src in sources]
        scored.sort(key=lambda x: x[1], reverse=True)
        
        logger.debug(f"Ranked {len(sources)} sources (top score: {scored[0][1]:.3f})")
        return scored
    
    def score_source(self, query: str, source: Source) -> float:
        """
        Berechnet Relevanz-Score für einzelne Quelle.
        
        Kombiniert:
        - Token-Overlap zwischen Query und Source
        - Domain Authority
        
        Args:
            query: User Query
            source: Quelle
            
        Returns:
            Relevanz-Score (0.0 - 1.0)
        """
        q_tokens = self._tokenize(query.lower())
        
        # Kombiniere Title + Snippet für bessere Abdeckung
        source_text = f"{source.title or ''} {source.snippet or ''}".lower()
        s_tokens = self._tokenize(source_text)
        
        # Base Score: Token-Overlap
        base_score = self._overlap(q_tokens, s_tokens)
        
        # Boost durch Domain Authority
        authority_boost = self.get_domain_authority(source.url or "")
        
        # Kombinierter Score (max 1.0)
        final_score = min(1.0, base_score + authority_boost)
        
        return final_score
    
    def adaptive_top_k(self, n_ranked: int) -> int:
        """
        Berechnet adaptive K-Anzahl basierend auf verfügbaren Quellen.
        
        Heuristik:
        - Wenige Quellen (≤5): Nutze alle
        - Mittlere Quellen (6-10): Nutze 80%
        - Viele Quellen (>10): Nutze Top 6-8
        
        Args:
            n_ranked: Anzahl gerankte Quellen
            
        Returns:
            Optimale K-Anzahl
        """
        if n_ranked <= 5:
            return n_ranked
        elif n_ranked <= 10:
            return max(4, int(n_ranked * 0.8))
        else:
            return min(8, max(6, int(n_ranked * 0.5)))
    
    def select_diverse_top_k(
        self,
        query: str,
        scored_sources: List[Tuple[Source, Any]],
        k: int,
        lambda_param: float = 0.7
    ) -> List[Source]:
        """
        MMR-basierte (Maximal Marginal Relevance) diversitäts-bewusste Auswahl.
        
        Balanciert Relevanz vs. Diversity:
        - lambda=1.0: Nur Relevanz (identisch zu Top-K)
        - lambda=0.5: 50% Relevanz, 50% Diversity
        - lambda=0.0: Nur Diversity (maximale Unterschiedlichkeit)
        
        Args:
            query: User Query
            scored_sources: Liste von (Source, Score) Tupeln
            k: Anzahl zu selektierende Quellen
            lambda_param: Relevanz-vs-Diversity Balance (0.0-1.0)
            
        Returns:
            Liste von Top-K diversen Quellen
        """
        if not scored_sources or k <= 0:
            return []
        
        if k >= len(scored_sources):
            return [src for src, _ in scored_sources]
        
        # Greedy MMR-Algorithmus
        selected: List[Source] = []
        remaining = list(scored_sources)
        
        # Start: Höchste Relevanz
        best_idx = 0
        selected.append(remaining[best_idx][0])
        remaining.pop(best_idx)
        
        # Iterativ: Balance Relevanz vs. Diversity
        while len(selected) < k and remaining:
            best_score = -float('inf')
            best_idx = 0
            
            for idx, (candidate, rel_score) in enumerate(remaining):
                # Relevanz-Komponente
                relevance = rel_score
                
                # Diversity-Komponente: Minimale Ähnlichkeit zu bereits selektierten
                max_similarity = 0.0
                candidate_tokens = self._tokenize(f"{candidate.title} {candidate.snippet}".lower())
                
                for selected_src in selected:
                    selected_tokens = self._tokenize(f"{selected_src.title} {selected_src.snippet}".lower())
                    similarity = self._overlap(candidate_tokens, selected_tokens)
                    max_similarity = max(max_similarity, similarity)
                
                diversity = 1.0 - max_similarity
                
                # MMR Score
                mmr_score = lambda_param * relevance + (1 - lambda_param) * diversity
                
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = idx
            
            selected.append(remaining[best_idx][0])
            remaining.pop(best_idx)
        
        logger.debug(f"MMR selected {len(selected)} diverse sources (lambda={lambda_param})")
        return selected
    
    def get_domain_authority(self, url: str) -> float:
        """
        Berechnet Domain Authority Score.
        
        Wikipedia, Gov, Edu = höhere Authority.
        
        Args:
            url: URL der Quelle
            
        Returns:
            Authority Score (0.0 - 0.15)
        """
        if not url:
            return 0.0
        
        url_lower = url.lower()
        
        # Exakte Domain-Matches
        for domain, score in self.domain_authority.items():
            if domain in url_lower:
                return score
        
        return 0.0
    
    def get_domain(self, url: str) -> str:
        """
        Extrahiert Domain aus URL.
        
        Args:
            url: URL
            
        Returns:
            Domain (z.B. "example.com")
        """
        if not url:
            return ""
        
        # Entferne Protokoll
        domain = re.sub(r'^https?://(www\.)?', '', url)
        # Entferne Pfad
        domain = domain.split('/')[0]
        
        return domain
    
    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenisiert Text in Wörter.
        
        Args:
            text: Zu tokenisierender Text
            
        Returns:
            Liste von Tokens (lowercase, alphanumerisch)
        """
        # Nur alphanumerische Zeichen + Umlaute
        tokens = re.findall(r'\w+', text.lower())
        return tokens
    
    def _overlap(self, tokens1: List[str], tokens2: List[str]) -> float:
        """
        Berechnet Token-Overlap (Jaccard-ähnlich).
        
        Args:
            tokens1: Token-Set 1
            tokens2: Token-Set 2
            
        Returns:
            Overlap-Score (0.0 - 1.0)
        """
        if not tokens1 or not tokens2:
            return 0.0
        
        set1 = set(tokens1)
        set2 = set(tokens2)
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        if union == 0:
            return 0.0
        
        return intersection / union
