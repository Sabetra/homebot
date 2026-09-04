#!/usr/bin/env python3
"""
INTELLIGENTER CHAT-KONTEXT-MANAGER (SOTA)
==========================================

Ein robustes System zur Chat-Verlaufs-Verwaltung mit SOTA LLM-basierter
rekursiver Zusammenfassung bei Kontextfenster-Überlauf.

Features:
- Exakte Token-Zählung mit geladenem LLM
- **SOTA: Rekursive LLM-basierte Zusammenfassung (Map-Reduce)**
- **SOTA: Salience-basierte Nachrichten-Priorisierung**
- **SOTA: Inkrementelle Running-Summary-Aktualisierung**
- Multi-Source Context Budgeting (History, RAG, Tools, System)
- Prioritätsbasierte Nachrichtenselektion
- Dynamische RAG-Reserve-Anpassung
- Fallback-Mechanismen

✅ Phase 9b: Upgraded from rule-based to SOTA recursive LLM summarization.
"""

import os
import logging
import json
import time
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from scripts.model_loader import ModelLoader

logger = logging.getLogger(__name__)

# ── SOTA: Recursive summarizer (always importable, degrades gracefully) ──
try:
    from wellbeing_session.workflow.recursive_summarizer import (
        RecursiveLLMSummarizer,
        SalienceScorer,
    )
    RECURSIVE_SUMMARIZER_AVAILABLE = True
except ImportError:
    RECURSIVE_SUMMARIZER_AVAILABLE = False
    RecursiveLLMSummarizer = None  # type: ignore[assignment,misc]
    SalienceScorer = None  # type: ignore[assignment,misc]
    logger.info("ℹ️ RecursiveLLMSummarizer not available -- using built-in fallback")

class ChatContextManager:
    """
    Intelligenter Chat-Kontext-Manager mit exakter Tokenisierung und Multi-Source Budgeting
    """
    
    def __init__(self, 
                 model_loader: Optional['ModelLoader'] = None,
                 max_context_tokens: Optional[int] = None,  # Auto-detect from model if None
                 rag_reserve_tokens: int = 4096,  # SOTA: Verdoppelt für reichhaltigeren RAG-Kontext (n_ctx=12288 Budget)
                 system_prompt_tokens: int = 512,  # Estimated System-Prompt size
                 summary_target_tokens: int = 2000,  # Target size for chat summaries (increased from 1024 for psych summaries)
                 min_recent_messages: int = 6,  # Minimum recent messages to keep
                 tools_reserve_tokens: int = 0):  # 0 = auto-calculate based on tool count
        """
        Initialisiert den Context Manager mit exakter Tokenisierung
        
        Args:
            model_loader: ModelLoader instance for exact tokenization
            max_context_tokens: Maximum tokens for entire context (auto-detected if None)
            rag_reserve_tokens: Reserved tokens for RAG context
            system_prompt_tokens: Estimated tokens for system prompt
            summary_target_tokens: Target size for chat summaries
            min_recent_messages: Minimum number of recent messages to keep
            tools_reserve_tokens: Reserved tokens for tool responses (0 = auto-calculate)
        """
        self.model_loader = model_loader
        
        # ── SOTA: Initialize recursive LLM summarizer ──
        self._recursive_summarizer: Optional[Any] = None
        self._running_summary: str = ""  # Incremental running summary
        self._running_summary_msg_count: int = 0  # Messages covered by running summary
        if RECURSIVE_SUMMARIZER_AVAILABLE and RecursiveLLMSummarizer is not None:
            self._recursive_summarizer = RecursiveLLMSummarizer(
                model_loader=model_loader,
                chunk_size=8,
                max_summary_tokens=summary_target_tokens,
                salience_threshold=4,
                token_estimator=self.estimate_tokens if model_loader else None,
            )
            logger.info("✅ SOTA: RecursiveLLMSummarizer initialized")
        
        # Auto-detect max context tokens from model
        if max_context_tokens is None and model_loader:
            detected_max = model_loader.get_max_context_tokens()
            if detected_max > 0:
                max_context_tokens = detected_max
                logger.info(f"🔍 Auto-detected max context: {max_context_tokens} tokens")
            else:
                max_context_tokens = 8192  # Conservative fallback
                logger.warning(f"⚠️ Could not detect context size, using fallback: {max_context_tokens}")
        elif max_context_tokens is None:
            max_context_tokens = 8192  # Fallback if no model loader
            logger.warning(f"⚠️ No model_loader provided, using fallback context: {max_context_tokens}")
        
        self.max_context_tokens = max_context_tokens
        self.rag_reserve_tokens = rag_reserve_tokens
        self.system_prompt_tokens = system_prompt_tokens
        self.summary_target_tokens = summary_target_tokens
        self.min_recent_messages = min_recent_messages
        self.tools_reserve_tokens = tools_reserve_tokens
        
        # SOTA: Auto-calculate tools reserve based on actual tool count
        if self.tools_reserve_tokens == 0:
            try:
                from agent.tool_schemas import get_toolkit_format_schemas
                tool_schemas = get_toolkit_format_schemas()
                tool_count = len(tool_schemas) if tool_schemas else 10
                # Each tool schema ~ 70 tokens (name + description + params)
                # Plus overhead for tool call format and response parsing
                self.tools_reserve_tokens = max(256, tool_count * 70 + 128)
                logger.info(f"🔧 Auto-calculated tools_reserve_tokens: {self.tools_reserve_tokens} ({tool_count} tools)")
            except Exception:
                # Fallback: estimate based on typical bot configuration (8-12 tools)
                self.tools_reserve_tokens = 768  # ~10 tools * 70 + overhead
                logger.debug("Auto-calculated tools_reserve_tokens with fallback: 768")
        
        # Calculate available tokens for chat history
        # Formula: max_context - system - rag_reserve - summary - tools - safety_buffer
        safety_buffer = 256  # Extra safety margin
        self.available_chat_tokens = (
            max_context_tokens 
            - system_prompt_tokens 
            - rag_reserve_tokens 
            - summary_target_tokens 
            - tools_reserve_tokens
            - safety_buffer
        )
        
        # Ensure we have at least some space for chat
        if self.available_chat_tokens < 512:
            logger.error(f"❌ CRITICAL: Available chat tokens too low ({self.available_chat_tokens})")
            # Reduce reserves to make room
            self.rag_reserve_tokens = max(512, rag_reserve_tokens // 2)
            self.available_chat_tokens = (
                max_context_tokens 
                - system_prompt_tokens 
                - self.rag_reserve_tokens 
                - summary_target_tokens 
                - tools_reserve_tokens
                - safety_buffer
            )
            logger.warning(f"⚠️ Reduced RAG reserve to {self.rag_reserve_tokens}, chat now has {self.available_chat_tokens} tokens")
        
        logger.info(f"🧠 Chat Context Manager initialisiert (EXACT TOKENIZATION):")
        logger.info(f"   Max Context: {max_context_tokens} tokens")
        logger.info(f"   System Prompt: {system_prompt_tokens} tokens")
        logger.info(f"   RAG Reserve: {rag_reserve_tokens} tokens")
        logger.info(f"   Tools Reserve: {tools_reserve_tokens} tokens")
        logger.info(f"   Summary Target: {summary_target_tokens} tokens")
        logger.info(f"   Safety Buffer: {safety_buffer} tokens")
        logger.info(f"   ➜ Chat Available: {self.available_chat_tokens} tokens")
        
    def estimate_tokens(self, text: str) -> int:
        """
        Zählt exakte Token-Anzahl für gegebenen Text (oder approximiert als Fallback)
        
        Args:
            text: Text zur Analyse
            
        Returns:
            Token-Anzahl
        """
        # Use exact tokenization if model_loader is available
        if self.model_loader:
            try:
                return self.model_loader.count_tokens(text)
            except Exception as e:
                logger.warning(f"Exact tokenization failed, using approximation: {e}")
        
        # Fallback: Approximation (4 Zeichen ≈ 1 Token für Deutsch)
        return max(1, len(text) // 4)
    
    def estimate_message_tokens(self, message: Dict[str, Any]) -> int:
        """
        Schätzt Token für eine einzelne Nachricht
        
        Args:
            message: Nachricht im LLM-Format
            
        Returns:
            Geschätzte Token-Anzahl
        """
        content = message.get('content', '')
        
        # Handle multimodal content
        if isinstance(content, list):
            total_tokens = 0
            for item in content:
                if item.get('type') == 'text':
                    total_tokens += self.estimate_tokens(item.get('text', ''))
                elif item.get('type') == 'image_url':
                    total_tokens += 85  # Standard-Token für Bilder in GPT-4V
            return total_tokens + 10  # 10 Token für Message-Struktur
        else:
            return self.estimate_tokens(str(content)) + 10
    
    def calculate_total_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        Berechnet Gesamt-Token für Message-Liste
        
        Args:
            messages: Liste von Nachrichten
            
        Returns:
            Gesamt-Token-Anzahl
        """
        return sum(self.estimate_message_tokens(msg) for msg in messages)
    
    def analyze_context_usage(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analysiert aktuelle Context-Nutzung
        
        Args:
            messages: Aktuelle Message-Liste
            
        Returns:
            Analyse-Ergebnisse
        """
        total_tokens = self.calculate_total_tokens(messages)
        usage_percentage = (total_tokens / self.max_context_tokens) * 100
        
        return {
            'total_tokens': total_tokens,
            'max_tokens': self.max_context_tokens,
            'available_tokens': max(0, self.max_context_tokens - total_tokens),
            'usage_percentage': usage_percentage,
            'needs_summarization': total_tokens > self.available_chat_tokens,
            'is_critical': usage_percentage > 90,
            'message_count': len(messages)
        }
    
    def should_summarize(self, messages: List[Dict[str, Any]]) -> bool:
        """
        Prüft ob Zusammenfassung erforderlich ist
        
        Args:
            messages: Aktuelle Message-Liste
            
        Returns:
            True wenn Zusammenfassung erforderlich ist
        """
        analysis = self.analyze_context_usage(messages)
        return analysis['needs_summarization'] and len(messages) > self.min_recent_messages
    
    def adjust_rag_reserve(self, actual_rag_tokens: int) -> None:
        """
        Passt RAG-Reserve dynamisch an die tatsächliche RAG-Größe an.
        
        Ermöglicht dynamische Anpassung des Reserved Space basierend auf
        tatsächlicher RAG-Nutzung, um Kontext-Überlauf zu verhindern.
        
        Args:
            actual_rag_tokens: Tatsächliche Anzahl der RAG-Tokens
        """
        old_reserve = self.rag_reserve_tokens
        
        # Add 20% safety margin to actual RAG size
        recommended_reserve = int(actual_rag_tokens * 1.2)
        
        # Only adjust if significantly different (>30% change)
        if abs(recommended_reserve - old_reserve) / old_reserve > 0.3:
            self.rag_reserve_tokens = recommended_reserve
            
            # Recalculate available chat tokens
            safety_buffer = 256
            self.available_chat_tokens = (
                self.max_context_tokens 
                - self.system_prompt_tokens 
                - self.rag_reserve_tokens 
                - self.summary_target_tokens 
                - self.tools_reserve_tokens
                - safety_buffer
            )
            
            logger.info(f"🔄 RAG reserve adjusted: {old_reserve} → {self.rag_reserve_tokens} tokens")
            logger.info(f"   Reason: Actual RAG size = {actual_rag_tokens} tokens")
            logger.info(f"   ➜ Chat now has {self.available_chat_tokens} tokens")
    
    def get_budget_info(self) -> Dict[str, Any]:
        """
        Gibt detaillierte Budget-Informationen zurück.
        
        Returns:
            Budget-Breakdown für alle Komponenten
        """
        return {
            'max_context_tokens': self.max_context_tokens,
            'system_prompt_tokens': self.system_prompt_tokens,
            'rag_reserve_tokens': self.rag_reserve_tokens,
            'tools_reserve_tokens': self.tools_reserve_tokens,
            'summary_target_tokens': self.summary_target_tokens,
            'safety_buffer': 256,
            'available_chat_tokens': self.available_chat_tokens,
            'total_allocated': (
                self.system_prompt_tokens +
                self.rag_reserve_tokens +
                self.tools_reserve_tokens +
                self.summary_target_tokens +
                256  # safety_buffer
            ),
            'model_source': 'exact' if self.model_loader else 'fallback'
        }
    
    def extract_conversation_chunks(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Teilt Conversation in zu zusammenfassende und beizubehaltende Teile
        
        Args:
            messages: Vollständige Message-Liste
            
        Returns:
            (messages_to_summarize, messages_to_keep)
        """
        if len(messages) <= self.min_recent_messages:
            return [], messages
        
        # Behalte System-Message und letzte N Nachrichten
        system_messages = [msg for msg in messages if msg.get('role') == 'system']
        non_system_messages = [msg for msg in messages if msg.get('role') != 'system']
        
        if len(non_system_messages) <= self.min_recent_messages:
            return [], messages
        
        # Teile Non-System-Messages
        split_index = len(non_system_messages) - self.min_recent_messages
        messages_to_summarize = non_system_messages[:split_index]
        messages_to_keep = system_messages + non_system_messages[split_index:]
        
        return messages_to_summarize, messages_to_keep
    
    def create_conversation_summary(self, messages: List[Dict[str, Any]]) -> str:
        """
        Erstellt SOTA LLM-basierte rekursive Zusammenfassung einer Conversation.
        
        ✅ SOTA: Uses RecursiveLLMSummarizer (Map-Reduce + Salience) when available.
        Falls back to incremental running summary, then to heuristic.
        
        Args:
            messages: Nachrichten zur Zusammenfassung
            
        Returns:
            Zusammenfassung des Gesprächs
        """
        if not messages:
            return ""
        
        # ── STRATEGY 1: SOTA Recursive LLM Summarizer (preferred) ──
        if self._recursive_summarizer is not None:
            try:
                # Use incremental mode if we have a running summary
                if self._running_summary and self._running_summary_msg_count > 0:
                    # Only summarize messages not yet covered
                    new_messages = messages[self._running_summary_msg_count:]
                    if new_messages:
                        summary: str = str(self._recursive_summarizer.summarize_incremental(
                            self._running_summary, new_messages
                        ))
                        self._running_summary = summary
                        self._running_summary_msg_count = len(messages)
                        logger.info(
                            "✅ SOTA: Incremental summary updated (%d new msgs, %d total)",
                            len(new_messages), len(messages),
                        )
                        return summary
                    return self._running_summary
                
                # Full recursive summarization for first time
                summary = str(self._recursive_summarizer.summarize_full(messages))
                self._running_summary = summary
                self._running_summary_msg_count = len(messages)
                logger.info(
                    "✅ SOTA: Full recursive summary created (%d msgs → %d chars)",
                    len(messages), len(summary),
                )
                return summary
                
            except Exception as exc:
                logger.warning("SOTA summarizer failed, falling back: %s", exc)
        
        # ── STRATEGY 2: Heuristic fallback (no LLM) ──
        return self._create_heuristic_summary(messages)
    
    def _create_heuristic_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Rule-based fallback summary when LLM is unavailable."""
        
        # Extrahiere Key-Points
        user_messages = [msg['content'] for msg in messages if msg.get('role') == 'user']
        assistant_messages = [msg['content'] for msg in messages if msg.get('role') == 'assistant']
        
        # Detaillierte regelbasierte Zusammenfassung
        topics: List[str] = []
        
        # Analyse User-Messages für Themen
        all_user_text = ' '.join(str(msg) for msg in user_messages).lower()
        
        topic_keywords = {
            'technische fragen': ['code', 'programmierung', 'fehler', 'bug', 'installation', 'konfiguration', 'software', 'python', 'javascript'],
            'psychologische unterstützung': ['stress', 'angst', 'depression', 'gefühle', 'sorgen', 'probleme', 'hilfe', 'therapie', 'beratung'],
            'arbeitsthemen': ['arbeit', 'job', 'kollegen', 'chef', 'projekt', 'deadline', 'karriere', 'bewerbung'],
            'gesundheit': ['gesundheit', 'krankheit', 'arzt', 'medizin', 'symptome', 'schmerzen', 'therapie'],
            'beziehungen': ['beziehung', 'partner', 'familie', 'freunde', 'konflikte', 'liebe', 'trennung'],
            'bildung/lernen': ['lernen', 'studium', 'schule', 'prüfung', 'wissen', 'universität', 'ausbildung'],
            'freizeit': ['hobby', 'spiel', 'musik', 'film', 'reisen', 'sport', 'unterhaltung'],
            'finanzen': ['geld', 'kosten', 'budget', 'sparen', 'investition', 'finanzierung'],
            'kreativität': ['kunst', 'design', 'schreiben', 'malen', 'kreativ', 'projekt'],
            'technologie': ['computer', 'handy', 'app', 'internet', 'software', 'hardware']
        }
        
        detected_topics = []
        for topic, keywords in topic_keywords.items():
            if any(keyword in all_user_text for keyword in keywords):
                detected_topics.append(topic)
        
        # Sammle wichtige Statements und Entscheidungen
        key_statements = []
        important_responses = []
        
        # Finde längere, detaillierte User-Messages (potenziell wichtig)
        for msg in user_messages:
            msg_str = str(msg)
            if len(msg_str) > 80:  # Längere Nachrichten sind oft wichtiger
                key_statements.append(msg_str[:150] + "..." if len(msg_str) > 150 else msg_str)
        
        # Finde wichtige Assistant-Responses (mit Schlüsselwörtern)
        response_keywords = ['empfehlung', 'vorschlag', 'lösung', 'antwort', 'ergebnis', 'zusammenfassung']
        for msg in assistant_messages:
            msg_str = str(msg).lower()
            if any(keyword in msg_str for keyword in response_keywords) and len(msg_str) > 50:
                important_responses.append(str(msg)[:200] + "..." if len(str(msg)) > 200 else str(msg))
        
        # Zähle Nachrichten und analysiere Gesprächsintensität
        message_count = len(messages)
        user_count = len(user_messages)
        assistant_count = len(assistant_messages)
        
        # Berechne durchschnittliche Message-Länge
        avg_user_length = sum(len(str(msg)) for msg in user_messages) // max(user_count, 1)
        avg_assistant_length = sum(len(str(msg)) for msg in assistant_messages) // max(assistant_count, 1)
        
        # Zeitraum
        timestamp = datetime.now().strftime("%H:%M")
        
        # Baue detaillierte Zusammenfassung
        summary_parts = [
            f"📝 DETAILLIERTE GESPRÄCHSZUSAMMENFASSUNG ({timestamp})",
            f"═══════════════════════════════════════════════════════",
            f"📊 STATISTIKEN:",
            f"   • Nachrichten gesamt: {message_count}",
            f"   • Benutzer-Nachrichten: {user_count} (⌀ {avg_user_length} Zeichen)",
            f"   • Assistent-Antworten: {assistant_count} (⌀ {avg_assistant_length} Zeichen)",
            "",
            f"🎯 HAUPTTHEMEN:"
        ]
        
        if detected_topics:
            for i, topic in enumerate(detected_topics[:5], 1):  # Max 5 Themen
                summary_parts.append(f"   {i}. {topic.title()}")
        else:
            summary_parts.append("   • Allgemeine Konversation")
        
        summary_parts.append("")
        
        # Füge wichtige Benutzer-Statements hinzu
        if key_statements:
            summary_parts.append("💬 WICHTIGE BENUTZER-AUSSAGEN:")
            for i, statement in enumerate(key_statements[:3], 1):  # Max 3 Statements
                summary_parts.append(f"   {i}. \"{statement}\"")
            summary_parts.append("")
        
        # Füge wichtige Assistent-Antworten hinzu
        if important_responses:
            summary_parts.append("🤖 SCHLÜSSEL-ANTWORTEN:")
            for i, response in enumerate(important_responses[:2], 1):  # Max 2 Responses
                summary_parts.append(f"   {i}. {response}")
            summary_parts.append("")
        
        # Füge Gesprächskontext hinzu
        summary_parts.extend([
            "🔄 GESPRÄCHSKONTEXT:",
            f"   • Konversationsstil: {'Detailliert' if avg_user_length > 100 else 'Kurz und prägnant'}",
            f"   • Interaktionsgrad: {'Hoch' if message_count > 20 else 'Mittel' if message_count > 10 else 'Niedrig'}",
            f"   • Themenfokus: {'Spezifisch' if len(detected_topics) <= 2 else 'Breit gefächert'}"
        ])
        
        summary = "\n".join(summary_parts)
        
        # Optimiere auf Ziel-Token-Anzahl (1024 Token)
        # Entferne schrittweise Details, falls zu lang
        current_tokens = self.estimate_tokens(summary)
        
        if current_tokens > self.summary_target_tokens:
            # Reduziere Details schrittweise
            if len(important_responses) > 1:
                important_responses = important_responses[:1]
            if len(key_statements) > 2:
                key_statements = key_statements[:2]
            if len(detected_topics) > 3:
                detected_topics = detected_topics[:3]
            
            # Baue kompaktere Version
            summary_parts = [
                f"📝 GESPRÄCHSZUSAMMENFASSUNG ({timestamp})",
                f"📊 {message_count} Nachrichten (Benutzer: {user_count}, Assistent: {assistant_count})",
                f"🎯 Themen: {', '.join(detected_topics) if detected_topics else 'Allgemein'}",
                ""
            ]
            
            if key_statements:
                summary_parts.append("💬 Wichtige Aussagen:")
                for statement in key_statements[:2]:
                    summary_parts.append(f"   • \"{statement}\"")
                summary_parts.append("")
            
            if important_responses:
                summary_parts.append("🤖 Schlüssel-Antworten:")
                for response in important_responses[:1]:
                    summary_parts.append(f"   • {response}")
            
            summary = "\n".join(summary_parts)
        
        return summary
    
    def manage_context(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Verwaltet Chat-Kontext mit automatischer Zusammenfassung
        
        Args:
            messages: Aktuelle Message-Liste
            
        Returns:
            (optimized_messages, summary_info_dict)
        """
        analysis = self.analyze_context_usage(messages)
        
        logger.debug(f"🧠 Context-Analyse: {analysis['total_tokens']} Token "
                    f"({analysis['usage_percentage']:.1f}% von {self.max_context_tokens})")
        
        # Prüfe ob Zusammenfassung erforderlich
        if not self.should_summarize(messages):
            return messages, None
        
        logger.info(f"🔄 Context-Overflow erkannt - führe Chat-Zusammenfassung durch...")
        
        # Teile Messages
        messages_to_summarize, messages_to_keep = self.extract_conversation_chunks(messages)
        
        if not messages_to_summarize:
            logger.warning("⚠️ Keine Messages zum Zusammenfassen verfügbar")
            return messages, None
        
        # Erstelle Zusammenfassung
        summary = self.create_conversation_summary(messages_to_summarize)
        
        # Erstelle System-Message mit Zusammenfassung
        summary_message = {
            'role': 'system',
            'content': f"GESPRÄCHSVERLAUF-ZUSAMMENFASSUNG:\n{summary}\n\n[Folgende Nachrichten setzen das aktuelle Gespräch fort]"
        }
        
        # Finde und ersetze/erweitere System-Message
        optimized_messages = []
        system_message_found = False
        
        for msg in messages_to_keep:
            if msg.get('role') == 'system' and not system_message_found:
                # Erweitere erste System-Message um Zusammenfassung
                enhanced_content = f"{msg['content']}\n\n{summary_message['content']}"
                optimized_messages.append({
                    'role': 'system',
                    'content': enhanced_content
                })
                system_message_found = True
            else:
                optimized_messages.append(msg)
        
        # Falls keine System-Message vorhanden, füge Summary-Message hinzu
        if not system_message_found:
            optimized_messages.insert(0, summary_message)
        
        # Finale Analyse
        final_analysis = self.analyze_context_usage(optimized_messages)
        
        logger.info(f"✅ Chat-Zusammenfassung abgeschlossen:")
        logger.info(f"   Ursprünglich: {analysis['total_tokens']} Token ({len(messages)} Messages)")
        logger.info(f"   Optimiert: {final_analysis['total_tokens']} Token ({len(optimized_messages)} Messages)")
        logger.info(f"   Einsparung: {analysis['total_tokens'] - final_analysis['total_tokens']} Token")
        logger.info(f"   Zusammengefasst: {len(messages_to_summarize)} Messages")
        
        summary_info = {
            'original_tokens': analysis['total_tokens'],
            'optimized_tokens': final_analysis['total_tokens'],
            'saved_tokens': analysis['total_tokens'] - final_analysis['total_tokens'],
            'summarized_messages': len(messages_to_summarize),
            'remaining_messages': len(optimized_messages),
            'summary_length': len(summary)
        }
        
        return optimized_messages, summary_info


def test_context_manager():
    """Test-Funktion für den Context Manager"""
    print("🧪 Teste Chat Context Manager...")
    
    manager = ChatContextManager(max_context_tokens=2048)  # Kleine Test-Größe
    
    # Erstelle Test-Messages
    test_messages = [
        {'role': 'system', 'content': 'Du bist ein hilfreicher Assistent.'},
        {'role': 'user', 'content': 'Hallo, kannst du mir mit Python helfen?'},
        {'role': 'assistant', 'content': 'Natürlich! Ich helfe gerne bei Python-Fragen. Was möchtest du wissen?'},
        {'role': 'user', 'content': 'Ich habe Probleme mit einer For-Schleife. Sie läuft nicht richtig.'},
        {'role': 'assistant', 'content': 'Gerne helfe ich dir bei der For-Schleife. Kannst du mir deinen Code zeigen?'},
        {'role': 'user', 'content': 'for i in range(10): print(i)'},
        {'role': 'assistant', 'content': 'Dein Code sieht korrekt aus. Was genau ist das Problem?'},
        {'role': 'user', 'content': 'Es funktioniert jetzt! Vielen Dank für die Hilfe.'},
    ]
    
    # Füge viele Messages hinzu um Overflow zu erzwingen
    for i in range(20):
        test_messages.extend([
            {'role': 'user', 'content': f'Das ist Nachricht {i} mit etwas mehr Text um Token-Verbrauch zu simulieren.'},
            {'role': 'assistant', 'content': f'Das ist die Antwort auf Nachricht {i} mit detaillierter Erklärung und Beispielen.'}
        ])
    
    print(f"📊 Original: {len(test_messages)} Messages")
    
    # Teste Context-Management
    optimized_messages, summary_info = manager.manage_context(test_messages)
    
    print(f"📊 Optimiert: {len(optimized_messages)} Messages")
    if summary_info:
        print(f"💾 Token-Einsparung: {summary_info.get('saved_tokens', 0)} Token")
        print(f"📝 Zusammengefasst: {summary_info.get('summarized_messages', 0)} Messages")


if __name__ == "__main__":
    test_context_manager()
