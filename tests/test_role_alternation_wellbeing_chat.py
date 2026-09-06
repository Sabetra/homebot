"""
Test: Verifiziert, ob wellbeing_chat() in agent_chatbot_logic.py
die Rolle-Alternation (user/assistant/user/assistant) garantiert.

Hypothese: wellbeing_chat() Normalisiert llm_messages NICHT vor dem
LLM-Aufruf. Bei Session-History mit konsekutiven gleichen Rollen
tritt "Conversation roles must alternate user/assistant/..." auf.

Belege:
- response_generator.py:248 ruft _normalize_role_alternation() vor LLM-Call
- agent_chatbot_logic.py:3722 ruft generate_response() OHNE Normalisierung
"""

import pytest
from typing import List, Dict, Any


def _check_role_alternation(messages: List[Dict[str, str]]) -> bool:
    """
    Check if messages follow proper role alternation after the first user message.
    System messages at the start are allowed. After the first 'user' message,
    roles must strictly alternate user/assistant/user/assistant.
    
    Returns True if alternation is valid, False if consecutive same roles exist.
    """
    if not messages:
        return True
    
    # Skip leading system messages
    idx = 0
    while idx < len(messages) and messages[idx].get('role') == 'system':
        idx += 1
    
    # After system messages, must alternate user/assistant
    while idx < len(messages):
        role = messages[idx].get('role')
        if role not in ('user', 'assistant'):
            # Unknown role - might cause issues
            idx += 1
            continue
        
        # Check next non-system message
        next_idx = idx + 1
        while next_idx < len(messages) and messages[next_idx].get('role') == 'system':
            next_idx += 1
        
        if next_idx < len(messages):
            next_role = messages[next_idx].get('role')
            if next_role in ('user', 'assistant') and role == next_role:
                return False  # Consecutive same roles!
        
        idx = next_idx
    
    return True


def _build_llm_messages_like_psych_chat(
    system_prompt: str,
    session_history: List[Dict[str, str]],
    user_prompt: str,
) -> List[Dict[str, str]]:
    """
    Simuliert exakt den llm_messages-Aufbau wie in 
    agent_chatbot_logic.py:wellbeing_chat() Zeilen 3133-3169.
    
    Steps:
    1. llm_messages = []
    2. llm_messages.append({'role': 'system', ...})
    3. for msg in session_history: llm_messages.append(msg)
    4. llm_messages.append({'role': 'user', ...})
    
    WICHTIG: Es gibt KEINEN _normalize_role_alternation() Aufruf!
    """
    llm_messages = []
    
    # Step 1: System prompt (Zeile 3152)
    if system_prompt:
        llm_messages.append({'role': 'system', 'content': system_prompt})
    
    # Step 2: Session history (Zeilen 3157-3166)
    # Filter: nur user/assistant roles, keine Duplikate der aktuellen Nachricht
    if session_history:
        history_msgs = [
            msg for msg in session_history
            if msg.get('role') in ['user', 'assistant']
            and msg.get('content') != user_prompt
        ]
        for msg in history_msgs:
            llm_messages.append({
                'role': msg.get('role'),
                'content': msg.get('content')
            })
    
    # Step 3: User prompt (Zeile 3169)
    llm_messages.append({'role': 'user', 'content': user_prompt})
    
    return llm_messages


class TestRoleAlternationPsychologicalChat:
    """
    Tests die Rolle-Alternation im wellbeing_chat-Pfad.
    """
    
    def test_consecutive_user_messages_causes_violation(self):
        """
        Zwei konsekutive user-Messages in der Session-History
        führen zu Rolle-Verletzung.
        """
        session_history = [
            {'role': 'user', 'content': 'Ich fühle mich depressiv'},
            {'role': 'assistant', 'content': 'Das klingt schwer. Erzählen Sie mehr.'},
            {'role': 'user', 'content': 'Seit Wochen schlaf ich schlecht'},
            # Konsekutiver user (z.B. durch DB-Fehler oder Race-Condition):
            {'role': 'user', 'content': 'Und ich habe keine Motivation'},
        ]
        
        messages = _build_llm_messages_like_psych_chat(
            system_prompt="Du bist ein Therapeut.",
            session_history=session_history,
            user_prompt="Können Sie mir helfen?",
        )
        
        # Dieser Test zeigt: ohne Normalisierung ist die Alternation NICHT garantiert
        assert not _check_role_alternation(messages), (
            "EXPECTED: Rolle-Alternationsverletzung bei konsekutiven user-Messages. "
            "Ohne _normalize_role_alternation() wird dieser Fehler an das LLM weitergegeben."
        )
    
    def test_consecutive_assistant_messages_causes_violation(self):
        """
        Zwei konsekutive assistant-Messages in der Session-History
        führen zu Rolle-Verletzung.
        """
        session_history = [
            {'role': 'user', 'content': 'Ich fühle mich depressiv'},
            {'role': 'assistant', 'content': 'Das klingt schwer.'},
            # Konsekutiver assistant (z.B. durch Logging-Fehler):
            {'role': 'assistant', 'content': 'Erzählen Sie mehr.'},
            {'role': 'user', 'content': 'Seit Wochen schlaf ich schlecht'},
        ]
        
        messages = _build_llm_messages_like_psych_chat(
            system_prompt="Du bist ein Therapeut.",
            session_history=session_history,
            user_prompt="Können Sie mir helfen?",
        )
        
        assert not _check_role_alternation(messages), (
            "EXPECTED: Rolle-Alternationsverletzung bei konsekutiven assistant-Messages."
        )
    
    def test_valid_alternation_passes(self):
        """
        Korrekte Alternation (user/assistant/user/assistant) 
        sollte ohne Probleme funktionieren.
        """
        session_history = [
            {'role': 'user', 'content': 'Ich fühle mich depressiv'},
            {'role': 'assistant', 'content': 'Das klingt schwer. Erzählen Sie mehr.'},
            {'role': 'user', 'content': 'Seit Wochen schlaf ich schlecht'},
            {'role': 'assistant', 'content': 'Das ist ein wichtiges Signal.'},
        ]
        
        messages = _build_llm_messages_like_psych_chat(
            system_prompt="Du bist ein Therapeut.",
            session_history=session_history,
            user_prompt="Können Sie mir helfen?",
        )
        
        assert _check_role_alternation(messages), (
            "Unerwartet: Gültige Alternation wurde als ungültig erkannt."
        )
    
    def test_empty_history_is_valid(self):
        """
        Leere Session-History (erste Nachricht) sollte immer gültig sein.
        """
        messages = _build_llm_messages_like_psych_chat(
            system_prompt="Du bist ein Therapeut.",
            session_history=[],
            user_prompt="Hallo, ich brauche Hilfe.",
        )
        
        assert _check_role_alternation(messages)
        # Struktur: [system, user]
        assert messages[0]['role'] == 'system'
        assert messages[1]['role'] == 'user'
    
    def test_single_history_message_valid(self):
        """
        Einzelne History-Message (user) vor aktueller user-Message:
        Das ist ein Edge-Case der auch zur Verletzung führen kann,
        wenn die History mit 'user' endet und der aktuelle Prompt auch 'user' ist.
        """
        session_history = [
            {'role': 'user', 'content': 'Vorherige Nachricht'},
        ]
        
        messages = _build_llm_messages_like_psych_chat(
            system_prompt="Du bist ein Therapeut.",
            session_history=session_history,
            user_prompt="Aktuelle Nachricht.",
        )
        
        # History[user] + current[user] = konsekutive user-Messages!
        assert not _check_role_alternation(messages), (
            "EXPECTED: History[user] + current[user] = konsekutive user-Messages."
        )


class TestRoleAlternationNormalizationFix:
    """
    Tests, dass _normalize_role_alternation() (aus response_generator.py)
    die Verletzungen korrekt behebt.
    """
    
    def _normalize_role_alternation(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Kopie der Logik aus response_generator.py:26-78.
        Merge consecutive same-role messages.
        """
        if not messages:
            return messages
        
        result = [messages[0]]
        
        for msg in messages[1:]:
            if msg.get('role') == 'system':
                # System messages can appear anywhere without breaking alternation
                result.append(msg)
                continue
            
            last = result[-1]
            
            if last.get('role') == msg.get('role'):
                # Merge consecutive same-role messages
                merged_content = last.get('content', '') + '\n\n' + msg.get('content', '')
                result[-1] = {
                    'role': msg.get('role'),
                    'content': merged_content,
                }
            else:
                result.append(msg)
        
        return result
    
    def test_normalization_fixes_consecutive_user(self):
        """
        _normalize_role_alternation() behebt konsekutive user-Messages.
        """
        session_history = [
            {'role': 'user', 'content': 'Ich fühle mich depressiv'},
            {'role': 'assistant', 'content': 'Das klingt schwer.'},
            {'role': 'user', 'content': 'Seit Wochen schlaf ich schlecht'},
            {'role': 'user', 'content': 'Und ich habe keine Motivation'},
        ]
        
        messages = _build_llm_messages_like_psych_chat(
            system_prompt="Du bist ein Therapeut.",
            session_history=session_history,
            user_prompt="Können Sie mir helfen?",
        )
        
        # Vor der Normalisierung: Verletzung
        assert not _check_role_alternation(messages)
        
        # Nach der Normalisierung: gültig
        normalized = self._normalize_role_alternation(messages)
        assert _check_role_alternation(normalized), (
            "_normalize_role_alternation() sollte konsekutive user-Messages mergen."
        )
    
    def test_normalization_fixes_consecutive_assistant(self):
        """
        _normalize_role_alternation() behebt konsekutive assistant-Messages.
        """
        session_history = [
            {'role': 'user', 'content': 'Ich fühle mich depressiv'},
            {'role': 'assistant', 'content': 'Das klingt schwer.'},
            {'role': 'assistant', 'content': 'Erzählen Sie mehr.'},
            {'role': 'user', 'content': 'Seit Wochen schlaf ich schlecht'},
        ]
        
        messages = _build_llm_messages_like_psych_chat(
            system_prompt="Du bist ein Therapeut.",
            session_history=session_history,
            user_prompt="Können Sie mir helfen?",
        )
        
        assert not _check_role_alternation(messages)
        
        normalized = self._normalize_role_alternation(messages)
        assert _check_role_alternation(normalized)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])