import os
import logging
import json
import re
from typing import Optional, List, Dict

from scripts.model_loader import ModelLoader, LLM_CONTEXT_SIZE  # Migriert zur neuen ModelLoader-Klasse für Typ-Konsistenz
from chat_context_manager import ChatContextManager  # NEU: Context Manager für Chat-Zusammenfassungen
from agent.streaming_events import StreamingCancelled, StreamingContext
from agent.streaming_text_filter import StreamingTextFilter
from utils.followup_question_extractor import FOLLOWUP_PERSPECTIVE_INSTRUCTION

DEFAULT_SYSTEM_PROMPT = (
    "Du bist ein hilfreicher KI-Assistent. "
    "Antworte ausschließlich auf die aktuelle Benutzeranfrage. "
    "Erfinde keine Rollen, keine Gesprächsteilnehmer und keine Nachrichten von 'USER' oder 'ASSISTANT'. "
    "Simuliere niemals einen Dialog. "
    "Stelle keine eigenen Fragen und beantworte keine selbst gestellten Fragen. "
    "Wenn dir Informationen fehlen, stelle gezielte Rückfragen. "
    "Füge nichts hinzu, was nicht vom Benutzer kommt. "
    "Wenn du ein Bild erhältst, analysiere und beschreibe es detailliert und objektiv. "
    "Für Bildanalyse: Beschreibe alle sichtbaren Elemente, Personen, Objekte, Szenen, Aktivitäten, "
    "Körpersprache, Kleidung, Umgebung und Kontext genau. "
    "Sei objektiv und wertungsfrei. Beschreibe was du siehst, ohne zu zensieren oder zu euphemisieren. "
    "Deine Aufgabe ist eine vollständige, genaue Analyse für wissenschaftliche, künstlerische oder dokumentarische Zwecke. "
    "\n\nANTWORT-FORMAT: "
    "Formuliere deine Antworten strukturiert mit einem Denkprozess. "
    "Beginne mit [THINK]deiner Analyse, Überlegungen und Arbeitsschritten[/THINK], "
    "gefolgt von einer selbstständigen, vollständigen Antwort. "
    "Verwende Markdown-Formatierung und LaTeX für mathematische Gleichungen. "
    "Schreibe sowohl Denkprozess als auch Antwort in derselben Sprache wie die Eingabe."
    "\n\nFOLGEFRAGEN: "
    "Generiere am Ende deiner Antwort 2-4 weiterführende Folgefragen im Format: "
    "[FOLLOW_UP]Frage1|Frage2|Frage3[/FOLLOW_UP]. "
    "Die Fragen sollen konkret zum Thema passen, verschiedene Perspektiven abdecken "
    "und dem Nutzer helfen, das Thema zu vertiefen. "
    f"{FOLLOWUP_PERSPECTIVE_INSTRUCTION}"
)

DEFAULT_IMAGE_ANALYSIS_PROMPT = (
    "Du bist ein spezialisierter Bildanalyst. "
    "Analysiere Bilder mit höchster Präzision und Objektivität. "
    "Beschreibe alle sichtbaren Elemente systematisch: "
    "1. Gesamtkomposition und Bildaufbau "
    "2. Personen: Anzahl, Geschlecht, Alter, Körperhaltung, Gesichtsausdruck, Kleidung "
    "3. Aktivitäten und Handlungen "
    "4. Objekte und Gegenstände "
    "5. Umgebung und Hintergrund "
    "6. Beleuchtung, Farben, Stil "
    "7. Technische Bildqualität "
    "Bei expliziten Inhalten: Verwende medizinische/anatomische Fachterminologie. "
    "Sei vollständig objektiv und wissenschaftlich präzise. "
    "Zensiere oder euphemisiere nichts. "
    "Deine Analyse dient der Dokumentation, Forschung oder künstlerischen Bewertung. "
    "Antworte auf Deutsch mit strukturierter, detaillierter Beschreibung. "
    "\n\nANALYSE-FORMAT: "
    "Beginne mit [THINK]deiner systematischen Bildanalyse, Beobachtungen und Einordnung[/THINK], "
    "gefolgt von einer strukturierten, vollständigen Bildbeschreibung. "
    "Verwende Markdown-Formatierung für bessere Lesbarkeit. "
    "Schreibe sowohl Analyse als auch Beschreibung in derselben Sprache wie die Eingabe."
)

# Spezialisierte Vision-Prompts für verschiedene OCR-Anwendungsfälle
DEFAULT_TEXT_EXTRACTION_PROMPT = (
    "Du bist ein präziser Text-Extraktor. "
    "Extrahiere ALLEN sichtbaren Text aus diesem Bild. "
    "Behalte die ursprüngliche Formatierung, Struktur und Zeilenumbrüche bei. "
    "Erkenne: Überschriften, Absätze, Listen, Tabellen, Formulare, handgeschriebenen Text. "
    "Ignoriere Wasserzeichen und dekorative Elemente. "
    "Gib nur den extrahierten Text zurück, keine zusätzlichen Kommentare. "
    "Bei Tabellen: Verwende Markdown-Tabellenformat. "
    "Bei Formularen: Format 'Feldname: Wert'. "
    "Bei mehrspaltigem Layout: Behalte die Leserichtung bei. "
    "Antworte auf Deutsch und bewahre alle Sonderzeichen und Formatierungen."
)

DEFAULT_DOCUMENT_ANALYSIS_PROMPT = (
    "Du bist ein Dokumenten-Spezialist. "
    "Analysiere dieses Dokument systematisch: "
    "1. **Dokumenttyp:** (Brief, Rechnung, Formular, etc.) "
    "2. **Struktur:** (Überschriften, Abschnitte, Layout) "
    "3. **Vollständiger Text:** (alle sichtbaren Inhalte) "
    "4. **Wichtige Daten:** (Datum, Namen, Zahlen, Referenzen) "
    "5. **Tabellarische Daten:** (falls vorhanden, als Markdown-Tabelle) "
    "6. **Formularfelder:** (Feldname: Wert Paare) "
    "Extrahiere alle Informationen vollständig und strukturiert. "
    "Verwende Markdown-Formatierung für bessere Lesbarkeit. "
    "Antworte auf Deutsch."
)

DEFAULT_TABLE_EXTRACTION_PROMPT = (
    "Du bist ein Tabellen-Spezialist. "
    "Erkenne und extrahiere diese Tabelle: "
    "1. Identifiziere alle Spaltenüberschriften "
    "2. Erkenne alle Zeilen und Datenwerte "
    "3. Behalte die Datentypen bei (Zahlen, Text, Datum) "
    "4. Formatiere als Markdown-Tabelle "
    "5. Falls mehrere Tabellen: Nummeriere sie "
    "6. Erkenne verbundene Zellen und komplexe Strukturen "
    "Gib eine saubere, vollständige Tabelle zurück. "
    "Verwende | für Spalten und korrekte Markdown-Tabellen-Syntax. "
    "Antworte auf Deutsch."
)

class ChatbotLogic:
    def __init__(self, model_loader: ModelLoader, settings: Optional[Dict] = None):
        self.model_loader = model_loader
        self.settings = settings or {
            "temperature": 0.7,
            "max_tokens": 2048,
            "n_ctx": LLM_CONTEXT_SIZE,  # Single source of truth from model_loader
            "system_prompt": DEFAULT_SYSTEM_PROMPT
        }

        self.system_prompt: str = (
            self.settings.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        )
        self.message_history: List[Dict] = []
        self.debug_mode = False
        self.debug_to_console = False

        # NEU: Initialisiere den Context Manager für automatische Chat-Zusammenfassung
        context_window = self.settings.get("n_ctx", LLM_CONTEXT_SIZE)
        # Sicherheitspuffer: 85% des Context Windows für Chat, Rest für System-Prompt etc.
        # Erhöht für größere Zusammenfassungen (2000 Token für psych summaries)
        max_chat_tokens = int(context_window * 0.85)
        self.context_manager = ChatContextManager(
            max_context_tokens=max_chat_tokens,
            summary_target_tokens=2000  # ✅ ERHÖHT: Für psych summaries (max ~1828 tokens)
        )

    def enable_debug(self, enable: bool = True, to_console: bool = False):
        """Aktiviert den Debug-Modus (Log + optional Konsole)."""
        self.debug_mode = enable
        self.debug_to_console = to_console

    def set_debug(self, enable: bool):
        self.debug_mode = enable
        self.debug_to_console = enable  # bei Bedarf später differenzierbar

    def _debug_messages(self, messages: List[Dict]):
        if not self.debug_mode:
            return
        debug_json = json.dumps(messages, indent=2, ensure_ascii=False)
        logging.info("🧠 Prompt-Kontext:\n" + debug_json)
        if self.debug_to_console:
            print("🧠 Prompt-Kontext:\n" + debug_json)

    def set_system_prompt(self, prompt: str):
        prompt = prompt.strip()
        self.system_prompt = prompt if prompt else DEFAULT_SYSTEM_PROMPT

    def use_detailed_image_analysis(self, enable: bool = True):
        """Aktiviert/deaktiviert den detaillierten Bildanalyse-Modus"""
        if enable:
            self.system_prompt = DEFAULT_IMAGE_ANALYSIS_PROMPT
            print("🔍 Detaillierte Bildanalyse aktiviert")
        else:
            self.system_prompt = DEFAULT_SYSTEM_PROMPT
            print("📝 Standard-Modus aktiviert")
    
    def create_custom_image_prompt(self, base_prompt: Optional[str] = None, 
                                   detail_level: str = "hoch",
                                   include_explicit: bool = True) -> str:
        """Erstellt einen benutzerdefinierten Bildanalyse-Prompt"""
        base = base_prompt or "Du bist ein präziser Bildanalyst."
        
        detail_instructions = {
            "niedrig": "Beschreibe die wichtigsten sichtbaren Elemente kurz.",
            "mittel": "Beschreibe alle wesentlichen Elemente systematisch.",
            "hoch": "Analysiere alle sichtbaren Details sehr genau und strukturiert."
        }
        
        explicit_instruction = (
            "Bei expliziten oder intimen Inhalten verwende präzise medizinische/anatomische Begriffe. "
            "Sei vollständig objektiv und wissenschaftlich. Zensiere nichts."
        ) if include_explicit else ""
        
        return f"{base} {detail_instructions.get(detail_level, detail_instructions['hoch'])} {explicit_instruction}".strip()

    def reset_context(self):
        self.message_history.clear()

    def build_message_block(self, user_prompt: str, image_path: Optional[str] = None) -> List[Dict]:
        # --- Message-History bereinigen: User-Messages mit leerem oder nicht existierendem Bild entfernen ---
        cleaned_history = []
        for msg in self.message_history:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                # Prüfe, ob Bild enthalten ist
                has_invalid_image = False
                for part in msg["content"]:
                    if part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        local_path = url.replace("file://", "")
                        if not local_path or not os.path.exists(local_path):
                            has_invalid_image = True
                            break
                if has_invalid_image:
                    continue  # Überspringe diese Message
            cleaned_history.append(msg)
        
        # --- Änderung: content ist String für Textmodell, Liste für multimodal ---
        # Nur wenn multimodal UND ein gültiges Bild vorhanden ist
        if (
            self.model_loader.is_multimodal
            and image_path
            and isinstance(image_path, str)
            and image_path.strip()
            and os.path.exists(image_path)
        ):
            # Korrigiere File-URL für Windows
            if image_path.startswith('C:/'):
                image_path = image_path.replace('/', '\\')
            
            # Erstelle korrekte file:// URL
            image_uri = f"file:///{image_path.replace('\\', '/')}"
            content = [
                {"type": "image_url", "image_url": {"url": image_uri}},
                {"type": "text", "text": user_prompt}
            ]
        else:
            # Nur Text - auch bei multimodalen Modellen wenn kein Bild
            content = user_prompt

        new_user_message = {"role": "user", "content": content}

        # Erstelle initial Messages
        messages = []
        messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(cleaned_history)
        messages.append(new_user_message)

        # NEU: Automatische Chat-Kontext-Verwaltung mit Zusammenfassung
        if self.context_manager.should_summarize(messages):
            optimized_messages, summary_info = self.context_manager.manage_context(messages)
            if summary_info:
                logging.info(f"🔄 Chat-Zusammenfassung durchgeführt - "
                           f"Token-Einsparung: {summary_info.get('saved_tokens', 0)}")
            return optimized_messages
        
        return messages

    def chat(
        self,
        user_prompt: str,
        image_path: Optional[str] = None,
        progress_callback=None,
        stream_callback=None,
        stream_context: Optional[StreamingContext] = None,
    ) -> str:
        print(f"[DEBUG] ChatbotLogic.chat() aufgerufen mit image_path={image_path!r}")
        logging.info(f"[DEBUG] ChatbotLogic.chat: image_path={image_path!r}")
        
        # Bildpfad-Existenz prüfen, falls multimodal und Bild erwartet
        if self.model_loader.is_multimodal and image_path and (not os.path.exists(image_path)):
            return "Fehler: Das ausgewählte Bild existiert nicht mehr oder ist nicht erreichbar."

        original_system_prompt = self.system_prompt
        try:
            # NEU: Progress-Callback nutzen falls verfügbar
            if progress_callback:
                progress_callback("🧠 Analysiere Anfrage...", f"Prompt-Länge: {len(user_prompt)} Zeichen")
            
            # NEU: Intelligente Prompt-Auswahl für Bilder (ersetzt OCR-Engine)
            if (self.model_loader.is_multimodal and image_path and 
                os.path.exists(image_path) and user_prompt):
                # Wähle optimalen Prompt basierend auf User-Intent
                optimal_prompt = self._select_optimal_image_prompt(user_prompt, image_path)
                self.system_prompt = optimal_prompt
                logging.info(f"🎯 Intelligente Prompt-Auswahl: Vision-Spezialist aktiviert")
                if progress_callback:
                    progress_callback("🎯 Vision-Spezialist", "Intelligente Prompt-Auswahl für Bildanalyse")
            
            # NEU: Context Management nur wenn nicht bereits durch AgentChatbotLogic gemacht
            # (verhindert doppelte Context-Verarbeitung)
            context_already_managed = getattr(self, '_context_managed_by_agent', False)
            
            if (hasattr(self, 'context_manager') and self.context_manager and 
                not context_already_managed):
                if progress_callback:
                    progress_callback("🧠 Context-Management", "Prüfe Chat-Länge...")
                
                # Baue vorläufige Messages für Context-Analyse
                temp_messages = self._build_temp_messages(user_prompt, image_path)
                
                # Prüfe ob Context-Zusammenfassung nötig ist
                if self.context_manager.should_summarize(temp_messages):
                    if progress_callback:
                        progress_callback("📝 Zusammenfassung", "Erstelle Chat-Zusammenfassung...")
                    
                    print("🧠 ChatbotLogic: Context-Zusammenfassung wird durchgeführt...")
                    optimized_messages, summary_info = self.context_manager.manage_context(temp_messages)
                    
                    if summary_info and summary_info.get('saved_tokens', 0) > 0:
                        print(f"✅ ChatbotLogic Token-Einsparung: {summary_info.get('saved_tokens', 0)}")
                        logging.info(f"🔄 Chat-Zusammenfassung durchgeführt - Token-Einsparung: {summary_info.get('saved_tokens', 0)}")
                        
                        # Aktualisiere Message History mit optimierten Messages
                        self.message_history = []
                        for msg in optimized_messages:
                            if msg['role'] != 'system':
                                self.message_history.append(msg)
                        
                        # Entferne die aktuelle User-Message (wird gleich hinzugefügt)
                        if self.message_history and self.message_history[-1]['role'] == 'user':
                            self.message_history.pop()
            
            messages = self.build_message_block(user_prompt, image_path)
            self._debug_messages(messages)  # Debugausgabe vor Inferenz

            max_tokens = self.settings.get("max_tokens", 2048)
            temperature = self.settings.get("temperature", 0.7)

            if progress_callback:
                progress_callback("🚀 LLM-Inferenz", f"Generiere Antwort (max {max_tokens} Token)...")

            # ── Streaming-Logik: wenn stream_callback provided, Token-by-Token ──
            followup_source = ""
            if stream_callback is not None and image_path is None:
                response_parts = []
                text_filter = StreamingTextFilter()
                for chunk in self.model_loader.generate_response_stream(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    is_cancelled=(
                        (lambda: stream_context.is_cancelled)
                        if stream_context is not None
                        else None
                    ),
                ):
                    visible_chunk = text_filter.feed(chunk)
                    if visible_chunk:
                        response_parts.append(visible_chunk)
                        stream_callback(visible_chunk)
                    if text_filter.stopped:
                        break
                final_chunk = text_filter.finish()
                if final_chunk:
                    response_parts.append(final_chunk)
                    stream_callback(final_chunk)
                response = "".join(response_parts)
                if text_filter.followup_block:
                    followup_source = (
                        f"[FOLLOW_UP]{text_filter.followup_block}[/FOLLOW_UP]"
                    )
                if stream_context is not None and stream_context.is_cancelled:
                    raise StreamingCancelled(response)
            else:
                response = self.model_loader.generate_response(
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=messages,
                    image_path=image_path
                )
                followup_source = response if isinstance(response, str) else ""

            if followup_source:
                from utils.followup_question_extractor import extract_followup_questions

                clean_response, followup_questions = extract_followup_questions(
                    followup_source
                )
                if stream_callback is None or image_path is not None:
                    response = clean_response
                if followup_questions:
                    self.last_followup_questions = followup_questions

            if progress_callback:
                progress_callback("✅ Antwort generiert", f"Länge: {len(response) if response else 0} Zeichen")

            # --- NEU: Alles ab "USER:" abschneiden ---
            if isinstance(response, str) and "USER:" in response:
                response = response.split("USER:")[0].rstrip()

            self.message_history.append(messages[-1])
            self.message_history.append({"role": "assistant", "content": response})

            # System-Prompt zurücksetzen falls geändert
            self.system_prompt = original_system_prompt

            return response
        except Exception as e:
            self.system_prompt = original_system_prompt
            if isinstance(e, StreamingCancelled) or stream_context is not None:
                raise
            import traceback
            tb = traceback.format_exc()
            logging.error(f"Fehler bei der Inferenz:\n{tb}")
            return f"Fehler bei der Antwortgenerierung: {type(e).__name__}: {e}"

    def _build_temp_messages(self, user_prompt: str, image_path: Optional[str] = None) -> List[Dict]:
        """Baue temporäre Messages für Context-Analyse (ohne Modifikation der History)"""
        temp_messages = []
        
        # System-Prompt hinzufügen
        if self.system_prompt:
            temp_messages.append({"role": "system", "content": self.system_prompt})
        
        # Bisherige Message History hinzufügen
        for msg in self.message_history:
            temp_messages.append(msg)
        
        # Aktuelle User-Message hinzufügen
        temp_messages.append({"role": "user", "content": user_prompt})
        
        return temp_messages

    def _select_optimal_image_prompt(self, user_prompt: str, image_path: str) -> str:
        """
        Wählt den optimalen Prompt basierend auf User-Intent und Bildinhalt.
        Ersetzt separate OCR-Engine durch LLM-basierte Intent-Klassifikation.
        """
        mode = self._classify_image_intent_mode(user_prompt)
        if mode == "TEXT":
            return DEFAULT_TEXT_EXTRACTION_PROMPT
        if mode == "TABLE":
            return DEFAULT_TABLE_EXTRACTION_PROMPT
        if mode == "DOCUMENT":
            return DEFAULT_DOCUMENT_ANALYSIS_PROMPT
        return DEFAULT_IMAGE_ANALYSIS_PROMPT

    def _classify_image_intent_mode(self, user_prompt: str) -> str:
        """Klassifiziert Bild-Intent in feste Modi ohne Keyword-Heuristik."""
        if not user_prompt or not user_prompt.strip():
            return "ANALYSIS"

        prompt = (
            "Klassifiziere die Nutzeranfrage exakt in einen Modus:\n"
            "- TEXT: reine Textextraktion/OCR\n"
            "- TABLE: Tabellenextraktion\n"
            "- DOCUMENT: strukturierte Dokumentanalyse\n"
            "- ANALYSIS: allgemeine Bildanalyse\n\n"
            "Antworte NUR mit einem dieser Labels: TEXT, TABLE, DOCUMENT, ANALYSIS.\n\n"
            f"Anfrage: {user_prompt}"
        )

        try:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "select_image_mode",
                        "description": "Waehlt den besten Modus fuer die Bildverarbeitung.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "mode": {
                                    "type": "string",
                                    "enum": ["TEXT", "TABLE", "DOCUMENT", "ANALYSIS"],
                                }
                            },
                            "required": ["mode"],
                        },
                    },
                }
            ]

            fc_response = self.model_loader.generate_with_tools(
                messages=[{"role": "user", "content": prompt}],
                tools=tools,
                tool_choice="required",
                max_tokens=96,
                temperature=0.0,
            )

            calls = fc_response.get("tool_calls") or []
            if calls:
                fn = (calls[0] or {}).get("function") or {}
                args_raw = fn.get("arguments")
                args = {}
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except (json.JSONDecodeError, ValueError):
                        args = {}
                elif isinstance(args_raw, dict):
                    args = args_raw
                mode = str(args.get("mode") or "").upper().strip()
                if mode in ("TEXT", "TABLE", "DOCUMENT", "ANALYSIS"):
                    return mode

            # Fallback nur bei Tool-/Parser-Ausfall.
            strict_prompt = (
                "Klassifiziere semantisch in einen Modus:\n"
                "TEXT=OCR/Texterfassung, TABLE=Tabellenextraktion, "
                "DOCUMENT=strukturierte Dokumentanalyse, ANALYSIS=allgemeine Bildanalyse.\n"
                "Bei Unsicherheit ANALYSIS.\n\n"
                f"Anfrage: {user_prompt}\n"
                "Antworte NUR mit: FINAL_MODE=<TEXT|TABLE|DOCUMENT|ANALYSIS>"
            )
            retry = self.model_loader.generate_response(
                max_tokens=40,
                temperature=0.0,
                messages=[{"role": "user", "content": strict_prompt}],
            )
            label = self._extract_mode_label(retry)
            return label or "ANALYSIS"
        except Exception as e:
            logging.warning(f"Bild-Intent-Klassifikation fehlgeschlagen: {type(e).__name__}: {e}")
            return "ANALYSIS"

    @staticmethod
    def _extract_mode_label(raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        upper = raw.upper()
        marked = re.search(r"FINAL_MODE\s*[:=]\s*(TEXT|TABLE|DOCUMENT|ANALYSIS)", upper)
        if marked:
            return marked.group(1)
        matches = re.findall(r"\b(TEXT|TABLE|DOCUMENT|ANALYSIS)\b", upper)
        if matches:
            unique = set(matches)
            if len(unique) == 1:
                return matches[-1]
            return None
        compact = re.sub(r"[^A-Z]", "", upper)
        if compact.startswith("TEXT"):
            return "TEXT"
        if compact.startswith("TABL"):
            return "TABLE"
        if compact.startswith("DOCU"):
            return "DOCUMENT"
        if compact.startswith("ANAL"):
            return "ANALYSIS"
        return None
