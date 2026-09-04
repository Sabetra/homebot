"""
Response generation for psychological sessions.

This module handles AI response generation including:
- Context building and formatting
- Token management and optimization
- LLM integration
- Fallback handling
- Comprehensive user context integration

Extracted from wellbeing_session_interface.py as part of Phase 4 refactoring.
"""

import logging
from typing import Dict, Any, List, Optional, Callable
import streamlit as st

try:
    from i18n import get_current_language as i18n_get_current_language
except Exception:
    i18n_get_current_language = None

logger = logging.getLogger(__name__)


def _normalize_role_alternation(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Ensure strict user/assistant alternation by merging consecutive same-role messages.

    Root cause of 'Conversation roles must alternate' errors:
    - DB may store consecutive messages with identical roles (e.g., two assistant turns
      when the bot retries, or two user turns when the user sends quickly).
    - LLM APIs require strict alternation after the optional system message.

    Strategy:
    - Iterate through messages; if a message has the same role as the previous one,
      append its content to the previous message with a separator.
    - System messages are allowed at the start only; subsequent system messages
      are converted to assistant role (they are bot-generated meta-context).

    Args:
        messages: List of {'role': str, 'content': str} dicts (first may be 'system').

    Returns:
        New list with strict role alternation.
    """
    if not messages:
        return messages

    result: List[Dict[str, str]] = []
    first_is_system = messages[0].get('role') == 'system'

    # Allow system message at position 0
    if first_is_system:
        result.append(messages[0])

    start_idx = 1 if first_is_system else 0

    for i in range(start_idx, len(messages)):
        msg = messages[i]
        role = msg.get('role', 'user')
        content = msg.get('content', '')

        # Skip empty messages
        if not content.strip():
            continue

        # Convert stray system messages (after pos 0) to assistant
        if role == 'system':
            role = 'assistant'

        if result and result[-1]['role'] == role:
            # Merge: append content to previous message
            result[-1]['content'] += '\n\n' + content
        else:
            result.append({'role': role, 'content': content})

    return result


def _care_language_instruction() -> str:
    """Return deterministic language rule for care responses."""
    lang = "de"
    if callable(i18n_get_current_language):
        try:
            lang = i18n_get_current_language() or "de"
        except Exception:
            lang = "de"

    if lang == "bg":
        return "\n- Antworte ausschliesslich auf Bulgarisch."
    if lang == "en":
        return "\n- Respond only in English."
    return "\n- Antworte ausschliesslich auf Deutsch."


# ============================================================================
# SINGLE SOURCE OF TRUTH: CARE ASSISTANT IDENTITY
# ============================================================================
#
# This constant drives ALL paths in the psychological chat pipeline:
#   - Primary path  : ResponseGenerator._build_system_prompt() prepends this
#                     to the formatted session context block
#   - Fallback path : psychological_chat() uses it when care_system_prompt
#                     is None (ResponseGenerator not available)
#   - Emergency path: _build_fallback_messages() uses it when context build fails
#
# Design principles:
#   1. Role clarity  — warm counselor, NOT a general-purpose AI assistant
#   2. No formatting templates — no [THINK]/[FOLLOW_UP] blocks in care conversations
#   3. Hard boundaries — no diagnoses, no medication recommendations
#   4. German-first, concise imperative style for model-facing instructions
#   5. Every sentence earns its token cost
#
CARE_SYSTEM_PROMPT_BASE = (
    "Du bist ein warmer, aufmerksamer Gesprächs- und Reflexionsbegleiter. Du arbeitest aktiv, "
    "kollaborativ und nicht-klinisch: Du hilfst dem Benutzer, Erleben, Muster, Bedürfnisse und "
    "nächste Schritte klarer zu verstehen, ohne eine Psychotherapie oder Diagnostik vorzutäuschen.\n\n"
    "ARBEITSWEISE:\n"
    "- Antworte zuerst konkret auf das aktuelle Anliegen und benenne den emotionalen Kern präzise.\n"
    "- Entwickle, wenn die Daten es tragen, genau eine vorsichtige Hypothese über "
    "ein mögliches Muster oder Bedürfnis. Markiere sie als Hypothese und prüfe sie am Erleben des Benutzers.\n"
    "- Verbinde relevante frühere Themen natürlich mit dem aktuellen Turn; behandle Profil- und "
    "KG-Angaben als fallible Hinweise, niemals als Fakten gegen die aktuelle Aussage.\n"
    "- Biete eine kleine, umsetzbare Reflexion oder einen praktischen Schritt an, wenn es passt.\n"
    "- Stelle höchstens eine fokussierte offene Frage. Stelle keine Fragenliste und verhöre nicht.\n"
    "- Bei konkreten Aufgaben: Führe sie vollständig aus; die Reflexion/Einordnung darf die "
    "Aufgabe ergänzen, aber nicht ersetzen.\n\n"
    "GRENZEN:\n"
    "- Stelle keine Diagnosen und behaupte keine Gewissheit über Motive, Persönlichkeit oder Ursachen.\n"
    "- Empfiehl, verändere oder bewerte keine Medikamente oder Dosierungen. Verweise dafür an "
    "qualifizierte medizinische Fachpersonen.\n"
    "- Behaupte Online-Recherche und nenne externe URLs nur, wenn der aktuelle Request ausdrücklich "
    "verifizierte Web-Tool-Ergebnisse im Quellen-Provenienzblock bereitstellt.\n"
    "- Akute Sicherheitsreaktionen werden außerhalb der freien Antwortgenerierung deterministisch gesteuert.\n\n"
    "STIL:\n"
    "- Direkt, warm, prägnant und menschlich; keine leeren Empathiefloskeln.\n"
    "- Natürliche Prosa ohne [THINK]-Blöcke, [FOLLOW_UP]-Listen oder klinischen Jargon.\n"
    "- Folge immer der aktiv ausgewaehlten UI-Sprache."
)

class ResponseGenerator:
    """Generates AI responses for psychological sessions."""
    
    def __init__(
        self, 
        session_manager: Any,
        context_manager: Any,
        chat_logic: Optional[Any] = None
    ) -> None:
        """
        Initialize response generator.
        
        Args:
            session_manager: Session manager instance
            context_manager: Context manager instance
            chat_logic: Optional chat logic instance
        """
        self.session_manager = session_manager
        self.context_manager = context_manager
        self.chat_logic = chat_logic
        
    def generate_psychological_response(
        self,
        user_input: str,
        build_context_func: Callable,
        format_context_func: Callable,
        load_history_func: Optional[Callable] = None
    ) -> str:
        """
        Generate a psychological AI response with ALL core modules integrated.
        
        Args:
            user_input: User input text
            build_context_func: Function to build comprehensive user context
            format_context_func: Function to format context for LLM
            load_history_func: Optional function to load session history into chat logic
            
        Returns:
            Generated AI response
        """
        session_id = st.session_state.psych_current_session
        session_info = self.session_manager.get_session_summary(session_id)
        if not session_info:
            raise RuntimeError(
                f"Session {session_id[:12]}... has no persisted summary record"
            )
        user_id = str(session_info.get("user_id") or "").strip()
        if not user_id:
            raise RuntimeError(
                f"Session {session_id[:12]}... has no persisted user identity"
            )

        comprehensive_context = {}  # Empty context as fallback
        messages = []  # Initialize messages outside try block
        optimized_messages = []
        
        try:
            # === BUILD COMPREHENSIVE USER CONTEXT ===
            logger.info(
                f"🧠 [COMPREHENSIVE CONTEXT] Lade User-Profil für User: {user_id}, "
                f"Session: {session_id[:12]}..."
            )
            
            comprehensive_context = build_context_func(
                user_id=user_id,
                current_session_id=session_id,
                user_input=user_input
            )
            
            # Format context for LLM
            formatted_context = format_context_func(comprehensive_context)
            print(
                f"✅ [COMPREHENSIVE CONTEXT] User-Profil geladen "
                f"(~{comprehensive_context.get('context_token_estimate', 0)} Token)"
            )
            
            # Estimate context tokens before optimization
            context_token_estimate = comprehensive_context.get('context_token_estimate', 0)
            logger.info(f"🔍 [CONTEXT_TOKENS] Geschätzt: {context_token_estimate} Token für Context")
            
            # ADAPTIVE token budgets based on context size
            max_messages_for_history = self._calculate_adaptive_message_limit(context_token_estimate)
            
            # Convert session messages to LLM format
            messages = self.session_manager.get_session_context(
                st.session_state.psych_current_session, 
                max_messages=max_messages_for_history
            )
            
            # ✅ RC-1 FIX: System-Prompt IMMER bauen — auch wenn keine vorherigen
            # Session-Messages existieren (neue Session, erster Turn).
            # Vorher war dies im `if messages:` Block → bei leerem History
            # blieb optimized_messages = [], care_system_prompt = None,
            # und psychological_chat() fiel auf DEFAULT_SYSTEM_PROMPT zurück.
            system_prompt = self._build_system_prompt(formatted_context)
            llm_messages = [{'role': 'system', 'content': system_prompt}]
            
            # Add session history messages if available
            if messages:
                for msg in messages:
                    llm_messages.append({
                        'role': msg.get('role', 'user'),
                        'content': msg.get('content', '')
                    })

            # ✅ RC-3 FIX: Normalize role alternation before LLM call.
            # Consecutive same-role messages (e.g., two assistant turns) cause
            # "Conversation roles must alternate user/assistant/..." errors.
            llm_messages = _normalize_role_alternation(llm_messages)

            # Use context manager for automatic summarization if needed
            optimized_messages, summary_info = self.context_manager.manage_context(llm_messages)
            
            if summary_info:
                logger.info("🔄 Chat-Kontext wurde automatisch zusammengefasst für psychologische Session")
            
            total_tokens = self.context_manager.calculate_total_tokens(optimized_messages)
            logger.info(
                f"📊 Optimized messages: {total_tokens} tokens "
                f"(max: {self.context_manager.max_context_tokens})"
            )
            
            # Create contextual input with current message
            current_prompt = self._build_current_prompt(user_input)
            optimized_messages.append({
                'role': 'user',
                'content': current_prompt
            })
                
        except Exception as e:
            logger.error(
                f"❌ Context-Build FAILED: {e} | user_id={user_id}, session_id={session_id[:12]}... "
                f"| comprehensive_context leer: {not bool(comprehensive_context)} "
                f"| messages: {len(messages)}"
            )
            import traceback
            traceback.print_exc()
            # Fallback to simple context
            optimized_messages = self._build_fallback_messages(user_input)
        
        # Use chat logic for better responses
        if self.chat_logic:
            # ✅ DIAGNOSTIC: Log was an psychological_chat übergeben wird
            kg_count = len(comprehensive_context.get('knowledge_graph', []))
            summ_count = len(comprehensive_context.get('session_summaries', []))
            logger.info(
                f"🔗 [HANDOFF] → psychological_chat | "
                f"user_id={user_id} | "
                f"KG_triples={kg_count} | "
                f"session_summaries={summ_count} | "
                f"history_msgs={len(messages)} | "
                f"comprehensive_context_keys={list(comprehensive_context.keys())}"
            )
            return self._generate_with_chat_logic(
                optimized_messages,
                user_input,
                user_id,
                session_id,
                comprehensive_context,
                messages,
                load_history_func
            )
        
        # Try session chat logic fallback
        if st.session_state.get('initialized') and st.session_state.get('chat_logic'):
            return self._generate_with_session_chat_logic(
                optimized_messages,
                user_input,
                messages,
                load_history_func
            )
        
        # Final fallback: simple response
        return self._build_fallback_response(user_input)
    
    def _calculate_adaptive_message_limit(self, context_token_estimate: int) -> int:
        """
        Calculate adaptive message limit based on context size.
        
        Args:
            context_token_estimate: Estimated tokens for context
            
        Returns:
            Maximum number of history messages
        """
        if context_token_estimate > 1500:
            # Large context: reduce session history
            max_messages = 20
            logger.info(f"⚡ [ADAPTIVE] Großer Context → max {max_messages} History-Messages")
        elif context_token_estimate > 800:
            # Medium context: normal session history
            max_messages = 30
            logger.info(f"⚡ [ADAPTIVE] Mittlerer Context → max {max_messages} History-Messages")
        else:
            # Small context: full session history
            max_messages = 50
            logger.info(f"⚡ [ADAPTIVE] Kleiner Context → max {max_messages} History-Messages")
        
        return max_messages
    
    def _build_system_prompt(self, formatted_context: str) -> str:
        """
        Builds the full care system prompt by combining the role identity
        (CARE_SYSTEM_PROMPT_BASE) with the formatted session context block.

        The context block contains: KG triples, session summaries, mood progression,
        care goals, user insights, and the persistent psychological profile.
        Prepending the base identity ensures the model never loses its care
        role even in long-context sessions where instructions can fade.
        """
        return CARE_SYSTEM_PROMPT_BASE + _care_language_instruction() + "\n\n" + formatted_context
    
    def _build_current_prompt(self, user_input: str) -> str:
        """
        Returns the user's raw input as the current turn message.

        The LLM has full session context in the system prompt — wrapping the user
        message in meta-language ("AKTUELLE BENUTZER-NACHRICHT: ...") adds noise
        and breaks the natural conversation format expected by chat models.
        """
        return user_input
    
    def _build_fallback_messages(self, user_input: str) -> List[Dict[str, str]]:
        """
        Minimal message list used when context build fails completely.

        Uses CARE_SYSTEM_PROMPT_BASE (without context block) so the model
        still behaves as a care companion rather than a generic assistant.
        The user's raw input is passed as-is — no meta-wrapping.
        """
        return [
            {'role': 'system', 'content': CARE_SYSTEM_PROMPT_BASE + _care_language_instruction()},
            {'role': 'user',   'content': user_input},
        ]
    
    def _build_fallback_response(self, user_input: str) -> str:
        """
        Build fallback response when all generation attempts fail.
        
        Args:
            user_input: User input text
            
        Returns:
            Fallback response
        """
        return f"""Ich höre Ihnen zu und verstehe, dass Sie über "{user_input}" sprechen möchten. 

Das sind wichtige Gedanken, die Sie mit mir teilen. Können Sie mir mehr darüber erzählen, was Sie in dieser Situation empfinden?"""
    
    def _generate_with_chat_logic(
        self,
        optimized_messages: List[Dict[str, str]],
        user_input: str,
        user_id: str,
        session_id: str,
        comprehensive_context: Dict[str, Any],
        messages: List[Dict[str, Any]],
        load_history_func: Optional[Callable]
    ) -> str:
        """Generate response using chat logic with full context."""
        try:
            # Convert comprehensive context to session context format
            session_context_dict = self._convert_to_session_context(
                comprehensive_context,
                user_id,
                session_id
            )
            
            logger.info(
                f"✅ [CONTEXT-CONVERSION] Comprehensive → Session-Context: "
                f"KG={len(session_context_dict.get('knowledge_graph', []))} Triples, "
                f"PrevSessions={len(session_context_dict.get('previous_sessions', []))}"
            )
            
            # Extract the care system prompt from optimized_messages
            # (built by _build_system_prompt with full formatted context)
            care_system_prompt = None
            for msg in optimized_messages:
                if msg.get('role') == 'system':
                    care_system_prompt = msg['content']
                    break
            
            if care_system_prompt:
                logger.info(f"✅ [PSYCHO-CHAT] Care-System-Prompt wird an psychological_chat übergeben ({len(care_system_prompt)} Zeichen)")
            
            # Use psychological_chat() with:
            # 1. session_context (KG, previous sessions, mood, goals, insights)
            # 2. session_history from DB (conversation continuity)
            # 3. care_system_prompt (formatted context as system prompt)
            if self.chat_logic is None:
                return "Entschuldigung, Chat-Logik ist nicht verfügbar."
            
            # RC-1 FIX: Übergebe den Care-System-Prompt
            # Damit nutzt psychological_chat() diesen statt DEFAULT_SYSTEM_PROMPT
            response = self.chat_logic.psychological_chat(
                user_input,
                session_context=session_context_dict,
                session_history=messages,
                care_system_prompt=care_system_prompt,
            )
            
            return str(response)
            
        except Exception as e:
            logger.error(f"Error generating psychological response with chat_logic: {e}")
            import traceback
            traceback.print_exc()
            return "Entschuldigung, es gab einen technischen Fehler bei der Antwortgenerierung. Bitte versuche es erneut."
    
    def _generate_with_session_chat_logic(
        self,
        optimized_messages: List[Dict[str, str]],
        user_input: str,
        messages: List[Dict[str, Any]],
        load_history_func: Optional[Callable]
    ) -> str:
        """Generate response using session chat logic fallback."""
        try:
            combined_prompt = self._combine_messages(optimized_messages)
            
            # Check final prompt size
            final_tokens = self.context_manager.estimate_tokens(combined_prompt)
            
            if final_tokens > 7000:
                combined_prompt = self._reduce_prompt_size(optimized_messages)
                final_tokens = self.context_manager.estimate_tokens(combined_prompt)
                logger.info(f"✅ Nach Fallback-Reduktion: {final_tokens} tokens (Context erhalten!)")
            
            logger.debug(f"🔍 [FALLBACK] Finale Prompt-Größe: {final_tokens} tokens")
            
            # Load session history
            if load_history_func and messages:
                load_history_func(messages, st.session_state.chat_logic)
            
            # ✅ FIXED: Build proper session context from session_context_builder
            # instead of passing empty dict
            session_context_dict: Dict[str, Any] = {}
            try:
                if hasattr(st.session_state, 'psych_interface') and st.session_state.psych_interface:
                    built_context = st.session_state.psych_interface._build_session_context(user_query=user_input)
                    if built_context:
                        session_context_dict = built_context
                        logger.info(
                            f"✅ [FALLBACK] Session-Context aus psych_interface gebaut: "
                            f"KG={len(session_context_dict.get('knowledge_graph', []))}"
                        )
            except Exception as ctx_err:
                logger.warning(f"⚠️ [FALLBACK] Session-Context konnte nicht gebaut werden: {ctx_err}")
            
            # RC-1 FIX: Baue Care-System-Prompt auch im Fallback-Pfad
            fallback_care_prompt = None
            for msg in optimized_messages:
                if msg.get('role') == 'system':
                    fallback_care_prompt = msg['content']
                    break
            
            # Use psychological_chat() with session context AND history
            response = st.session_state.chat_logic.psychological_chat(
                user_input,
                session_context=session_context_dict,
                session_history=messages,
                care_system_prompt=fallback_care_prompt,
            )
            
            return str(response)
            
        except Exception as e:
            logger.error(f"Error generating psychological response with session chat_logic: {e}")
            return "Entschuldigung, es gab einen technischen Fehler bei der Antwortgenerierung. Bitte versuche es erneut."
    
    def _combine_messages(self, messages: List[Dict[str, str]]) -> str:
        """Combine messages into a single prompt."""
        combined_prompt = ""
        
        for msg in messages:
            if msg['role'] == 'system':
                combined_prompt += f"SYSTEM: {msg['content']}\n\n"
            elif msg['role'] == 'user':
                combined_prompt += f"USER: {msg['content']}\n\n"
            elif msg['role'] == 'assistant':
                combined_prompt += f"ASSISTANT: {msg['content']}\n\n"
        
        combined_prompt += "Bitte antworten Sie als psychologischer Berater:"
        
        return combined_prompt
    
    def _reduce_prompt_size(self, optimized_messages: List[Dict[str, str]]) -> str:
        """
        Reduce prompt size by keeping context and reducing history.
        
        Args:
            optimized_messages: Optimized messages list
            
        Returns:
            Reduced combined prompt
        """
        logger.warning(f"⚠️ Combined prompt zu groß")
        
        # PSYCHO-AWARE REDUCTION: Keep context, reduce HISTORY!
        # Strategy: System prompt (with context!) + few messages
        
        system_msgs = [msg for msg in optimized_messages if msg.get('role') == 'system']
        non_system_msgs = [msg for msg in optimized_messages if msg.get('role') != 'system']
        
        # Keep system prompt (contains comprehensive context!)
        # Reduce only history to last 6-8 messages
        emergency_messages = system_msgs + non_system_msgs[-8:]
        
        logger.info(
            f"🧠 [PSYCHO-REDUCTION] System: {len(system_msgs)}, "
            f"History: {len(non_system_msgs[-8:])} Messages"
        )
        
        return self._combine_messages(emergency_messages)
    
    def _convert_to_session_context(
        self,
        comprehensive_context: Dict[str, Any],
        user_id: str,
        session_id: str
    ) -> Dict[str, Any]:
        """
        Convert comprehensive context to session context format.
        
        Args:
            comprehensive_context: Comprehensive context dict
            user_id: User ID
            session_id: Session ID
            
        Returns:
            Session context dict
        """
        # Extract previous sessions from session_summaries (EARLIER sessions, not current!)
        previous_sessions_list = comprehensive_context.get('session_summaries', [])
        logger.info(
            f"🔍 [CONTEXT-CONV] previous_sessions aus comprehensive_context: "
            f"{len(previous_sessions_list)} Sessions"
        )
        if previous_sessions_list:
            logger.debug(
                f"   → Previous Sessions: "
                f"{[s.get('session_id', 'N/A')[:12] for s in previous_sessions_list]}"
            )
        
        session_context_dict = {
            'user_id': user_id,
            'user_name': st.session_state.psych_current_user,  # Display name for personal address
            'session_id': session_id,
            'mood': comprehensive_context.get('mood_progression', {}).get('current_mood', 'neutral'),
            'goals': comprehensive_context.get('care_goals', []),
            'summary': (
                comprehensive_context.get('session_summaries', [{}])[0].get('summary', '') 
                if comprehensive_context.get('session_summaries') else ''
            ),
            'knowledge_graph': comprehensive_context.get('knowledge_graph', []),
            'previous_sessions': previous_sessions_list,
            'mood_progression': comprehensive_context.get('mood_progression'),
            'user_insights': comprehensive_context.get('user_insights', []),
            'persistent_profile': comprehensive_context.get('persistent_profile'),
        }
        
        return session_context_dict

