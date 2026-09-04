#!/usr/bin/env python3
"""
User-ID-Konfiguration für psychologisches System
================================================
Zentraler Ort für User-ID-Management

Portabilität:
    - User-ID-Override:  ``HOMEBOT_USER_ID`` (Umgibung, z. B. Tests/CI)
    - Datenbank-Pfad:    ``utils.db_path_resolver.get_wellbeing_path()``
      (IMMER über den Resolver — kein Literal-Pfad, s. AGENTS.md)
"""

import os
import sqlite3

from utils.db_path_resolver import get_wellbeing_path


def get_primary_user_id() -> str:
    """
    Ermittelt die primäre User-ID.

    Priorität:
        1. ``HOMEBOT_USER_ID`` (Umgibungs-Override)
        2. User-ID mit den meisten psychologischen Sessions (DB)
        3. ``"default_user"`` (Fallback)

    Returns:
        Die aktive User-ID für das psychologische System
    """
    override = os.environ.get("HOMEBOT_USER_ID", "").strip()
    if override:
        return override

    try:
        # Datenbank-Pfad IMMER über den zentralen Resolver (AGENTS.md)
        db_path = get_wellbeing_path()
        if not db_path.exists():
            return "default_user"

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Finde User-ID mit den meisten Sessions
        cursor.execute("""
            SELECT user_id, COUNT(*) as session_count 
            FROM wellbeing_sessions 
            GROUP BY user_id 
            ORDER BY session_count DESC 
            LIMIT 1
        """)
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            user_id = result[0]
            print(f"[INFO] Primaere User-ID gefunden: {user_id} ({result[1]} Sessions)")
            return str(user_id)
        else:
            print("[WARNING] Keine User-ID in Datenbank gefunden, verwende default_user")
            return "default_user"
            
    except Exception as e:
        print(f"[WARNING] Fehler beim Ermitteln der User-ID: {e}")
        return "default_user"

# Globale Variable für User-ID Caching
_cached_user_id = None

def get_current_user_id() -> str:
    """
    Gibt die aktuelle User-ID zurück
    
    Returns:
        Aktuelle User-ID für das psychologische System
    """
    global _cached_user_id
    
    # Cache die User-ID für Performance
    if _cached_user_id is None:
        _cached_user_id = get_primary_user_id()
    
    return _cached_user_id

def set_user_id(user_id: str) -> None:
    """
    Setzt eine neue User-ID (für Tests oder manuelle Konfiguration)
    
    Args:
        user_id: Neue User-ID
    """
    global _cached_user_id
    _cached_user_id = user_id
    print(f"[SUCCESS] User-ID gesetzt auf: {user_id}")

# Exportiere die konfigurierte User-ID
CURRENT_USER_ID = get_current_user_id()

if __name__ == "__main__":
    print(f"🆔 Aktuelle User-ID: {get_current_user_id()}")
