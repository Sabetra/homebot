"""
Text and formatting utilities for psychological session interface.

This module provides utilities for:
- Date and time formatting in German
- Text truncation and cleaning
- Context formatting for LLM prompts
"""
from datetime import datetime
from typing import List, Dict, Any


def get_german_datetime_info(dt: datetime) -> Dict[str, str]:
    """
    Get German datetime information.
    
    Args:
        dt: Datetime object to format
        
    Returns:
        Dict with keys:
        - wochentag: German weekday name
        - monat: German month name
        - tageszeit: Time of day category (Morgen, Mittag, etc.)
    """
    wochentage = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
    monate = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 
              'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember']
    
    wochentag = wochentage[dt.weekday()]
    monat = monate[dt.month - 1]
    
    # Tageszeit-Kategorie
    stunde = dt.hour
    if 5 <= stunde < 12:
        tageszeit = "Morgen"
    elif 12 <= stunde < 14:
        tageszeit = "Mittag"
    elif 14 <= stunde < 18:
        tageszeit = "Nachmittag"
    elif 18 <= stunde < 22:
        tageszeit = "Abend"
    else:
        tageszeit = "Nacht"
    
    return {
        'wochentag': wochentag,
        'monat': monat,
        'tageszeit': tageszeit
    }


def format_datetime_section(dt: datetime) -> List[str]:
    """
    Format datetime section for LLM prompt.
    
    Args:
        dt: Datetime to format
        
    Returns:
        List of formatted lines
    """
    info = get_german_datetime_info(dt)
    
    lines = [
        "=" * 80,
        "AKTUELLER ZEITKONTEXT",
        "=" * 80,
        f"📅 Datum: {info['wochentag']}, {dt.day}. {info['monat']} {dt.year}",
        f"🕐 Uhrzeit: {dt.strftime('%H:%M')} Uhr ({info['tageszeit']})",
        ""
    ]
    
    return lines


def get_relevance_indicator(relevance_score: float) -> str:
    """
    Get relevance indicator emoji based on score.
    
    Args:
        relevance_score: Float between 0 and 1
        
    Returns:
        Emoji indicator string
    """
    if relevance_score >= 0.7:
        return "🔥"  # Very relevant
    elif relevance_score >= 0.5:
        return "⭐"  # Relevant
    else:
        return "○"   # Basic relevance


def get_trend_emoji(trend: str) -> str:
    """
    Get trend emoji and description.
    
    Args:
        trend: Trend category string
        
    Returns:
        Emoji with German description
    """
    trend_map = {
        'improving': '📈 Verbessernd',
        'declining': '📉 Verschlechternd',
        'stable': '➡️ Stabil',
        'fluctuating': '〰️ Schwankend'
    }
    return trend_map.get(trend, '❓ Unbekannt')


def get_valence_description(avg_valence: float) -> str:
    """
    Get German description for valence score.
    
    Args:
        avg_valence: Average valence (0-1)
        
    Returns:
        German description
    """
    if avg_valence >= 0.7:
        return "Überwiegend positiv"
    elif avg_valence >= 0.5:
        return "Leicht positiv"
    elif avg_valence >= 0.3:
        return "Leicht negativ"
    else:
        return "Überwiegend negativ"


def get_status_emoji(status: str, has_progress: bool = False) -> str:
    """
    Get status emoji for care goals.
    
    DEPRECATED: Use GoalUIRenderer.emoji() for new code.
    This function is kept for backwards compatibility.
    
    The old implementation had bugs:
    - Searched for 'completed' but system uses 'achieved'
    - Ignored status when has_progress=True
    - Didn't handle all GoalStatus enum values
    
    This wrapper now delegates to the new GoalUIRenderer state machine
    which properly handles all cases.
    
    Args:
        status: Goal status string
        has_progress: Whether goal has recorded progress (deprecated concept)
        
    Returns:
        Emoji indicator
    """
    # Import here to avoid circular imports
    from wellbeing_session.ui.goal_renderer import GoalUIRenderer
    
    # Adapt old calling convention to new one
    # has_progress=True in old system means "some progress exists"
    # Progress score is unknown, so we use 0.5 as an arbitrary value
    progress_score = 0.5 if has_progress else None
    
    # Normalize status string
    status_normalized = status.lower().strip() if isinstance(status, str) else ""
    
    return GoalUIRenderer.emoji(status_normalized, progress_score)
