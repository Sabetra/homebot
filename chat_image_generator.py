#!/usr/bin/env python3
"""
CHAT-BILD-GENERATOR (TYPE-SAFE VERSION)
======================================

Ein robustes System für die Generierung und Anzeige von Bildern im Chat,
mit korrekter matplotlib Type-Behandlung.
"""

import os
import io
import base64
import logging
import tempfile
import time
from typing import Optional, Tuple, Dict, Any, List, Union
from pathlib import Path
import re

# Setup Logging
logger = logging.getLogger(__name__)

class ChatImageGenerator:
    """
    Generiert und verwaltet Bilder für Chat-Antworten
    """
    
    def __init__(self):
        """Initialisiert den Chat-Bild-Generator"""
        self.image_dir = Path("generated_images")
        self.image_dir.mkdir(exist_ok=True)
        
        # Dynamic matplotlib import für Type-Safety
        self.plt = None
        self.patches = None
        self.matplotlib_available = False
        
        try:
            import matplotlib
            matplotlib.use('Agg')  # Non-interactive backend
            import matplotlib.pyplot as plt
            import matplotlib.patches as patches
            self.plt = plt
            self.patches = patches
            self.matplotlib_available = True
            logger.info("✅ Matplotlib erfolgreich importiert")
        except ImportError as e:
            logger.warning(f"⚠️ Matplotlib nicht verfügbar: {e}")
        
        # PIL import
        self.pil_available = False
        try:
            from PIL import Image, ImageDraw, ImageFont
            self.Image = Image
            self.ImageDraw = ImageDraw
            self.ImageFont = ImageFont
            self.pil_available = True
            logger.info("✅ PIL erfolgreich importiert")
        except ImportError as e:
            logger.warning(f"⚠️ PIL nicht verfügbar: {e}")
    
    def process_chat_response(self, user_message: str, bot_response: str) -> Tuple[str, Optional[str]]:
        """
        Verarbeitet Chat-Antwort und generiert Bilder falls relevant
        
        Args:
            user_message: Benutzer-Nachricht
            bot_response: Bot-Antwort
            
        Returns:
            (erweiterte_antwort, bild_pfad_oder_none)
        """
        try:
            # Erkenne Bildkontext
            image_context = self._detect_image_context(user_message, bot_response)
            
            if image_context:
                # Generiere entsprechendes Bild
                image_path = self._generate_contextual_image(image_context, user_message, bot_response)
                
                if image_path:
                    # Erweitere Bot-Antwort
                    enhanced_response = f"{bot_response}\n\n📊 **Visualisierung erstellt**\n*Siehe Diagramm unten zur Veranschaulichung.*"
                    
                    logger.info(f"✅ Kontextuelles Bild generiert: {image_path}")
                    return enhanced_response, image_path
            
            return bot_response, None
            
        except Exception as e:
            logger.warning(f"⚠️ Bildgenerierung fehlgeschlagen: {e}")
            return bot_response, None
    
    def _detect_image_context(self, user_message: str, bot_response: str) -> Optional[Dict[str, Any]]:
        """Erkennt Bildkontext in Chat-Nachrichten"""
        combined_text = f"{user_message} {bot_response}".lower()
        
        # Diagramm-Keywords
        chart_keywords = [
            'vergleich', 'unterschied', 'statistik', 'zahlen', 'daten',
            'prozent', 'anteil', 'verteilung', 'diagramm', 'grafik',
            'übersicht', 'entwicklung', 'trend', 'verlauf'
        ]
        
        # Konzept-Keywords  
        concept_keywords = [
            'struktur', 'aufbau', 'schema', 'konzept', 'modell',
            'architektur', 'system', 'hierarchie', 'beziehung',
            'workflow', 'prozess', 'ablauf'
        ]
        
        # Erkenne Chart-Kontext
        chart_matches = sum(1 for kw in chart_keywords if kw in combined_text)
        concept_matches = sum(1 for kw in concept_keywords if kw in combined_text)
        
        if chart_matches >= 2:
            return {
                'type': 'chart',
                'confidence': min(chart_matches / len(chart_keywords), 1.0),
                'data': self._extract_chart_data(bot_response)
            }
        
        elif concept_matches >= 2:
            return {
                'type': 'concept',
                'confidence': min(concept_matches / len(concept_keywords), 1.0),
                'concepts': self._extract_concepts(bot_response)
            }
        
        return None
    
    def _extract_chart_data(self, text: str) -> Dict[str, Union[int, float]]:
        """Extrahiert Daten für Diagramme"""
        data = {}
        
        # Suche nach Zahlen und Bezeichnungen
        import re
        
        # Pattern für "Begriff: Zahl" oder "Begriff (Zahl)"
        patterns = [
            r'(\w+):\s*(\d+(?:\.\d+)?)',
            r'(\w+)\s*\((\d+(?:\.\d+)?)\)',
            r'(\w+)\s*=\s*(\d+(?:\.\d+)?)',
            r'(\w+)\s*-\s*(\d+(?:\.\d+)?)'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for label, value in matches:
                try:
                    data[label.capitalize()] = float(value)
                except ValueError:
                    continue
        
        # Fallback: Standard-Daten
        if not data:
            data = {
                "Kategorie A": 45.0,
                "Kategorie B": 30.0,
                "Kategorie C": 25.0
            }
        
        return data
    
    def _extract_concepts(self, text: str) -> List[str]:
        """Extrahiert Konzepte für Diagramme"""
        import re
        
        # Finde wichtige Substantive (Großbuchstaben)
        concepts = re.findall(r'\b[A-ZÄÖÜ][a-zäöüß]+\b', text)
        
        # Entferne häufige Wörter
        stopwords = {'Der', 'Die', 'Das', 'Ein', 'Eine', 'Ist', 'Sind', 'Hat', 'Haben', 'Kann', 'Werden'}
        concepts = [c for c in concepts if c not in stopwords]
        
        # Fallback
        if not concepts:
            concepts = ["Hauptkonzept", "Element 1", "Element 2", "Element 3"]
        
        return concepts[:6]  # Max 6 Konzepte
    
    def _generate_contextual_image(self, context: Dict[str, Any], user_msg: str, bot_msg: str) -> Optional[str]:
        """Generiert kontextuelles Bild"""
        if not self.matplotlib_available:
            return None
        
        try:
            if context['type'] == 'chart':
                return self._create_chart(context['data'], f"Daten zu: {user_msg[:30]}...")
            elif context['type'] == 'concept':
                return self._create_concept_diagram(context['concepts'], f"Konzept: {user_msg[:30]}...")
        except Exception as e:
            logger.warning(f"⚠️ Bild-Generierung fehlgeschlagen: {e}")
        
        return None
    
    def _create_chart(self, data: Dict[str, Union[int, float]], title: str) -> Optional[str]:
        """Erstellt ein Balkendiagramm"""
        if not self.matplotlib_available or not self.plt:
            return None
        
        fig = None  # Initialisiere fig für Exception-Behandlung
        try:
            fig, ax = self.plt.subplots(figsize=(10, 6))
            
            labels = list(data.keys())
            values = [float(v) for v in data.values()]
            
            bars = ax.bar(labels, values, color=['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd'])
            
            # Werte auf Balken anzeigen
            for bar, value in zip(bars, values):
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{value:.1f}', ha='center', va='bottom')
            
            ax.set_title(title, fontsize=14, pad=20)
            ax.set_ylabel('Werte')
            
            if len(labels) > 3:
                self.plt.xticks(rotation=45)
            self.plt.tight_layout()
            
            # Speichern
            filename = self.image_dir / f"chart_{int(time.time())}.png"
            self.plt.savefig(filename, dpi=150, bbox_inches='tight')
            self.plt.close(fig)
            
            return str(filename)
            
        except Exception as e:
            logger.error(f"❌ Chart-Generierung fehlgeschlagen: {e}")
            try:
                if 'fig' in locals() and fig is not None:
                    self.plt.close(fig)
            except Exception:
                pass
            return None
    
    def _create_concept_diagram(self, concepts: List[str], title: str) -> Optional[str]:
        """Erstellt ein Konzept-Diagramm"""
        if not self.matplotlib_available or not self.plt or not self.patches:
            return None
        
        fig = None  # Initialisiere fig für Exception-Behandlung
        try:
            fig, ax = self.plt.subplots(figsize=(12, 8))
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis('off')
            
            # Zentrales Konzept
            center_x, center_y = 0.5, 0.5
            central_circle = self.patches.Circle((center_x, center_y), 0.08,
                                               facecolor='lightblue', edgecolor='black', linewidth=2)
            ax.add_patch(central_circle)
            ax.text(center_x, center_y, concepts[0][:12], ha='center', va='center',
                   fontsize=10, weight='bold')
            
            # Umgebende Konzepte
            import math
            for i, concept in enumerate(concepts[1:], 1):
                angle = 2 * math.pi * i / len(concepts[1:])
                x = center_x + 0.3 * math.cos(angle)
                y = center_y + 0.3 * math.sin(angle)
                
                # Konzept-Kreis
                circle = self.patches.Circle((x, y), 0.06,
                                           facecolor='lightgreen', edgecolor='black')
                ax.add_patch(circle)
                ax.text(x, y, concept[:10], ha='center', va='center',
                       fontsize=8)
                
                # Verbindungslinie
                ax.plot([center_x, x], [center_y, y], 'k-', alpha=0.6)
            
            self.plt.title(f"Konzept: {title}", fontsize=16, pad=20)
            self.plt.tight_layout()
            
            # Speichern
            filename = self.image_dir / f"concept_{int(time.time())}.png"
            self.plt.savefig(filename, dpi=150, bbox_inches='tight')
            self.plt.close(fig)
            
            return str(filename)
            
        except Exception as e:
            logger.error(f"❌ Konzept-Diagramm-Generierung fehlgeschlagen: {e}")
            try:
                if fig is not None:
                    self.plt.close(fig)
            except Exception:
                pass
            return None


def test_image_generator():
    """Test-Funktion"""
    print("🧪 Teste Chat Image Generator...")
    
    generator = ChatImageGenerator()
    
    test_cases = [
        ("Zeige mir Statistiken", "Die Verteilung ist: A: 40, B: 35, C: 25"),
        ("Erkläre die Architektur", "Das System besteht aus Frontend, Backend, Datenbank und API")
    ]
    
    for user_msg, bot_msg in test_cases:
        print(f"\n📝 Test: {user_msg}")
        enhanced_response, image_path = generator.process_chat_response(user_msg, bot_msg)
        
        if image_path:
            print(f"✅ Bild generiert: {image_path}")
        else:
            print("❌ Kein Bild generiert")
    
    print("\n🧪 Test abgeschlossen!")


if __name__ == "__main__":
    test_image_generator()
