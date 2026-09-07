#!/usr/bin/env python3
"""
DIAGRAMM-QUALITÄTS-VALIDATOR MIT VISION-LLM
===========================================

Validiert generierte Diagramme mit einem multimodalen LLM (Vision).
Prüft: Lesbarkeit, Korrektheit, Layout-Qualität
"""

import os
import base64
import logging
import math
from typing import Any, Dict, List, Optional, Tuple, Type
from PIL import Image, ImageStat
import io

# ✅ PHASE 1: Structured Output Imports
LLMStructuredWrapper: Optional[Type[Any]] = None
DiagramValidationOutput: Optional[Type[Any]] = None
try:
    from llm_structured_wrapper import LLMStructuredWrapper  # type: ignore[assignment,no-redef]
    from llm_output_schemas import DiagramValidationOutput  # type: ignore[assignment,no-redef]
    STRUCTURED_OUTPUTS_AVAILABLE = True
except ImportError:
    STRUCTURED_OUTPUTS_AVAILABLE = False

logger = logging.getLogger(__name__)


class DiagramQualityValidator:
    """
    🔍 Validiert Diagramme mit Vision-LLM (Multimodal)
    
    Nutzt model_loader.generate_response(image_path=..., prompt=...) für echte
    Vision-basierte Qualitätsprüfung. Kein base64 → der model_loader handhabt
    die file:// URL-Konvertierung für Llava15ChatHandler intern.
    
    Prüft generierte Diagramme auf:
    - Lesbarkeit der Beschriftungen
    - Korrektheit der Darstellung
    - Layout-Qualität (Überlappungen, Spacing)
    - Farbkontraste (WCAG 2.1)
    """
    
    def __init__(self, model_loader=None):
        """
        Args:
            model_loader: ModelLoader-Instanz mit generate_response(prompt, image_path, ...)
                         Muss multimodal fähig sein (is_multimodal=True)
        """
        self.model_loader = model_loader
        # Prüfe ob das Modell tatsächlich multimodal ist
        self.validation_enabled = (
            model_loader is not None
            and hasattr(model_loader, 'generate_response')
            and getattr(model_loader, 'is_multimodal', False)
        )
        
        if self.validation_enabled:
            logger.info("✅ Diagramm-Qualitäts-Validator aktiviert (Vision-LLM, multimodal)")
        elif model_loader is not None and not getattr(model_loader, 'is_multimodal', False):
            logger.warning("⚠️ Diagramm-Qualitäts-Validator DEAKTIVIERT (Modell ist nicht multimodal)")
        else:
            logger.warning("⚠️ Diagramm-Qualitäts-Validator DEAKTIVIERT (kein model_loader)")
    
    # ═══════════════════════════════════════════════════════════════════
    # PIXEL-LEVEL HEURISTIC CHECKS (kein LLM nötig)
    # ═══════════════════════════════════════════════════════════════════
    
    @staticmethod
    def _relative_luminance(r: int, g: int, b: int) -> float:
        """WCAG 2.1 relative Luminanz (sRGB linearisiert)"""
        def linearize(c_8bit: int) -> float:
            c = c_8bit / 255.0
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)
    
    @staticmethod
    def _contrast_ratio(lum1: float, lum2: float) -> float:
        """WCAG 2.1 Kontrastverhältnis"""
        lighter = max(lum1, lum2)
        darker = min(lum1, lum2)
        return (lighter + 0.05) / (darker + 0.05)
    
    def heuristic_check(self, image_path: str) -> Dict:
        """Schnelle Pixel-Level-Prüfung ohne LLM.
        
        Prüft:
        1. Mindest-Auflösung (>= 800x600)
        2. Nicht leer (Entropie > 1.0)
        3. Hintergrund-Dominanz (Background < 97%)
        4. Kontrast-Check (Varianz > Schwelle)
        
        Returns:
            {"pass": bool, "issues": List[str], "metrics": Dict}
        """
        issues: List[str] = []
        metrics: Dict[str, Any] = {}
        
        try:
            with Image.open(image_path) as img:
                img_rgb = img.convert("RGB")
                w, h = img_rgb.size
                metrics["width"] = w
                metrics["height"] = h
                metrics["pixels"] = w * h
                
                # 1. Mindest-Auflösung
                if w < 800 or h < 600:
                    issues.append(f"Niedrige Auflösung: {w}x{h} (Minimum: 800x600)")
                
                # 2. Entropie (nicht leer / nicht einfarbig)
                stat = ImageStat.Stat(img_rgb)
                # Varianz pro Kanal → Gesamt-Varianz
                variance = sum(v for v in stat.var) / 3.0
                metrics["mean_variance"] = round(variance, 2)
                if variance < 50:
                    issues.append(f"Bild scheint fast leer/einfarbig (Varianz={variance:.1f})")
                
                                # 3. Hintergrund-Dominanz (meistgenutzte Farbe)
                # Sample 10000 Pixel (performant)
                pixels = list(img_rgb.getdata())
                sample_size = min(10000, len(pixels))
                step = max(1, len(pixels) // sample_size)
                sampled = pixels[::step]
                
                from collections import Counter
                # Quantisiere auf 8-Bit-Bins (±4)  
                quantized = [(r // 8 * 8, g // 8 * 8, b // 8 * 8) for r, g, b in sampled]
                most_common = Counter(quantized).most_common(1)
                if most_common:
                    bg_pct = most_common[0][1] / len(sampled) * 100
                    metrics["background_pct"] = round(bg_pct, 1)
                    if bg_pct > 97:
                        issues.append(f"Hintergrund dominiert: {bg_pct:.0f}% -- Diagramm möglicherweise zu klein")
                
                # 4. Foreground vs Background Kontrast (WCAG-basiert)
                # Bestimme Hintergrundfarbe (häufigste) und Vordergrund (Rest)
                bg_color = most_common[0][0] if most_common else (255, 255, 255)
                
                # Finde Vordergrund-Pixel (alles was nicht Hintergrund ±16 ist)
                def is_foreground(pixel, bg, threshold=24):
                    return any(abs(pixel[i] - bg[i]) > threshold for i in range(3))
                
                fg_pixels = [p for p in sampled if is_foreground(p, bg_color)]
                
                if fg_pixels:
                    avg_fg = tuple(sum(c[i] for c in fg_pixels) // len(fg_pixels) for i in range(3))
                    lum_fg = self._relative_luminance(*avg_fg)
                    lum_bg = self._relative_luminance(*bg_color)
                    cr = self._contrast_ratio(lum_fg, lum_bg)
                    metrics["contrast_ratio"] = round(cr, 2)
                    metrics["foreground_pct"] = round(len(fg_pixels) / len(sampled) * 100, 1)
                    
                    # WCAG AA: ≥4.5:1 für normalen Text, ≥3:1 für große Elemente
                    if cr < 3.0:
                        issues.append(f"Sehr niedriger Kontrast: {cr:.1f}:1 (WCAG AA erfordert ≥4.5:1)")
                    elif cr < 4.5:
                        issues.append(f"Grenzwertiger Kontrast: {cr:.1f}:1 (WCAG AA erfordert ≥4.5:1)")
                else:
                    metrics["contrast_ratio"] = 0.0
                    metrics["foreground_pct"] = 0.0
        
        except Exception as e:
            issues.append(f"Heuristic-Check fehlgeschlagen: {e}")
        
        return {
            "pass": len(issues) == 0,
            "issues": issues,
            "metrics": metrics,
        }
    
    def validate_diagram(self, image_path: str, diagram_description: Dict) -> Dict:
        """
        Validiert ein generiertes Diagramm
        
        Args:
            image_path: Pfad zum generierten Diagramm
            diagram_description: Die ursprüngliche Beschreibung des Diagramms
            
        Returns:
            {
                "quality_score": float (0-100),
                "issues": List[str],
                "suggestions": List[str],
                "is_acceptable": bool
            }
        """
        if not self.validation_enabled:
            return {
                "quality_score": 0,
                "issues": ["Vision-LLM nicht verfügbar"],
                "suggestions": [],
                "is_acceptable": False,
                "validation_skipped": True
            }
        
        if not os.path.exists(image_path):
            return {
                "quality_score": 0,
                "issues": [f"Diagramm-Datei nicht gefunden: {image_path}"],
                "suggestions": [],
                "is_acceptable": False
            }
        
        try:
            # ═══ PHASE 0: Pixel-Level Heuristic Pre-Check ═══
            heuristic = self.heuristic_check(image_path)
            if heuristic["issues"]:
                logger.warning(f"⚠️ Heuristic issues: {heuristic['issues']}")
            
            # Erstelle Validierungs-Prompt
            validation_prompt = self._create_validation_prompt(diagram_description)
            
            # ═══ PHASE 1: Vision-LLM direkt über model_loader ═══
            # model_loader.generate_response() handhabt multimodal intern:
            #   → _validate_image_path() prüft Datei
            #   → _process_multimodal() konvertiert zu file:// URL
            #   → Llava15ChatHandler verarbeitet das Bild nativ
            logger.info(f"🔍 Sende Diagramm an Vision-LLM: {os.path.basename(image_path)}")
            
            response_text = self.model_loader.generate_response(
                prompt=validation_prompt,
                image_path=image_path,
                temperature=0.3,   # Niedrig für objektive Bewertung
                max_tokens=1000
            )
            
            # Parse die Antwort
            validation_result = self._parse_validation_response(response_text)
            
            # Merge heuristic issues
            if heuristic["issues"]:
                validation_result.setdefault("issues", []).extend(
                    [f"[Heuristic] {i}" for i in heuristic["issues"]]
                )
                # Reduziere Score wenn Heuristic fehlgeschlagen
                penalty = min(20, len(heuristic["issues"]) * 5)
                validation_result["quality_score"] = max(0, 
                    validation_result.get("quality_score", 50) - penalty)
            validation_result["heuristic_metrics"] = heuristic.get("metrics", {})
            
            logger.info(f"📊 Diagramm-Validierung: Score={validation_result['quality_score']}, "
                       f"Issues={len(validation_result['issues'])}")
            
            return validation_result
            
        except Exception as e:
            logger.error(f"❌ Fehler bei Diagramm-Validierung: {e}", exc_info=True)
            return {
                "quality_score": 0,
                "issues": [f"Validierung fehlgeschlagen: {str(e)}"],
                "suggestions": [],
                "is_acceptable": False
            }
    
    def _encode_image_to_base64(self, image_path: str) -> str:
        """Kodiert Bild als Base64"""
        try:
            # Öffne und ggf. konvertiere Bild
            with Image.open(image_path) as img:
                # Konvertiere zu RGB falls nötig
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Konvertiere zu Base64
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                img_bytes = buffered.getvalue()
                img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                
                return img_base64
                
        except Exception as e:
            logger.error(f"❌ Fehler beim Kodieren des Bildes: {e}")
            raise
    
    def _create_validation_prompt(self, diagram_description: Dict) -> str:
        """Erstellt den Validierungs-Prompt für das Vision-LLM"""
        
        diagram_type = diagram_description.get("type", "unbekannt")
        title = diagram_description.get("title", "")
        
        prompt = f"""Du bist ein Experte für Datenvisualisierung. Bewerte die QUALITÄT dieses {diagram_type.upper()}-Diagramms.

DIAGRAMM-KONTEXT:
- Typ: {diagram_type}
- Titel: {title}
- Sollte darstellen: {diagram_description.get('nodes', [])}

BEWERTE FOLGENDE ASPEKTE:

1. **LESBARKEIT** (30 Punkte):
   - Sind alle Beschriftungen lesbar?
   - Gibt es Überlappungen von Text?
   - Ist die Schriftgröße angemessen?

2. **KORREKTHEIT** (30 Punkte):
   - Stimmen die dargestellten Daten mit der Beschreibung überein?
   - Sind alle Elemente korrekt positioniert?
   - Fehlen wichtige Elemente?

3. **LAYOUT** (20 Punkte):
   - Ist das Layout ausgewogen?
   - Ist der Platz gut genutzt?
   - Gibt es unnötige Überlappungen?

4. **ÄSTHETIK** (20 Punkte):
   - Sind die Farben kontrastreich genug?
   - Ist das Diagramm professionell?
   - Ist es visuell ansprechend?

ANTWORTE IN FOLGENDEM JSON-FORMAT:
```json
{{
  "quality_score": <0-100>,
  "readability_score": <0-30>,
  "correctness_score": <0-30>,
  "layout_score": <0-20>,
  "aesthetics_score": <0-20>,
  "issues": [
    "Problem 1: Beschriftung 'XYZ' ist unleserlich",
    "Problem 2: Überlappung zwischen Node A und B"
  ],
  "suggestions": [
    "Vorschlag 1: Schriftgröße erhöhen auf mindestens 12pt",
    "Vorschlag 2: Abstand zwischen Nodes vergrößern"
  ],
  "is_acceptable": <true/false>
}}
```

WICHTIG:
- is_acceptable = true NUR wenn quality_score >= 70
- Sei kritisch aber fair
- Gib konkrete, umsetzbare Vorschläge"""

        return prompt
    
    def _parse_validation_response(self, llm_response: str) -> Dict:
        """
        Parsed die LLM-Antwort mit Structured Output Validation
        
        ✅ PHASE 1: Nutzt DiagramValidationOutput Schema für robustes Parsing
        """
        # Überprüfe, ob die Antwort leer ist
        if not llm_response or llm_response.strip() == "":
            logger.error("❌ LLM-Antwort ist leer")
            return {
                "quality_score": 0,
                "issues": ["LLM-Antwort war leer"],
                "suggestions": [],
                "is_acceptable": False
            }
        
        # ✅ PHASE 1: Structured Output mit Pydantic-Validierung
        if STRUCTURED_OUTPUTS_AVAILABLE and DiagramValidationOutput is not None:
            try:
                import json
                import re
                
                # Extrahiere JSON aus der Antwort
                json_match = re.search(r'```json\n(.*?)\n```', llm_response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    # Versuche, die gesamte Antwort als JSON zu parsen
                    json_str = llm_response.strip()
                
                # Validiere mit Pydantic Schema
                validation_output = DiagramValidationOutput.model_validate_json(json_str)
                
                # Konvertiere zu erwarteter Dict-Struktur
                result = {
                    "quality_score": validation_output.quality_score,
                    "issues": validation_output.issues,
                    "suggestions": validation_output.suggestions,
                    "is_acceptable": validation_output.is_acceptable
                }
                
                logger.info(f"✅ [DIAGRAM-VALIDATION-STRUCTURED] Score: {result['quality_score']}, Acceptable: {result['is_acceptable']}")
                return result
                
            except Exception as e:
                logger.debug(f"⚠️ [DIAGRAM-VALIDATION-STRUCTURED] Pydantic validation failed: {e}, trying legacy...")
        
        # Legacy JSON Parsing (Fallback)
        try:
            import json
            import re
            
            # Suche nach JSON-Block
            json_match = re.search(r'```json\n(.*?)\n```', llm_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Versuche, die gesamte Antwort als JSON zu parsen
                json_str = llm_response.strip()
            
            result = json.loads(json_str)
            
            # Validiere Struktur
            required_keys = ["quality_score", "issues", "suggestions", "is_acceptable"]
            for key in required_keys:
                if key not in result:
                    result[key] = None if key != "is_acceptable" else False
            
            # Normalisiere quality_score mit robustem Parsing
            if result["quality_score"] is not None:
                try:
                    # Versuche direkt zu konvertieren
                    quality_score_value = float(result["quality_score"])
                    result["quality_score"] = max(0, min(100, quality_score_value))
                except (ValueError, TypeError):
                    # Wenn String ist z.B. "N/A (Bild fehlt)" oder ähnlich
                    score_str = str(result["quality_score"]).strip().lower()
                    if "n/a" in score_str or "fehlt" in score_str or "unavailable" in score_str:
                        # Image nicht verfügbar → setze Heuristik-Wert
                        result["quality_score"] = 35  # Niedrig, da Validierung nicht möglich
                        result["issues"] = result.get("issues", []) + ["⚠️ Bild konnte nicht validiert werden (N/A)"]
                        logger.warning(f"⚠️ Quality score war N/A, setze auf Heuristik-Fallback: {result['quality_score']}")
                    else:
                        # Andere unbekannte Formate → Default-Heuristik
                        result["quality_score"] = 50
                        logger.warning(f"⚠️ Quality score konnte nicht geparst werden ('{result['quality_score']}'), setze auf 50")
            else:
                result["quality_score"] = 50  # Default wenn None
            
            logger.info(f"✅ [DIAGRAM-VALIDATION-LEGACY] Score: {result['quality_score']}, Acceptable: {result['is_acceptable']}")
            return dict(result)
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Parsen der Validierungs-Antwort: {e}")
            logger.debug(f"   Raw LLM Response (first 500 chars): {llm_response[:500]}")
            # Fallback: Manuelle Analyse
            return {
                "quality_score": 50,
                "issues": ["Konnte Validierung nicht automatisch auswerten"],
                "suggestions": ["Bitte manuell prüfen"],
                "is_acceptable": False,
                "raw_response": llm_response
            }
    
    def suggest_improvements(self, validation_result: Dict, 
                            original_description: Dict) -> Optional[Dict]:
        """
        Schlägt konkrete Verbesserungen für die Diagramm-Beschreibung vor
        
        Args:
            validation_result: Ergebnis der Validierung
            original_description: Die ursprüngliche Diagramm-Beschreibung
            
        Returns:
            Verbesserte Diagramm-Beschreibung oder None
        """
        if validation_result.get("is_acceptable", False):
            return None  # Keine Verbesserungen nötig
        
        # Erstelle verbesserte Beschreibung basierend auf Issues
        improved_description = original_description.copy()
        
        issues = validation_result.get("issues", [])
        
        # Automatische Anpassungen
        for issue in issues:
            issue_lower = issue.lower()
            
            # Schriftgröße-Probleme
            if any(k in issue_lower for k in ("schrift", "lesbar", "font", "text", "label")):
                if "style" not in improved_description:
                    improved_description["style"] = {}
                current_font = improved_description["style"].get("font_size", 11)
                improved_description["style"]["font_size"] = current_font + 2
                logger.info(f"🔧 Erhöhe font_size: {current_font} → {current_font + 2}")
            
            # Überlappungs- und Platzprobleme
            if any(k in issue_lower for k in ("überlapp", "spacing", "overlap", "crowd", "platz", "klein")):
                if "style" not in improved_description:
                    improved_description["style"] = {}
                current_figsize = improved_description["style"].get("figsize", [14, 10])
                improved_description["style"]["figsize"] = [
                    current_figsize[0] + 3,
                    current_figsize[1] + 2
                ]
                logger.info(f"🔧 Vergrößere figsize: {current_figsize} → {improved_description['style']['figsize']}")
            
            # Kontrast-Probleme
            if any(k in issue_lower for k in ("kontrast", "farb", "contrast", "color", "wcag")):
                if "style" not in improved_description:
                    improved_description["style"] = {}
                improved_description["style"]["node_color"] = "#2E86DE"  # Kräftiges Blau
                improved_description["style"]["background_color"] = "#FFFFFF"
                improved_description["style"]["edge_color"] = "#2C3E50"
                logger.info("🔧 Verbessere Farbkontrast (WCAG-optimiert)")
            
            # Auflösungsprobleme
            if any(k in issue_lower for k in ("auflösung", "resolution", "dpi", "pixel")):
                if "style" not in improved_description:
                    improved_description["style"] = {}
                improved_description["style"]["dpi"] = 200
                logger.info("🔧 Erhöhe DPI auf 200")
            
            # Hintergrund-Dominanz (Diagramm zu klein)
            if any(k in issue_lower for k in ("hintergrund", "background", "dominiert", "leer", "empty")):
                if "style" not in improved_description:
                    improved_description["style"] = {}
                improved_description["style"]["node_size"] = improved_description["style"].get("node_size", 2000) + 500
                logger.info("🔧 Vergrößere Node-Größe")
        
        return improved_description if improved_description != original_description else None


def validate_and_improve_diagram(diagram_path: str, 
                                 diagram_description: Dict,
                                 model_loader,
                                 max_iterations: int = 2) -> Tuple[str, Dict]:
    """
    Validiert ein Diagramm mit Vision-LLM und erstellt ggf. verbesserte Version
    
    Args:
        diagram_path: Pfad zum Diagramm
        diagram_description: Diagramm-Beschreibung
        model_loader: ModelLoader-Instanz (multimodal, mit generate_response)
        max_iterations: Max. Anzahl Verbesserungs-Iterationen
        
    Returns:
        (final_diagram_path, final_validation_result)
    """
    from generic_visualization_tool import GenericVisualizationTool
    
    validator = DiagramQualityValidator(model_loader)
    viz_tool = GenericVisualizationTool()
    
    current_path = diagram_path
    current_description = diagram_description
    
    for iteration in range(max_iterations):
        logger.info(f"🔍 Validiere Diagramm (Iteration {iteration + 1}/{max_iterations})")
        
        # Validiere aktuelles Diagramm
        validation_result = validator.validate_diagram(current_path, current_description)
        
        if validation_result.get("is_acceptable", False):
            logger.info(f"✅ Diagramm akzeptabel (Score: {validation_result['quality_score']})")
            return current_path, validation_result
        
        if iteration < max_iterations - 1:
            # Schlage Verbesserungen vor
            improved_description = validator.suggest_improvements(
                validation_result, 
                current_description
            )
            
            if improved_description:
                logger.info(f"🔧 Erstelle verbessertes Diagramm (Iteration {iteration + 2})")
                
                # Erstelle verbessertes Diagramm
                improved_path = current_path.replace(".png", f"_v{iteration + 2}.png")
                try:
                    improved_path = viz_tool.visualize(
                        description=improved_description,
                        output_path=improved_path
                    )
                    current_path = improved_path
                    current_description = improved_description
                except Exception as e:
                    logger.error(f"❌ Fehler bei Verbesserung: {e}")
                    break
            else:
                logger.warning("⚠️ Keine automatischen Verbesserungen möglich")
                break
    
    # Finale Validierung
    final_validation = validator.validate_diagram(current_path, current_description)
    return current_path, final_validation
