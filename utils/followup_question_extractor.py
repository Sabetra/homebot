"""
Follow-Up Question Extractor
=============================
Extracts follow-up questions from bot responses using a multi-layer approach:
1. Structured parsing: [FOLLOW_UP]...[/FOLLOW_UP] delimiter blocks (+ truncated)
2. Section-based: "Weiterführende Fragen" / "Offene Fragen" sections
3. Embedded extraction: "Frage:" prefixed questions within the analysis text

SOTA Design:
- Prioritizes structured output (most reliable)
- Falls back to section-based extraction
- Last resort: scans for embedded questions (keeps text intact, just adds buttons)
- Deduplicates and ranks by relevance
- Strips extracted section from display text for clean rendering
"""

import re
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

# ── Compiled Patterns (performance) ────────────────────────────────
# Primary: Structured delimiters from the LLM
_STRUCTURED_PATTERN = re.compile(
    r'\[FOLLOW_UP\](.*?)\[/FOLLOW_UP\]',
    re.DOTALL | re.IGNORECASE
)

# Alternative structured formats the LLM might produce
_ALT_STRUCTURED_PATTERNS = [
    re.compile(r'<follow_up>(.*?)</follow_up>', re.DOTALL | re.IGNORECASE),
    re.compile(r'<folgefragen>(.*?)</folgefragen>', re.DOTALL | re.IGNORECASE),
]

# Heuristic: Section headers that indicate follow-up questions
# Flexible: handles numbered prefixes ("2. Offene Fragen") and suffixes ("und kritische Bewertung")
_SECTION_HEADER_PATTERN = re.compile(
    r'(?:^|\n)\s*(?:\d+[.)]\s*)?(?:#{1,4}\s*)?(?:\*{0,2})'
    r'(?:Offene Fragen|Weiterführende Fragen|Folgefragen|Mögliche Folgefragen|'
    r'Follow-up Fragen|Zum Weiterdenken|Weitergehende Fragen|'
    r'Das könntest du noch fragen|Weitere interessante Fragen)'
    r'(?:[^\n]*)'   # Allow suffix text like "und kritische Bewertung"
    r'(?:\*{0,2})\s*:?\s*\n',
    re.IGNORECASE | re.MULTILINE
)

# Embedded: "Frage:" prefixed questions within analytical text
_FRAGE_PREFIX_PATTERN = re.compile(
    r'(?:^|\n)\s*(?:\w\))\s*[^\n]*\n\s*Frage:\s*(.+?\?)',
    re.IGNORECASE | re.MULTILINE
)
# Simpler fallback: just "Frage: ..." on any line
_FRAGE_SIMPLE_PATTERN = re.compile(
    r'Frage:\s*(.+?\?)',
    re.IGNORECASE
)

# Heuristic: Bullet-point questions (e.g., "- Wie wird das finanziert?")
_BULLET_QUESTION_PATTERN = re.compile(
    r'^\s*[-•*▸►✦→]\s*(.+\?)\s*$',
    re.MULTILINE
)

# Heuristic: Numbered questions (e.g., "1. Wie wird das finanziert?")
_NUMBERED_QUESTION_PATTERN = re.compile(
    r'^\s*\d+[.)]\s*(.+\?)\s*$',
    re.MULTILINE
)

# Min/max constraints
MIN_QUESTION_LENGTH = 15   # Skip very short "questions"
MAX_QUESTION_LENGTH = 200  # Skip overly long "questions"
MAX_FOLLOWUP_COUNT = 5     # Maximum follow-up questions to return
MIN_FOLLOWUP_COUNT = 0     # Minimum (0 = don't force if none found)

FOLLOWUP_PERSPECTIVE_INSTRUCTION = (
    "Formuliere jeden Vorschlag als nächste Nachricht, die der Nutzer selbst per Klick "
    "an den Assistenten sendet. Frage aus Nutzerperspektive nach dem Thema; sprich den "
    "Nutzer nicht mit 'du' oder 'Sie' an und biete keine Assistentenhandlung an. "
    "Verboten sind insbesondere Formulierungen wie 'Möchten Sie ...?', 'Soll ich ...?', "
    "'Haben Sie ...?' oder 'Gibt es etwas ...?'."
)


def extract_followup_questions(response_text: str) -> Tuple[str, List[str]]:
    """
    Extract follow-up questions from a bot response.
    
    Returns:
        Tuple of (clean_text, questions):
        - clean_text: Response with follow-up section stripped for display
        - questions: List of extracted follow-up question strings
    """
    if not response_text or not isinstance(response_text, str):
        return response_text or "", []
    
    # ── Attempt 1: Structured delimiters ──
    clean_text, questions = _extract_structured(response_text)
    if questions:
        logger.info(f"✅ Extracted {len(questions)} follow-up questions (structured)")
        clean_text = _strip_orphan_followup_header(clean_text)
        return clean_text, questions[:MAX_FOLLOWUP_COUNT]
    
    # ── Attempt 2: Section-based extraction ──
    clean_text, questions = _extract_from_sections(response_text)
    if questions:
        logger.info(f"✅ Extracted {len(questions)} follow-up questions (section-based)")
        clean_text = _strip_orphan_followup_header(clean_text)
        return clean_text, questions[:MAX_FOLLOWUP_COUNT]
    
    # ── Attempt 3: Embedded questions ("Frage: ...?" within analysis text) ──
    # These are part of the content, so we DON'T remove them from display text.
    questions = _extract_embedded_questions(response_text)
    if questions:
        logger.info(f"✅ Extracted {len(questions)} follow-up questions (embedded)")
        return response_text, questions[:MAX_FOLLOWUP_COUNT]
    
    # ── No follow-up questions found ──
    return response_text, []


def _strip_orphan_followup_header(text: str) -> str:
    """Remove orphaned 'Weiterführende Fragen' headers left after extraction.
    
    After extracting the [FOLLOW_UP] block, the section header may remain
    as an empty line with no content below it. This cleans it up.
    """
    # Remove the header line if it's now followed by nothing or just whitespace/Quellen
    text = re.sub(
        r'\n\s*(?:#{1,4}\s*)?(?:\*{0,2})'
        r'(?:Offene Fragen|Weiterführende Fragen|Folgefragen|Mögliche Folgefragen|'
        r'Follow-up Fragen|Zum Weiterdenken|Weitergehende Fragen|'
        r'Das könntest du noch fragen|Weitere interessante Fragen)'
        r'(?:\*{0,2})\s*:?\s*(?=\n|$)',
        '',
        text,
        flags=re.IGNORECASE | re.MULTILINE
    )
    # Clean up resulting double blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_structured(text: str) -> Tuple[str, List[str]]:
    """Extract from [FOLLOW_UP]...[/FOLLOW_UP] or <follow_up>...</follow_up> blocks.
    
    Also handles TRUNCATED blocks where the LLM hit its token limit
    and the closing tag is missing.
    """
    # Try primary pattern (complete block)
    match = _STRUCTURED_PATTERN.search(text)
    if match:
        raw_block = match.group(1).strip()
        questions = _parse_question_block(raw_block)
        clean_text = text[:match.start()].rstrip() + text[match.end():].lstrip()
        clean_text = clean_text.strip()
        return clean_text, _validate_questions(questions)
    
    # Try alternative patterns (complete blocks)
    for pattern in _ALT_STRUCTURED_PATTERNS:
        match = pattern.search(text)
        if match:
            raw_block = match.group(1).strip()
            questions = _parse_question_block(raw_block)
            clean_text = text[:match.start()].rstrip() + text[match.end():].lstrip()
            clean_text = clean_text.strip()
            return clean_text, _validate_questions(questions)
    
    # ── TRUNCATION FALLBACK: Opening [FOLLOW_UP] tag without closing tag ──
    # LLM often hits token limit mid-block. Extract whatever we have.
    truncated_match = re.search(
        r'\[FOLLOW_UP\](.*)',
        text, re.DOTALL | re.IGNORECASE
    )
    if truncated_match:
        raw_block = truncated_match.group(1).strip()
        # Stop at clear content boundaries (Quellen, Sources, newlines before non-question content)
        for boundary in ['\nQuellen:', '\nSources:', '\n\n📊', '\n\n⚠️']:
            boundary_pos = raw_block.find(boundary)
            if boundary_pos > 0:
                raw_block = raw_block[:boundary_pos].strip()
        questions = _parse_question_block(raw_block)
        if questions:
            logger.info(f"✅ Truncated [FOLLOW_UP] block recovered: {len(questions)} questions")
            clean_text = text[:truncated_match.start()].rstrip()
            # Don't discard content after the block (Quellen etc.)
            after_block = truncated_match.group(1)
            for boundary in ['\nQuellen:', '\nSources:']:
                bp = after_block.find(boundary)
                if bp >= 0:
                    clean_text = clean_text + after_block[bp:]
                    break
            clean_text = clean_text.strip()
            return clean_text, _validate_questions(questions)
    
    # Also try truncated alternative tags
    for tag_open in ['<follow_up>', '<folgefragen>']:
        trunc_match = re.search(
            re.escape(tag_open) + r'(.*)',
            text, re.DOTALL | re.IGNORECASE
        )
        if trunc_match:
            raw_block = trunc_match.group(1).strip()
            for boundary_str in ['\nQuellen:', '\nSources:']:
                bp = raw_block.find(boundary_str)
                if bp > 0:
                    raw_block = raw_block[:bp].strip()
            questions = _parse_question_block(raw_block)
            if questions:
                clean_text = text[:trunc_match.start()].rstrip()
                clean_text = clean_text.strip()
                return clean_text, _validate_questions(questions)
    
    return text, []


def _extract_from_sections(text: str) -> Tuple[str, List[str]]:
    """Extract questions from DEDICATED follow-up sections like '## Weiterführende Fragen'.
    
    Only extracts and removes sections that are PURE follow-up question lists.
    Analytical sections (containing 'Eigene Einschätzung', sub-headers, etc.)
    are left intact — those are handled by _extract_embedded_questions instead.
    
    Handles:
    - Bullet-point questions (- Wie wird...?)
    - Numbered questions (1. Wie wird...?)
    - Pipe-separated inline blocks ([FOLLOW_UP] or plain)
    - Plain lines ending with ?
    """
    match = _SECTION_HEADER_PATTERN.search(text)
    if not match:
        return text, []
    
    section_start = match.start()
    section_header_end = match.end()
    
    # Find the section content: everything until the next header, Quellen block, or end of text
    remaining = text[section_header_end:]
    
    # Find end of section: next numbered heading, # heading, Quellen block, or emoji-section
    section_end_pattern = re.search(
        r'\n\s*(?:\d+[.)]\s+[A-ZÄÖÜ]|#{1,4}\s+\S|Quellen\s*:|Sources\s*:|📊|⚠️)',
        remaining
    )
    if section_end_pattern:
        section_content = remaining[:section_end_pattern.start()]
        section_end = section_header_end + section_end_pattern.start()
    else:
        section_content = remaining
        section_end = len(text)
    
    # ── Guard: Skip analytical sections that contain discussion, not just questions ──
    # If the section has analysis markers, it's analytical content, not a pure Q-list.
    # Pure Q-lists look like: "- Wie wird...?\n- Was sind...?"
    # Analytical sections look like: "a) Datenschutz\nFrage: Wie wird...? Hier ist ..."
    analytical_markers = [
        'Eigene Einschätzung', 'Einschätzung:', 'Begründung:', 'Implikation:',
        'Beispiel:', 'Bewertung:', 'Analyse:', 'Diskussion:', 'Frage:',
    ]
    has_text_markers = any(marker.lower() in section_content.lower() for marker in analytical_markers)
    # Sub-headers like "a) ...", "b) ..." indicate structured analysis, not a question list
    has_sub_headers = bool(re.search(r'^\s*[a-z]\)\s+', section_content, re.MULTILINE))
    if has_text_markers or has_sub_headers:
        logger.debug("Section contains analytical content → skip section extraction, defer to embedded")
        return text, []
    
    # Extract questions from section content
    questions = []
    
    # ── Check for [FOLLOW_UP] inline block within section ──
    followup_inline = re.search(
        r'\[FOLLOW_UP\]\s*(.*?)(?:\[/FOLLOW_UP\]|$)',
        section_content, re.DOTALL | re.IGNORECASE
    )
    if followup_inline:
        raw_block = followup_inline.group(1).strip()
        questions = _parse_question_block(raw_block)
    
    # Bullet-point questions
    if not questions:
        for m in _BULLET_QUESTION_PATTERN.finditer(section_content):
            questions.append(m.group(1).strip())
    
    # Numbered questions
    if not questions:
        for m in _NUMBERED_QUESTION_PATTERN.finditer(section_content):
            q = m.group(1).strip()
            if q not in questions:
                questions.append(q)
    
    # Pipe-separated questions (without [FOLLOW_UP] tags)
    if not questions and '|' in section_content:
        # Strip any tag remnants and split on pipes
        clean_block = re.sub(r'\[/?FOLLOW_UP\]', '', section_content, flags=re.IGNORECASE).strip()
        pipe_questions = [q.strip() for q in clean_block.split('|') if q.strip()]
        if len(pipe_questions) >= 2:
            questions = pipe_questions
    
    # Plain lines ending with ?
    if not questions:
        for line in section_content.strip().split('\n'):
            line = line.strip()
            # Strip [FOLLOW_UP] tags from line
            line = re.sub(r'\[/?FOLLOW_UP\]', '', line, flags=re.IGNORECASE).strip()
            if line.endswith('?') and len(line) >= MIN_QUESTION_LENGTH:
                # Remove leading markers
                line = re.sub(r'^[-•*▸►✦→\d.)\s]+', '', line).strip()
                if line:
                    questions.append(line)
    
    if questions:
        # Clean text: remove the follow-up section
        clean_text = text[:section_start].rstrip()
        after_section = text[section_end:].lstrip()
        if after_section:
            clean_text = clean_text + "\n\n" + after_section
        clean_text = clean_text.strip()
        return clean_text, _validate_questions(questions)
    
    return text, []


def _extract_embedded_questions(text: str) -> List[str]:
    """Extract questions embedded within analytical text (e.g., 'Frage: Wie wird...?').
    
    Unlike structured/section extraction, this does NOT modify the display text.
    The questions are part of the analysis and should remain visible, but we
    also offer them as clickable follow-up buttons for convenience.
    
    Patterns detected:
    - "Frage: Wie wird mit sensiblen Daten umgegangen?"
    - "Frage: Wann und wie wird es für andere Betriebssysteme zugänglich sein?"
    """
    questions = []
    
    # Find all "Frage:" prefixed questions
    for m in _FRAGE_SIMPLE_PATTERN.finditer(text):
        q = m.group(1).strip()
        if q and len(q) >= MIN_QUESTION_LENGTH:
            questions.append(q)
    
    if questions:
        return _validate_questions(questions)
    
    return []


def _parse_question_block(raw_block: str) -> List[str]:
    """Parse a raw text block into individual questions.
    
    Supports:
    - Pipe-separated: "question1|question2|question3"
    - Newline-separated (with optional bullets/numbers)
    - Semicolon-separated
    """
    questions = []
    
    # Try pipe-separated first (most compact structured format)
    if '|' in raw_block and raw_block.count('|') >= 1:
        for q in raw_block.split('|'):
            q = q.strip()
            if q:
                questions.append(q)
        if len(questions) >= 2:
            return questions
    
    # Try newline-separated
    questions = []
    for line in raw_block.split('\n'):
        line = line.strip()
        if not line:
            continue
        # Strip bullet points, numbers, dashes
        line = re.sub(r'^[-•*▸►✦→\d.)\s]+', '', line).strip()
        if line:
            questions.append(line)
    
    if questions:
        return questions
    
    # Try semicolon-separated
    if ';' in raw_block:
        for q in raw_block.split(';'):
            q = q.strip()
            if q:
                questions.append(q)
    
    return questions


def _validate_questions(questions: List[str]) -> List[str]:
    """Validate and deduplicate questions."""
    seen = set()
    valid = []
    
    for q in questions:
        # Length check
        if len(q) < MIN_QUESTION_LENGTH or len(q) > MAX_QUESTION_LENGTH:
            continue
        
        # Ensure it's actually a question (ends with ? or is a clear imperative)
        if not q.endswith('?') and not any(q.lower().startswith(w) for w in [
            'erkläre', 'beschreibe', 'vergleiche', 'analysiere', 'bewerte',
            'nenne', 'liste', 'zeige', 'untersuche'
        ]):
            # Add ? if it looks like a question
            if any(q.lower().startswith(w) for w in [
                'wie', 'was', 'warum', 'wann', 'wo', 'wer', 'welche', 'welcher',
                'welches', 'ist', 'sind', 'hat', 'haben', 'kann', 'können',
                'wird', 'werden', 'sollte', 'könnte', 'würde', 'inwiefern'
            ]):
                q = q.rstrip('.') + '?'
            else:
                continue  # Skip non-questions
        
        # Dedup (case-insensitive)
        q_lower = q.lower()
        if q_lower not in seen:
            seen.add(q_lower)
            valid.append(q)
    
    return valid


def format_followup_for_prompt() -> str:
    """Returns the instruction block to append to SUMMARIZER prompts.
    
    This instructs the LLM to generate follow-up questions in a
    parseable format at the end of its response.
    """
    return (
        "\n\n<followup_instructions>\n"
        "WICHTIG: Generiere am Ende deiner Antwort 2-4 weiterführende Folgefragen, "
        "die dem Nutzer helfen, das Thema zu vertiefen. Diese werden als klickbare Buttons angezeigt.\n"
        f"{FOLLOWUP_PERSPECTIVE_INSTRUCTION}\n"
        "Format: Setze die Fragen in einen [FOLLOW_UP]...[/FOLLOW_UP] Block, getrennt durch |.\n"
        "Beispiel:\n"
        "[FOLLOW_UP]Wie wirkt sich das auf den europäischen Markt aus?|"
        "Welche Alternativen gibt es dazu?|"
        "Was sind die langfristigen Risiken?[/FOLLOW_UP]\n"
        "Die Fragen sollen:\n"
        "- Direkt zum Thema passen und das Gespräch vertiefen\n"
        "- Konkret und spezifisch sein (nicht generisch)\n"
        "- Verschiedene Perspektiven abdecken (z.B. Chancen, Risiken, Vergleiche, Details)\n"
        "- In der Sprache der Nutzerfrage formuliert sein\n"
        "</followup_instructions>"
    )
