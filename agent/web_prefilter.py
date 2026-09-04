"""
Web Prefilter Module

LLM-basierte Vorfilterung von Web-URLs vor dem Scraping.
Verhindert Zeitverschwendung durch irrelevante URLs.
"""
from typing import List, Dict, Any, Tuple, Callable, Optional
import json
import logging

logger = logging.getLogger(__name__)


class WebPrefilter:
    """Filtert Web-URLs vor dem Scraping (Hybrid: LLM + Keywords)."""
    
    def __init__(self, llm_callable: Callable[[str], str]):
        """
        Initialisiert den Web Prefilter.
        
        Args:
            llm_callable: Funktion für LLM-Calls (prompt) -> str
        """
        self.llm = llm_callable
        
        # Keyword-basierte Filter (Fallback)
        self.irrelevant_keywords = {
            'shop', 'buy', 'price', 'sale', 'cart', 'checkout',
            'login', 'signup', 'register', 'paywall', 'subscription',
            'facebook.com', 'twitter.com', 'instagram.com', 'tiktok.com',
            'pinterest.com', 'linkedin.com/jobs'
        }
        
        logger.info("WebPrefilter initialisiert (LLM-basiert mit Keyword-Fallback)")
    
    def prefilter_urls(
        self,
        query: str,
        web_urls: List[Dict[str, Any]],
        max_urls: int = 5,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Wrapper für orchestrator-Kompatibilität.
        
        Delegiert zu apply_hybrid_filter und konvertiert das Ergebnis
        zum erwarteten Format.
        
        Args:
            query: User-Query
            web_urls: Liste von Web-URLs mit {url, title, snippet}
            max_urls: Maximale Anzahl relevanter URLs
            timeout: Max Latenz für LLM-Call (Sekunden)
            
        Returns:
            Dict mit {relevant_urls, irrelevant_urls, reasoning}
        """
        if not web_urls:
            return {
                "relevant_urls": [],
                "irrelevant_urls": [],
                "reasoning": "Keine URLs zum Filtern"
            }
        
        # Nutze apply_hybrid_filter (primäre Methode)
        relevant, stats = self.apply_hybrid_filter(
            query=query,
            urls=web_urls,
            target_urls=max_urls,
            use_llm=True
        )
        
        # Berechne irrelevante URLs
        relevant_urls_set = {url.get('url') for url in relevant}
        irrelevant = [url for url in web_urls if url.get('url') not in relevant_urls_set]
        
        return {
            "relevant_urls": relevant,
            "irrelevant_urls": irrelevant,
            "reasoning": stats.get('reasoning', f"{len(relevant)}/{len(web_urls)} URLs relevant (Methode: {stats.get('method', 'unknown')})")
        }
    
    def apply_hybrid_filter(
        self,
        query: str,
        urls: List[Dict[str, Any]],
        target_urls: int = 5,
        use_llm: bool = True
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Hybrid-Filterung: LLM (primär) + Keywords (Fallback).
        
        Args:
            query: User Query
            urls: Liste von URL-Dicts (url, title, snippet)
            target_urls: Ziel-Anzahl relevanter URLs
            use_llm: Wenn False, nur Keyword-Filter
            
        Returns:
            Tuple of (relevant_urls, filter_stats)
        """
        if not urls:
            return [], {"total": 0, "relevant": 0, "method": "none"}
        
        logger.info(f"🎯 Web-Prefilter: {len(urls)} URLs für Query '{query[:50]}...'")
        
        # Strategie 1: LLM-basierte Filterung
        if use_llm:
            try:
                result = self.prefilter_urls_llm(query, urls, target_urls)
                relevant = result.get("relevant_urls", [])
                
                if relevant:
                    stats = {
                        "total": len(urls),
                        "relevant": len(relevant),
                        "irrelevant": len(result.get("irrelevant_urls", [])),
                        "method": "llm",
                        "reasoning": result.get("reasoning", "")
                    }
                    logger.info(f"   ✅ LLM-Filter: {len(relevant)}/{len(urls)} URLs relevant")
                    return relevant, stats
                
            except Exception as e:
                logger.warning(f"   ⚠️ LLM-Filter fehlgeschlagen: {e}, Fallback zu Keywords")
        
        # Strategie 2: Keyword-basierte Filterung (Fallback)
        relevant = self.prefilter_urls_keywords(urls)
        stats = {
            "total": len(urls),
            "relevant": len(relevant),
            "method": "keywords"
        }
        logger.info(f"   ✅ Keyword-Filter: {len(relevant)}/{len(urls)} URLs relevant")
        
        return relevant[:target_urls], stats
    
    def prefilter_urls_llm(
        self,
        query: str,
        urls: List[Dict[str, Any]],
        max_urls: int
    ) -> Dict[str, Any]:
        """
        LLM-basierte URL-Vorfilterung.
        
        Args:
            query: User Query
            urls: Liste von URL-Dicts
            max_urls: Max. Anzahl relevanter URLs
            
        Returns:
            Dict mit relevant_urls, irrelevant_urls, reasoning
        """
        if not urls:
            return {"relevant_urls": [], "irrelevant_urls": [], "reasoning": "Keine URLs"}
        
        # Build Prompt
        prompt = self._build_prefilter_prompt(query, urls, max_urls)
        
        # LLM Call
        try:
            response = self.llm(prompt)
            
            # Parse Response
            result = self._parse_llm_response(response)
            
            return result
            
        except Exception as e:
            logger.error(f"LLM-Prefilter fehlgeschlagen: {e}")
            raise
    
    def prefilter_urls_keywords(
        self,
        urls: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Keyword-basierte Filterung (Fallback).
        
        Entfernt URLs mit irrelevanten Keywords (Shop, Social Media, etc.).
        
        Args:
            urls: Liste von URL-Dicts
            
        Returns:
            Gefilterte URL-Liste
        """
        relevant = []
        
        for url_data in urls:
            url = url_data.get('url', '').lower()
            title = url_data.get('title', '').lower()
            snippet = url_data.get('snippet', '').lower()
            
            # Prüfe auf irrelevante Keywords
            text = f"{url} {title} {snippet}"
            
            is_irrelevant = any(kw in text for kw in self.irrelevant_keywords)
            
            if not is_irrelevant:
                relevant.append(url_data)
        
        return relevant
    
    def _build_prefilter_prompt(
        self,
        query: str,
        urls: List[Dict[str, Any]],
        max_urls: int
    ) -> str:
        """Baut Prompt für Pre-Filter LLM-Call."""
        
        # URLs formatieren
        urls_formatted = ""
        for i, url_data in enumerate(urls, 1):
            urls_formatted += f"{i}. URL: {url_data.get('url', 'N/A')}\n"
            urls_formatted += f"   Title: {url_data.get('title', 'N/A')}\n"
            snippet = url_data.get('snippet', 'N/A')
            if isinstance(snippet, str):
                snippet = snippet[:200]  # Limit
            urls_formatted += f"   Snippet: {snippet}...\n\n"
        
        prompt = f"""Du bist ein Web-Quellen-Filter.

USER QUERY:
{query}

WEB-ERGEBNISSE ({len(urls)} URLs):
{urls_formatted}

AUFGABE:
Bewerte jede URL nach RELEVANZ für die User-Query.

KRITERIEN für RELEVANTE URLs:
- Beantwortet direkt die User-Frage
- Enthält sachliche, hilfreiche Informationen
- Ist KEINE Werbung/Shopping/Social-Media

KRITERIEN für IRRELEVANTE URLs:
- Zu allgemein oder off-topic
- Werbung, Produktverkauf
- Social Media Posts
- Login-Walls oder Paywalls

AUSGABE (Python-Dict):
{{
  "relevant_urls": [
    {{"url": "...", "title": "...", "relevance_score": 0.9, "reason": "..."}},
    ...
  ],
  "irrelevant_urls": [
    {{"url": "...", "title": "...", "reason": "..."}},
    ...
  ],
  "reasoning": "X von Y URLs sind relevant weil..."
}}

Wähle maximal {max_urls} relevante URLs. Gib NUR das Dict zurück!
"""
        return prompt
    
    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Parsed LLM-Response für Prefilter."""
        
        try:
            # Extrahiere Dict aus Response
            response_clean = response.strip()
            
            # Finde JSON-Block
            if '{' in response_clean and '}' in response_clean:
                start = response_clean.find('{')
                end = response_clean.rfind('}') + 1
                json_str = response_clean[start:end]
                
                result: Dict[str, Any] = json.loads(json_str)
                return result
            
            logger.warning("Kein valides JSON in LLM-Response gefunden")
            return {"relevant_urls": [], "irrelevant_urls": [], "reasoning": "Parse-Fehler"}
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON-Parse-Fehler: {e}")
            return {"relevant_urls": [], "irrelevant_urls": [], "reasoning": "JSON-Fehler"}
