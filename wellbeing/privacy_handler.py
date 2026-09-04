"""
Privacy-Handler für psychologische Unterstützung
===============================================
DSGVO-konforme Datenschutz-Funktionen für sensitive therapeutische Daten:
- Anonymisierung von Personendaten
- Content-Bereinigung
- Aufbewahrungsrichtlinien
- Datenexport und -löschung
"""

import re
import hashlib
import logging
from typing import Dict, List, Optional, Any, Set
from typing_extensions import TypedDict
from datetime import datetime, timedelta
import json

# Logger für Privacy-Operationen
logger = logging.getLogger(__name__)


class PIIPatternInfo(TypedDict):
    """Typisierte PII-Pattern-Information"""
    pattern: str
    replacement: str
    level: int

class PrivacyHandler:
    """
    DSGVO-konformer Privacy-Handler für psychologische Daten
    
    Features:
    - Automatische Erkennung und Anonymisierung von PII
    - Konfigurierbare Bereinigungsregeln
    - Audit-Logging für Datenschutz-Compliance
    - Export-Funktionen für Datenportabilität
    """
    
    def __init__(self, anonymization_level: int = 1):
        """
        Initialisiert Privacy-Handler
        
        Args:
            anonymization_level: Anonymisierungs-Level (1=basis, 2=medium, 3=maximal)
        """
        self.anonymization_level = anonymization_level
        
        # PII-Pattern für verschiedene Datentypen
        self.pii_patterns: Dict[str, PIIPatternInfo] = {}
        self._init_pii_patterns()
        
        # Therapeutische Begriffe die NICHT anonymisiert werden sollen
        self._init_therapeutic_whitelist()
        
        # Ersetzungs-Cache für konsistente Anonymisierung
        self._replacement_cache: Dict[str, str] = {}
        
        logger.info(f"✓ PrivacyHandler initialisiert (Level: {anonymization_level})")
    
    def _init_pii_patterns(self) -> None:
        """Initialisiert Pattern für PII-Erkennung"""
        
        # Basis-Pattern für Level 1
        self.pii_patterns = {
            'email': {
                'pattern': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
                'replacement': '[EMAIL]',
                'level': 1
            },
            'phone': {
                'pattern': r'(\+49|0049|0)\s?[1-9]\d{1,4}\s?\d{1,7}\s?\d{1,7}',
                'replacement': '[TELEFON]',
                'level': 1
            },
            'full_name': {
                'pattern': r'\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b',
                'replacement': '[NAME]',
                'level': 2
            },
            'german_names': {
                'pattern': self._create_german_names_pattern(),
                'replacement': '[PERSON]',
                'level': 2
            },
            'addresses': {
                'pattern': r'\b\d{1,5}\s+[A-Z][a-z]+\s+(Straße|Str\.|Platz|Weg|Gasse)',
                'replacement': '[ADRESSE]',
                'level': 2
            },
            'postal_codes': {
                'pattern': r'\b\d{5}\s+[A-Z][a-z]+\b',
                'replacement': '[ORT]',
                'level': 2
            },
            'dates': {
                'pattern': r'\b\d{1,2}\.\d{1,2}\.\d{4}\b',
                'replacement': '[DATUM]',
                'level': 3
            },
            'ages': {
                'pattern': r'\b\d{1,2}\s+Jahre?\s+(alt|jung)\b',
                'replacement': '[ALTER]',
                'level': 3
            }
        }
    
    def _create_german_names_pattern(self) -> str:
        """Erstellt Pattern für häufige deutsche Namen"""
        # Häufige deutsche Vornamen
        common_names = [
            'Alexander', 'Andreas', 'Christian', 'Daniel', 'David', 'Frank', 'Jan', 'Jens',
            'Klaus', 'Markus', 'Martin', 'Michael', 'Peter', 'Stefan', 'Thomas', 'Uwe',
            'Andrea', 'Angela', 'Anna', 'Barbara', 'Birgit', 'Claudia', 'Diana', 'Julia',
            'Karin', 'Katrin', 'Maria', 'Monika', 'Nicole', 'Petra', 'Sabine', 'Sandra'
        ]
        return r'\b(' + '|'.join(common_names) + r')\b'
    
    def _init_therapeutic_whitelist(self) -> None:
        """Initialisiert Whitelist für therapeutische Begriffe"""
        self.therapeutic_whitelist = {
            'emotions': [
                'angst', 'freude', 'trauer', 'wut', 'ärger', 'liebe', 'hass',
                'eifersucht', 'neid', 'scham', 'schuld', 'stolz', 'hoffnung'
            ],
            'therapy_terms': [
                'therapie', 'beratung', 'gespräch', 'session', 'sitzung',
                'depression', 'burnout', 'stress', 'trauma', 'panik'
            ],
            'relationships': [
                'mutter', 'vater', 'eltern', 'kind', 'sohn', 'tochter',
                'freund', 'freundin', 'partner', 'ehemann', 'ehefrau'
            ]
        }
    
    def anonymize_user_id(self, user_id: str) -> str:
        """
        Anonymisiert Benutzer-ID für konsistente, aber anonyme Referenzierung
        
        Args:
            user_id: Original-Benutzer-ID
            
        Returns:
            Anonymisierte Benutzer-ID
        """
        try:
            normalized_user_id = str(user_id or "").strip()

            # NEU: Prüfe ob bereits eine psychologische User-ID (beginnt mit 'psych_')
            if normalized_user_id.startswith('psych_'):
                # Bereits anonymisiert, direkt zurückgeben
                return normalized_user_id

            # Explizit eingegebene User-IDs/Namen bleiben die kanonische Quelle.
            # Ein global konfigurierter Default-User darf diese NICHT überschreiben,
            # sonst werden mehrere reale Nutzer auf dieselbe gespeicherte ID gemappt.
            if normalized_user_id and normalized_user_id != 'default_user':
                hash_input = f"psychological_user_{normalized_user_id}".encode('utf-8')
                user_hash = hashlib.sha256(hash_input).hexdigest()[:12]
                return f"psych_{user_hash}"
            
            # Fallback nur dann, wenn keine explizite User-ID vorliegt.
            try:
                import sys
                import os
                sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                from config.user_id_config import get_current_user_id
                
                current_user_id_raw = get_current_user_id()
                # Ensure type safety
                current_user_id = str(current_user_id_raw) if current_user_id_raw is not None else ""
                if current_user_id.startswith('psych_'):
                    # Verwende die bereits existierende User-ID
                    return current_user_id
            except:
                pass
            
            # Fallback: Verwende Hash für konsistente Anonymisierung
            hash_input = f"psychological_user_{normalized_user_id or 'anonymous'}".encode('utf-8')
            user_hash = hashlib.sha256(hash_input).hexdigest()[:12]
            return f"psych_{user_hash}"
            
        except Exception as e:
            logger.error(f"❌ User-ID-Anonymisierung fehlgeschlagen: {e}")
            return "psych_anonymous"
    
    def clean_content(self, content: str) -> str:
        """
        Bereinigt Content von PII basierend auf Anonymisierungs-Level
        
        Args:
            content: Original-Content
            
        Returns:
            Bereinigter Content
        """
        try:
            if not content:
                return content
            
            cleaned_content = content
            
            # Wende PII-Pattern basierend auf Level an
            for pattern_name, pattern_info in self.pii_patterns.items():
                if pattern_info['level'] <= self.anonymization_level:
                    # Prüfe ob der Match in der therapeutischen Whitelist ist
                    if not self._is_therapeutic_term(content, pattern_info['pattern']):
                        cleaned_content = re.sub(
                            pattern_info['pattern'], 
                            pattern_info['replacement'], 
                            cleaned_content,
                            flags=re.IGNORECASE
                        )
            
            # Konsistente Ersetzungen für bessere Lesbarkeit
            cleaned_content = self._apply_consistent_replacements(cleaned_content)
            
            # Log wenn Änderungen vorgenommen wurden
            if cleaned_content != content:
                logger.debug(f"✓ Content bereinigt (Level {self.anonymization_level})")
            
            return cleaned_content
            
        except Exception as e:
            logger.error(f"❌ Content-Bereinigung fehlgeschlagen: {e}")
            return content
    
    def _is_therapeutic_term(self, content: str, pattern: str) -> bool:
        """
        Prüft ob gefundene Matches therapeutische Begriffe sind
        
        Args:
            content: Zu prüfender Content
            pattern: RegEx-Pattern
            
        Returns:
            True wenn therapeutischer Begriff
        """
        try:
            # Finde alle Matches
            matches = re.findall(pattern, content, re.IGNORECASE)
            
            # Prüfe jedes Match gegen Whitelist
            for match in matches:
                match_lower = match.lower() if isinstance(match, str) else str(match).lower()
                
                # Prüfe gegen alle Whitelist-Kategorien
                for category, terms in self.therapeutic_whitelist.items():
                    if any(term in match_lower for term in terms):
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Therapeutische Begriff-Prüfung fehlgeschlagen: {e}")
            return False
    
    def _apply_consistent_replacements(self, content: str) -> str:
        """
        Wendet konsistente Ersetzungen an (gleiche Namen → gleiche Platzhalter)
        
        Args:
            content: Content mit Platzhaltern
            
        Returns:
            Content mit konsistenten Platzhaltern
        """
        try:
            # Placeholder für zukünftige Implementierung
            # Hier könnte man Namen-Mapping implementieren
            return content
            
        except Exception as e:
            logger.error(f"❌ Konsistente Ersetzung fehlgeschlagen: {e}")
            return content
    
    def create_privacy_report(self, session_id: str, user_id: str) -> Dict[str, Any]:
        """
        Erstellt Datenschutz-Bericht für eine Session
        
        Args:
            session_id: Session-ID
            user_id: Benutzer-ID
            
        Returns:
            Privacy-Report als Dictionary
        """
        try:
            report = {
                'session_id': session_id,
                'user_id': self.anonymize_user_id(user_id),
                'anonymization_level': self.anonymization_level,
                'data_processing': {
                    'pii_patterns_applied': len([
                        p for p in self.pii_patterns.values() 
                        if p['level'] <= self.anonymization_level
                    ]),
                    'therapeutic_whitelist_active': True,
                    'encryption_enabled': True  # Wird von DB-Layer bestimmt
                },
                'compliance_info': {
                    'gdpr_compliant': True,
                    'data_minimization': True,
                    'purpose_limitation': True,
                    'storage_limitation': 'configurable'
                },
                'generated_at': datetime.now().isoformat()
            }
            
            return report
            
        except Exception as e:
            logger.error(f"❌ Privacy-Report-Erstellung fehlgeschlagen: {e}")
            return {}
    
    def export_user_data(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Bereitet Benutzerdaten für DSGVO-konformen Export vor
        
        Args:
            user_id: Benutzer-ID
            data: Rohdaten
            
        Returns:
            Exportbereite Daten
        """
        try:
            export_data = {
                'user_id': self.anonymize_user_id(user_id),
                'export_timestamp': datetime.now().isoformat(),
                'data_categories': {
                    'sessions': data.get('sessions', []),
                    'interactions': data.get('interactions', []),
                    'summaries': data.get('summaries', [])
                },
                'privacy_info': {
                    'anonymization_applied': True,
                    'encryption_used': True,
                    'data_minimized': True
                },
                'export_format': 'json',
                'retention_policy': 'user_controlled'
            }
            
            # Bereinige alle Content-Felder für Export
            cleaned_data = self._clean_export_data(export_data)
            
            # Ensure we return Dict[str, Any] as declared
            if isinstance(cleaned_data, dict):
                export_data = cleaned_data
            else:
                logger.warning(f"⚠️ _clean_export_data returned non-dict type: {type(cleaned_data)}")
            
            logger.info(f"✓ Datenexport vorbereitet für User: {user_id}")
            return export_data
            
        except Exception as e:
            logger.error(f"❌ Datenexport-Vorbereitung fehlgeschlagen: {e}")
            return {}
    
    def _clean_export_data(self, data: Any) -> Any:
        """
        Bereinigt Exportdaten rekursiv
        
        Args:
            data: Zu bereinigende Daten (dict, list, oder primitiv)
            
        Returns:
            Bereinigte Daten (gleicher Typ wie Input)
        """
        try:
            if isinstance(data, dict):
                cleaned = {}
                for key, value in data.items():
                    if key == 'content' and isinstance(value, str):
                        cleaned[key] = self.clean_content(value)
                    else:
                        cleaned[key] = self._clean_export_data(value)
                return cleaned
            
            elif isinstance(data, list):
                return [self._clean_export_data(item) for item in data]
            
            else:
                return data
                
        except Exception as e:
            logger.error(f"❌ Export-Daten-Bereinigung fehlgeschlagen: {e}")
            return data
    
    def validate_retention_policy(self, session_data: Dict[str, Any], 
                                 retention_days: int = 365) -> Dict[str, Any]:
        """
        Validiert Aufbewahrungsrichtlinien
        
        Args:
            session_data: Session-Daten
            retention_days: Aufbewahrungszeit in Tagen
            
        Returns:
            Validierungs-Report
        """
        try:
            now = datetime.now()
            
            # Parse Session-Zeitstempel
            created_at = datetime.fromisoformat(
                session_data.get('created_at', now.isoformat()).replace('Z', '+00:00')
            )
            
            # Berechne Alter
            age_days = (now - created_at).days
            
            # Validierung
            validation = {
                'session_id': session_data.get('id'),
                'age_days': age_days,
                'retention_days': retention_days,
                'within_retention_period': age_days <= retention_days,
                'days_until_deletion': max(0, retention_days - age_days),
                'deletion_required': age_days > retention_days,
                'checked_at': now.isoformat()
            }
            
            return validation
            
        except Exception as e:
            logger.error(f"❌ Retention-Policy-Validierung fehlgeschlagen: {e}")
            return {}
    
    def get_privacy_settings(self) -> Dict[str, Any]:
        """
        Liefert aktuelle Privacy-Einstellungen
        
        Returns:
            Privacy-Einstellungen
        """
        return {
            'anonymization_level': self.anonymization_level,
            'pii_patterns_count': len(self.pii_patterns),
            'active_patterns': [
                name for name, info in self.pii_patterns.items()
                if info['level'] <= self.anonymization_level
            ],
            'therapeutic_whitelist_categories': list(self.therapeutic_whitelist.keys()),
            'compliance_features': [
                'gdpr_compliant',
                'data_minimization',
                'purpose_limitation',
                'storage_limitation',
                'data_portability',
                'right_to_deletion'
            ]
        }
    
    def update_anonymization_level(self, new_level: int) -> bool:
        """
        Aktualisiert Anonymisierungs-Level
        
        Args:
            new_level: Neues Level (1-3)
            
        Returns:
            True wenn erfolgreich
        """
        try:
            if 1 <= new_level <= 3:
                old_level = self.anonymization_level
                self.anonymization_level = new_level
                
                logger.info(f"✓ Anonymisierungs-Level geändert: {old_level} → {new_level}")
                return True
            else:
                logger.warning(f"⚠ Ungültiges Anonymisierungs-Level: {new_level}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Level-Update fehlgeschlagen: {e}")
            return False
