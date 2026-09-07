"""Renderer for the main Streamlit chat tab."""

from __future__ import annotations

import json
import base64
import binascii
import logging
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
from pydantic import ValidationError as PydanticValidationError

from agent.streaming_events import (
    ChatEventConsumer,
    ChatRunResult,
)
from database.chat_history_db import get_default_chat_db
from i18n import t as i18n_t
from schemas import ChatMessage, FileUpload, MermaidDiagramRequest, UserInput

try:
    from utils.mermaid_diagram import MermaidGenerator
    MERMAID_GENERATOR_AVAILABLE = True
except ImportError:
    MermaidGenerator = None
    MERMAID_GENERATOR_AVAILABLE = False


_MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def render_chat_tab(
    *,
    logger: logging.Logger,
    get_ai_response_events,
    extract_followup_questions,
    feedback_logger_available: bool,
    feedback_logger: Any,
    smart_hints_available: bool,
    generate_smart_hint,
    pandas_available: bool,
    pd: Any,
    pdf_processor_available: bool,
) -> None:
    st.header("💬 AI Chat Interface")
    
    # Simple status display (buttons are now in sidebar)
    if not st.session_state.initialized:
        st.info("🚀 Bitte laden Sie das AI-System über die Sidebar, um zu chatten.")
    else:
        st.success("✅ AI-System ist bereit! Sie können jetzt chatten.")
    
    st.divider()

    # Persistent session bootstrap
    if 'chat_session_id' not in st.session_state:
        st.session_state.chat_session_id = f"chat_{uuid.uuid4().hex[:16]}"
    if 'chat_history_loaded_from_db' not in st.session_state:
        st.session_state.chat_history_loaded_from_db = False
    if 'chat_db' not in st.session_state:
        try:
            st.session_state.chat_db = get_default_chat_db()
        except (RuntimeError, sqlite3.Error, OSError) as db_init_exc:
            logger.error(f"Chat DB konnte nicht initialisiert werden: {db_init_exc}")
            st.session_state.chat_db = None

    def _extract_mermaid_segments(content: str) -> list[tuple[str, str]]:
        """Split content into renderable text/mermaid segments."""
        if not content:
            return [("text", "")]

        segments: list[tuple[str, str]] = []
        last_end = 0
        for match in _MERMAID_BLOCK_RE.finditer(content):
            if match.start() > last_end:
                text_part = content[last_end:match.start()].strip()
                if text_part:
                    segments.append(("text", text_part))

            code_part = (match.group(1) or "").strip()
            if code_part:
                segments.append(("mermaid", code_part))
            last_end = match.end()

        if segments:
            tail = content[last_end:].strip()
            if tail:
                segments.append(("text", tail))
            return segments

        if MERMAID_GENERATOR_AVAILABLE and MermaidGenerator is not None:
            header = MermaidGenerator._detect_mermaid_header(content)  # type: ignore[attr-defined]
            if header:
                code = MermaidGenerator._extract_primary_mermaid_block(content)  # type: ignore[attr-defined]
                return [("mermaid", code)]

        return [("text", content)]

    def _render_message_with_mermaid(content: str, message_key: str) -> None:
        """Render markdown text and Mermaid diagrams in assistant messages."""
        segments = _extract_mermaid_segments(content)
        diagram_idx = 0

        for seg_type, seg_content in segments:
            if seg_type == "text":
                st.write(seg_content)
                continue

            if not (MERMAID_GENERATOR_AVAILABLE and MermaidGenerator is not None):
                st.code(seg_content, language="mermaid")
                continue

            diagram_idx += 1
            diagram_id = f"chat-mermaid-{message_key}-{diagram_idx}"
            preview_html = MermaidGenerator.get_render_html(seg_content, diagram_id=diagram_id)
            components.html(preview_html, height=460, scrolling=True)
            with st.expander("Mermaid-Code", expanded=False):
                st.code(seg_content, language="mermaid")

    def _render_graphics(graphics: list[dict[str, Any]]) -> None:
        """Render validated local-file and base64 graphical artifacts."""
        for graphic in graphics:
            image_source: str | bytes | None = None
            path = graphic.get("path")
            if isinstance(path, str) and os.path.isfile(path):
                image_source = path
            else:
                encoded = graphic.get("data_base64")
                if isinstance(encoded, str) and encoded:
                    try:
                        image_source = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError):
                        logger.warning("Ungültiges Base64-Grafikartefakt verworfen")

            if image_source is None:
                st.warning("Das erzeugte Diagramm ist nicht mehr verfügbar.")
                continue

            st.image(image_source, caption=graphic.get("caption") or "Generiertes Diagramm", width=800)
            details = [
                value
                for value in (
                    f"Typ: {str(graphic['diagram_type']).upper()}" if graphic.get("diagram_type") else None,
                    f"Renderer: {str(graphic['backend']).upper()}" if graphic.get("backend") else None,
                )
                if value
            ]
            if details:
                st.caption(" • ".join(details))

    def _render_files(files: list[dict[str, Any]], message_key: str) -> None:
        """Render downloads only for files produced inside the code sandbox."""
        sandbox_root = (Path(__file__).resolve().parents[1] / "code_sandbox").resolve()
        max_download_bytes = 20 * 1024 * 1024
        for index, file_info in enumerate(files):
            raw_path = file_info.get("path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            try:
                file_path = Path(raw_path).resolve(strict=True)
                file_path.relative_to(sandbox_root)
            except (OSError, ValueError):
                logger.warning("Dateiartefakt außerhalb der Code-Sandbox verworfen: %s", raw_path)
                st.warning("Die erzeugte Datei ist nicht mehr sicher verfügbar.")
                continue

            size = file_path.stat().st_size
            if size > max_download_bytes:
                st.warning(f"{file_path.name} ist zu groß für den Chat-Download.")
                continue

            data = file_path.read_bytes()
            display_name = str(file_info.get("name") or file_path.name)
            st.caption(f"{file_info.get('caption') or 'Erzeugte Datei'}: {display_name} ({size} Bytes)")
            st.download_button(
                label=f"⬇️ {display_name}",
                data=data,
                file_name=display_name,
                mime=str(file_info.get("media_type") or "application/octet-stream"),
                key=f"generated-file-{message_key}-{index}",
            )
            if display_name.lower().endswith(".py") and size <= 100_000:
                with st.expander("Python-Quellcode", expanded=False):
                    st.code(data.decode("utf-8", errors="replace"), language="python")

    def _append_message_to_db(message_dict: dict[str, Any]) -> None:
        """Persist a single chat message via schema-validated ChatMessage."""
        chat_db = st.session_state.get('chat_db')
        if chat_db is None:
            return

        sender_map = {'user': 'user', 'assistant': 'assistant', 'system': 'system'}
        sender = sender_map.get(message_dict.get('role', 'user'), 'user')

        model = ChatMessage(
            content=message_dict.get('content', ''),
            sender=sender,
            timestamp=message_dict.get('timestamp', datetime.utcnow()),
            conversation_id=st.session_state.chat_session_id,
            metadata={
                'generated_image': message_dict.get('generated_image'),
                'graphics': message_dict.get('graphics', []),
                'files': message_dict.get('files', []),
                'internet_image': message_dict.get('internet_image'),
                'generated_diagram_backend': message_dict.get('generated_diagram_backend'),
                'generated_diagram_type': message_dict.get('generated_diagram_type'),
                'chunk_ids': message_dict.get('chunk_ids', []),
                'chunk_scores': message_dict.get('chunk_scores', []),
            },
            reasoning=message_dict.get('reasoning_trace'),
        )
        chat_db.append_message_to_session(st.session_state.chat_session_id, model)

    def _stream_ai_response(
        message: str,
        image_path: str | None = None,
        status_callback=None,
    ) -> ChatRunResult | None:
        """Render typed events and return only a successfully completed result."""
        consumer = ChatEventConsumer()
        session_id = st.session_state.chat_session_id
        status_placeholder = st.empty()

        route_labels = {
            "simple": i18n_t("gui.chat.route_simple"),
            "plan_execute": i18n_t("gui.chat.route_plan_execute"),
            "react": i18n_t("gui.chat.route_react"),
            "vision": i18n_t("gui.chat.route_vision"),
            "cache": i18n_t("gui.chat.route_cache"),
        }

        def request_stop() -> None:
            chat_logic = st.session_state.get("chat_logic")
            if chat_logic is not None:
                chat_logic.cancel_stream(session_id)

        def text_chunks():
            for event in get_ai_response_events(
                message,
                session_id=session_id,
                image_path=image_path,
            ):
                visible_delta = consumer.observe(event)
                if event.type == "route_selected":
                    label = route_labels[event.selected_route]
                    status_placeholder.caption(label)
                    if status_callback is not None:
                        status_callback(label)
                elif event.type == "step_started":
                    status_placeholder.caption(event.label)
                    if status_callback is not None:
                        status_callback(event.label)
                if visible_delta is not None:
                    yield visible_delta

        with st.chat_message("assistant"):
            st.button(
                i18n_t("gui.chat.stop"),
                key=f"stop-chat-{session_id}",
                on_click=request_stop,
                type="secondary",
            )
            rendered_text = st.write_stream(text_chunks())

        status_placeholder.empty()
        if consumer.failure_code is not None:
            logger.error(
                "Chat-Run fehlgeschlagen [%s]: %s",
                consumer.failure_code,
                consumer.failure_message,
            )
            st.error(i18n_t("gui.chat.stream_failed"))
            return None
        if consumer.was_cancelled:
            st.info(i18n_t("gui.chat.stream_cancelled"))
            return None
        if consumer.result is None:
            logger.error(
                "Chat-Eventstream endete ohne Terminalevent; beobachtet=%s",
                consumer.observed_types,
            )
            st.error(i18n_t("gui.chat.stream_incomplete"))
            return None
        if isinstance(rendered_text, str) and rendered_text != consumer.result.text:
            raise RuntimeError("Sichtbarer und kanonischer Antworttext weichen ab")
        return consumer.result

    def _load_history_from_db_once() -> None:
        if st.session_state.chat_history_loaded_from_db:
            return
        chat_db = st.session_state.get('chat_db')
        if chat_db is None:
            st.session_state.chat_history_loaded_from_db = True
            return

        session_id = st.session_state.chat_session_id
        loaded = chat_db.load_chat_history(session_id)
        if loaded and loaded.messages:
            rebuilt: list[dict[str, Any]] = []
            for msg in loaded.messages:
                rebuilt.append({
                    'role': 'assistant' if msg.sender == 'assistant' else 'user',
                    'content': msg.content,
                    'timestamp': msg.timestamp,
                    'image': None,
                    'generated_image': (msg.metadata or {}).get('generated_image'),
                    'graphics': (msg.metadata or {}).get('graphics', []),
                    'files': (msg.metadata or {}).get('files', []),
                    'internet_image': (msg.metadata or {}).get('internet_image'),
                    'generated_diagram_backend': (msg.metadata or {}).get('generated_diagram_backend'),
                    'generated_diagram_type': (msg.metadata or {}).get('generated_diagram_type'),
                    'debug_info_friendly': None,
                    'debug_info_technical': None,
                    'orchestrator_type': None,
                    'orchestrator_id': None,
                    'llm_calls': None,
                    'reasoning_complexity': None,
                    'reasoning_token_budget': None,
                    'reasoning_enabled': None,
                    'reasoning_trace': msg.reasoning,
                    'chunk_ids': (msg.metadata or {}).get('chunk_ids', []),
                    'chunk_scores': (msg.metadata or {}).get('chunk_scores', []),
                    'followup_questions': [],
                })
            st.session_state.chat_history = rebuilt

        st.session_state.chat_history_loaded_from_db = True
    
    # Chat interface
    if st.session_state.initialized:
        try:
            _load_history_from_db_once()
        except (RuntimeError, ValueError, sqlite3.Error) as load_exc:
            logger.error(f"Persistente Chat-Historie konnte nicht geladen werden: {load_exc}")
            st.warning("Persistente Chat-Historie konnte nicht geladen werden. Es wird mit leerer Session fortgefahren.")

        # Display chat history FIRST (so it's at the top)
        st.subheader("💬 Chat-Verlauf")
        
        # Chat container for scrolling
        chat_container = st.container()
        
        with chat_container:
            for i, message in enumerate(st.session_state.chat_history):
                if message["role"] == "user":
                    with st.chat_message("user"):
                        st.write(message["content"])
                        if message.get("image"):
                            # FIX: Unterstütze sowohl Bytes als auch alte Dateipfade (backwards compatible)
                            image_data = message["image"]
                            if isinstance(image_data, str) and os.path.exists(image_data):
                                # Alter Dateipfad - direkt anzeigen
                                st.image(image_data, caption="Hochgeladenes Bild", width=300)
                            elif isinstance(image_data, (bytes, bytearray)):
                                # Neue Bytes-Speicherung - direkt anzeigen
                                st.image(image_data, caption="Hochgeladenes Bild", width=300)
                            else:
                                # Fallback: Versuche es trotzdem
                                st.image(image_data, caption="Hochgeladenes Bild", width=300)
                else:
                    with st.chat_message("assistant"):
                        # Follow-Up-Fragen extrahieren und Antworttext bereinigen
                        display_content = message["content"]
                        followup_questions = message.get("followup_questions", None)
                        
                        if followup_questions is None:
                            # Erstmalige Extraktion (wird dann im Message gecacht)
                            display_content, followup_questions = extract_followup_questions(message["content"])
                            message["display_content"] = display_content
                            message["followup_questions"] = followup_questions
                        else:
                            display_content = message.get("display_content", message["content"])

                        _render_message_with_mermaid(display_content, message_key=f"history-{i}")
                        _render_graphics(message.get("graphics", []))
                        _render_files(message.get("files", []), message_key=f"history-{i}")
                        
                        # Show generated image
                        if message.get("generated_image") and os.path.exists(message["generated_image"]):
                            st.image(message["generated_image"], caption="Generiertes Diagramm", width=800)
                            backend = message.get("generated_diagram_backend")
                            dtype = message.get("generated_diagram_type")
                            if backend or dtype:
                                label_parts = []
                                if dtype:
                                    label_parts.append(f"Typ: {str(dtype).upper()}")
                                if backend:
                                    label_parts.append(f"Renderer: {str(backend).upper()}")
                                st.caption(" • ".join(label_parts))
                        
                        # Show internet image
                        if message.get("internet_image"):
                            st.image(message["internet_image"], caption="Relevantes Internet-Bild", width=400)
                        
                        # NEU: Nutzerfreundliche Debug-Anzeige direkt unter der Antwort
                        debug_friendly = message.get("debug_info_friendly")
                        debug_technical = message.get("debug_info_technical")
                        
                        if debug_friendly:
                            # Hauptanzeige: Nutzerfreundlich
                            with st.expander("ℹ️ **Wie diese Antwort entstand**", expanded=False):
                                st.markdown(debug_friendly)
                                
                                # Optional: Technische Details
                                if debug_technical:
                                    with st.expander("� **Technische Details** (für Entwickler)", expanded=False):
                                        st.code(debug_technical, language="text")
                                        
                                        # Zeige zusätzliche Orchestrator-Infos wenn vorhanden
                                        if message.get("orchestrator_type"):
                                            st.markdown("**Orchestrator-Status:**")
                                            st.markdown(f"- Typ: `{message['orchestrator_type']}`")
                                            st.markdown(f"- Objekt-ID: `{message.get('orchestrator_id', 'N/A')}`")
                                        
                                        # Zeige LLM-Call-Statistik wenn vorhanden
                                        if message.get("llm_calls"):
                                            st.markdown("**LLM-Calls:**")
                                            st.info(f"🤖 {message['llm_calls']} LLM-Aufrufe für diese Antwort")
                        
                        elif debug_technical:
                            # Fallback: Nur technische Details (alte Nachrichten)
                            with st.expander("� **Routing-Entscheidung & Debug**", expanded=False):
                                st.code(debug_technical, language="text")
                                
                                # Zeige zusätzliche Orchestrator-Infos wenn vorhanden
                                if message.get("orchestrator_type"):
                                    st.markdown("**Orchestrator-Status:**")
                                    st.markdown(f"- Typ: `{message['orchestrator_type']}`")
                                    st.markdown(f"- Objekt-ID: `{message.get('orchestrator_id', 'N/A')}`")
                                
                                # Zeige LLM-Call-Statistik wenn vorhanden
                                if message.get("llm_calls"):
                                    st.markdown("**LLM-Calls:**")
                                    st.info(f"🤖 {message['llm_calls']} LLM-Aufrufe für diese Antwort")
                        
                        else:
                            # Keine Debug-Info verfügbar
                            with st.expander("💡 **Info zur Antwortgenerierung**", expanded=False):
                                st.info("ℹ️ Für diese Nachricht sind keine Details verfügbar.")
                                st.caption("**Mögliche Gründe:**")
                                st.markdown("- Nachricht wurde vor dem Debug-Feature erstellt")
                                st.markdown("- Direkte Bildanalyse ohne Routing")
                                st.markdown("- Fehler beim Sammeln der Debug-Info")
                        
                        # ================================================================
                        # NEU: KLICKBARE FOLGEFRAGEN (Follow-Up Buttons)
                        # ================================================================
                        if followup_questions:
                            st.markdown("---")
                            st.markdown("**💡 Weiterführende Fragen:**")
                            followup_cols = st.columns(min(len(followup_questions), 2))
                            for fq_idx, fq_text in enumerate(followup_questions):
                                col_idx = fq_idx % min(len(followup_questions), 2)
                                with followup_cols[col_idx]:
                                    # Kürze Button-Label für kompakte Darstellung
                                    btn_label = fq_text if len(fq_text) <= 80 else fq_text[:77] + "..."
                                    if st.button(
                                        f"➔ {btn_label}",
                                        key=f"followup_{i}_{fq_idx}",
                                        width='stretch',
                                        help=fq_text  # Volltext im Tooltip
                                    ):
                                        st.session_state.pending_followup = fq_text
                                        st.rerun()
                        
                        # Feedback buttons
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("👍 Hilfreich", key=f"pos_{i}"):
                                # Store in session state
                                st.session_state.feedback_data.append({"type": "positive", "message_id": i})
                                
                                # Log to persistent storage (JSONL + DB) - only if available
                                if feedback_logger_available and feedback_logger:
                                    query = st.session_state.chat_history[i-1]["content"] if i > 0 and i <= len(st.session_state.chat_history) else "N/A"
                                    response = message["content"]
                                    feedback_logger.log_feedback(
                                        query=query,
                                        response=response,
                                        feedback="positive",
                                        search_depth=st.session_state.get("search_depth", 5),
                                        confidence=st.session_state.get("faiss_confidence", 0.70),
                                        conversation_id=st.session_state.get("session_id", "default"),
                                        category="quality",
                                        chunk_ids=message.get("chunk_ids", []),
                                        chunk_scores=message.get("chunk_scores", []),
                                    )
                                
                                st.toast("👍 Positives Feedback gespeichert!")
                        with col2:
                            # Enhanced negative feedback with smart hints
                            if st.button("👎 Nicht hilfreich", key=f"neg_{i}"):
                                # Set flag to show feedback form
                                feedback_key = f"feedback_expanded_{i}"
                                if feedback_key not in st.session_state:
                                    st.session_state[feedback_key] = True
                            
                            # Show feedback form if button was clicked
                            if st.session_state.get(f"feedback_expanded_{i}", False):
                                with st.form(key=f"feedback_form_{i}"):
                                    st.markdown("**Was war nicht hilfreich?**")
                                    
                                    # Quick category selection
                                    quick_category = st.selectbox(
                                        "Schnellauswahl (optional):",
                                        ["---", "Zu lang", "Zu kurz", "Kontext fehlt", "Unklar", 
                                         "Fehler", "Keine Quelle", "Zu allgemein", "Zu technisch", "Anderes"],
                                        key=f"quick_cat_{i}"
                                    )
                                    
                                    # Free text comment
                                    feedback_comment = st.text_area(
                                        "Details (optional):",
                                        placeholder="z.B. zu technisch, falscher Kontext, Quelle fehlt...",
                                        key=f"fb_comment_{i}",
                                        height=80
                                    )
                                    
                                    # Submit button
                                    submitted = st.form_submit_button("📤 Feedback absenden")
                                    
                                    if submitted:
                                        # Combine category and comment
                                        selected_category = None if quick_category == "---" else quick_category
                                        full_comment = ""
                                        
                                        if selected_category and feedback_comment:
                                            full_comment = f"{selected_category}: {feedback_comment}"
                                        elif selected_category:
                                            full_comment = selected_category
                                        elif feedback_comment:
                                            full_comment = feedback_comment
                                        else:
                                            full_comment = "Nicht hilfreich (keine Details)"
                                        
                                        # Store in session state
                                        st.session_state.feedback_data.append({
                                            "type": "negative",
                                            "message_id": i,
                                            "category": selected_category,
                                            "comment": full_comment
                                        })
                                        
                                        # Log to persistent storage
                                        if feedback_logger_available and feedback_logger:
                                            query = st.session_state.chat_history[i-1]["content"] if i > 0 and i <= len(st.session_state.chat_history) else "N/A"
                                            response = message["content"]
                                            feedback_logger.log_feedback(
                                                query=query,
                                                response=response,
                                                feedback="negative",
                                                search_depth=st.session_state.get("search_depth", 5),
                                                confidence=st.session_state.get("faiss_confidence", 0.70),
                                                reason=full_comment,
                                                comment=full_comment,
                                                conversation_id=st.session_state.get("session_id", "default"),
                                                category=selected_category or "quality",
                                                chunk_ids=message.get("chunk_ids", []),
                                                chunk_scores=message.get("chunk_scores", []),
                                            )
                                        
                                        # Thank you message
                                        st.success("✅ Danke für dein Feedback!")
                                        
                                        # Generate smart hint (LLM-based)
                                        if smart_hints_available and generate_smart_hint:
                                            hint_context = {
                                                'has_pdf': st.session_state.get('pdf_data') is not None,
                                                'original_query': st.session_state.chat_history[i-1]["content"] if i > 0 and i <= len(st.session_state.chat_history) else None
                                            }
                                            
                                            with st.spinner("� Generiere Tipp..."):
                                                hint = generate_smart_hint(
                                                    feedback_text=feedback_comment,
                                                    quick_category=selected_category,
                                                    context=hint_context,
                                                    use_llm=True,
                                                    llm_model="mistral"
                                                )
                                            
                                            if hint:
                                                st.info(hint)
                                        
                                        # Reset form state
                                        st.session_state[f"feedback_expanded_{i}"] = False
                                        st.rerun()
                        
                        # ================================================================
                        # NEU: REASONING-TRACE-ANZEIGE
                        # ================================================================
                        if message.get("reasoning_complexity") or message.get("reasoning_trace"):
                            with st.expander("🧠 **Reasoning-Optimizer Details**", expanded=False):
                                # Complexity-Badge
                                if message.get("reasoning_complexity"):
                                    complexity = message["reasoning_complexity"]
                                    if complexity == "simple":
                                        st.success(f"🟢 **Komplexität:** SIMPLE")
                                    elif complexity == "medium":
                                        st.info(f"🟡 **Komplexität:** MEDIUM")
                                    elif complexity == "complex":
                                        st.warning(f"🔴 **Komplexität:** COMPLEX")
                                
                                # Token-Budget und Reasoning-Status
                                if message.get("reasoning_token_budget") or message.get("reasoning_enabled"):
                                    col1, col2 = st.columns(2)
                                    with col1:
                                        if message.get("reasoning_token_budget"):
                                            st.metric("Token-Budget", f"{message['reasoning_token_budget']:,}")
                                    with col2:
                                        if message.get("reasoning_enabled") is not None:
                                            st.metric("Reasoning", "✓ Aktiviert" if message["reasoning_enabled"] else "✗ Deaktiviert")
                                
                                # Reasoning-Trace (falls vorhanden)
                                if message.get("reasoning_trace"):
                                    st.markdown("**Reasoning-Trace:**")
                                    st.code(message["reasoning_trace"], language="text")
                                
                                st.caption("ℹ️ Diese Informationen zeigen, wie der Reasoning-Optimizer die Query klassifiziert hat.")
    
        
        # Input section ALWAYS AT BOTTOM
        st.divider()
        st.subheader("✏️ Neue Nachricht")
        
        # File upload for images, PDFs, Excel, PowerPoint, and other documents
        # Layout: file_uploader + Persistenz-Checkbox direkt nebeneinander, damit
        # die Checkbox nicht zwischen Multi-Query-Buttons und Mermaid-Studio
        # versteckt ist (wie es in früheren Layouts der Fall war).
        upload_col, persist_col = st.columns([3, 2])
        with upload_col:
            uploaded_file = st.file_uploader(
                "📎 Datei hochladen (optional)", 
                type=['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 
                      'pdf', 'xlsx', 'xls', 'xlsm', 'csv', 'txt', 'docx', 'doc', 'pptx'],
                help="Laden Sie ein Bild, PDF, Excel, PowerPoint oder andere Dokumente zur Analyse hoch"
            )
    
        # Persistenz-Schalter: legt fest, ob extrahierter Inhalt dauerhaft
        # in den RAG-Store + Knowledge-Graph aufgenommen wird oder nur
        # ephemer als Kontext für den aktuellen Chat-Turn verwendet wird.
        # Default: an — bewahrt das bisherige Verhalten.
        with persist_col:
            add_to_rag = st.checkbox(
                "📚 In Wissensbasis (RAG) aufnehmen",
                value=True,
                help=(
                    "An: Datei wird dauerhaft in RAG-Store + Knowledge-Graph "
                    "aufgenommen und steht in zukünftigen Sessions als "
                    "Wissensquelle zur Verfügung.\n"
                    "Aus: Inhalt fließt nur in den aktuellen Chat-Turn ein, "
                    "wird aber nicht persistiert (z.B. für vertrauliche "
                    "Dokumente, Einmal-Anfragen)."
                ),
                key="upload_add_to_rag",
            )
            # Sichtbarer Status, damit der User nicht rätselt was passiert
            if uploaded_file is not None:
                if add_to_rag:
                    st.success("✅ wird ins RAG übernommen")
                else:
                    st.warning("⚠️ wird NICHT ins RAG übernommen")
        
        # 🚀 NEW: Multi-Query RAG Depth Control (Preset + Custom)
        st.caption("🔍 **Such-Tiefe** (Multi-Query RAG):")
        
        # Initialize default value in session state
        if 'mq_n' not in st.session_state:
            st.session_state.mq_n = 5  # Default: Standard
        
        # Preset Buttons + Custom Popover
        col1, col2, col3, col4 = st.columns([1, 1, 1, 1.2])
        
        with col1:
            if st.button("⚡ **Schnell**", width="stretch", 
                        help="3 Subqueries - Schnelle Suche, weniger umfassend"):
                st.session_state.mq_n = 3
                # Update orchestrator immediately
                if st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'orchestrator') and st.session_state.chat_logic.orchestrator is not None:
                    st.session_state.chat_logic.orchestrator.set_multiquery_config(n=3)
                st.toast("⚡ Schnelle Suche aktiviert (3 Subqueries)")
        
        with col2:
            if st.button("⚖️ **Standard**", width="stretch",
                        help="5 Subqueries - Ausgewogene Balance zwischen Geschwindigkeit und Tiefe"):
                st.session_state.mq_n = 5
                if st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'orchestrator') and st.session_state.chat_logic.orchestrator is not None:
                    st.session_state.chat_logic.orchestrator.set_multiquery_config(n=5)
                st.toast("⚖️ Standard-Suche aktiviert (5 Subqueries)")
        
        with col3:
            if st.button("🔬 **Tief**", width="stretch",
                        help="10 Subqueries - Tiefensuche, sehr umfassend, aber langsamer"):
                st.session_state.mq_n = 10
                if st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'orchestrator') and st.session_state.chat_logic.orchestrator is not None:
                    st.session_state.chat_logic.orchestrator.set_multiquery_config(n=10)
                st.toast("🔬 Tiefensuche aktiviert (10 Subqueries)")
        
        with col4:
            with st.popover("⚙️ **Custom**"):
                st.markdown("### 🎛️ Benutzerdefinierte Anzahl")
                st.caption("Power-User-Modus: Wählen Sie eine beliebige Anzahl an Subqueries.")
                
                custom_mq = st.slider(
                    "Anzahl Subqueries", 
                    min_value=1, 
                    max_value=20, 
                    value=st.session_state.mq_n,
                    step=1,
                    help="1 = Minimale Suche, 20 = Maximum (sehr langsam!)"
                )
                
                st.info(f"""
                **Aktuell ausgewählt**: {custom_mq} Subqueries
                
                **Geschätzte Suchzeit**:
                - 1-3: ~2-5 Sekunden ⚡
                - 4-7: ~5-10 Sekunden ⚖️
                - 8-12: ~10-20 Sekunden 🔬
                - 13-20: ~20-40 Sekunden 🐌
                """)
                
                if st.button("✅ Anwenden", type="primary", width="stretch"):
                    st.session_state.mq_n = custom_mq
                    if st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'orchestrator') and st.session_state.chat_logic.orchestrator is not None:
                        st.session_state.chat_logic.orchestrator.set_multiquery_config(n=custom_mq)
                    st.toast(f"✅ Custom-Suche aktiviert ({custom_mq} Subqueries)")
                    st.rerun()
        
        # Status indicator
        current_mq = st.session_state.get('mq_n', 5)
        if current_mq <= 3:
            mode_emoji = "⚡"
            mode_name = "Schnell"
        elif current_mq <= 7:
            mode_emoji = "⚖️"
            mode_name = "Standard"
        elif current_mq <= 12:
            mode_emoji = "🔬"
            mode_name = "Tief"
        else:
            mode_emoji = "🌐"
            mode_name = "Ultra"
        
        st.caption(f"{mode_emoji} **Aktuell**: {current_mq} Subqueries ({mode_name})")
        
        # ===================================================================
        # NEW: FAISS Confidence Threshold Control
        # ===================================================================
        st.divider()
        st.markdown("### 🎯 RAG Qualitäts-Schwelle")
        st.caption("Steuert die minimale Ähnlichkeit für FAISS-Suchergebnisse")
        
        # Initialize default value in session state
        if 'faiss_confidence' not in st.session_state:
            st.session_state.faiss_confidence = 0.70  # Default: Moderate
        
        # Preset Buttons + Custom Popover
        col1, col2, col3, col4 = st.columns([1, 1, 1, 1.2])
        
        with col1:
            if st.button("🎯 **Breit**", key="faiss_broad", width="stretch", 
                        help="Schwelle: 0.60 - Findet viele Ergebnisse, auch weniger relevante"):
                st.session_state.faiss_confidence = 0.60
                st.toast("🎯 Breite Suche aktiviert (Schwelle: 0.60)")
        
        with col2:
            if st.button("⚖️ **Standard**", key="faiss_standard",
                        help="Schwelle: 0.70 - Ausgewogen zwischen Menge und Qualität"):
                st.session_state.faiss_confidence = 0.70
                st.toast("⚖️ Standard-Schwelle aktiviert (0.70)")
        
        with col3:
            if st.button("🎯 **Präzise**", key="faiss_precise",
                        help="Schwelle: 0.85 - Nur hochrelevante Ergebnisse"):
                st.session_state.faiss_confidence = 0.85
                st.toast("🎯 Präzise Suche aktiviert (Schwelle: 0.85)")
        
        with col4:
            with st.popover("⚙️ **Custom**"):
                st.markdown("### 🎛️ Benutzerdefinierte Schwelle")
                st.caption("Power-User-Modus: Wählen Sie eine beliebige Schwelle.")
                
                custom_conf = st.slider(
                    "Confidence Threshold", 
                    min_value=0.50, 
                    max_value=0.95, 
                    value=st.session_state.faiss_confidence,
                    step=0.05,
                    help="0.50 = Viele Ergebnisse, 0.95 = Nur perfekte Matches"
                )
                
                st.info(f"""
                **Aktuell ausgewählt**: {custom_conf:.2f}
                
                **Erwartete Ergebnisse**:
                - 0.50-0.65: Viele Ergebnisse (auch ungenau) 📚
                - 0.66-0.75: Gute Balance (Standard) ⚖️
                - 0.76-0.85: Weniger aber relevanter 🎯
                - 0.86-0.95: Nur perfekte Matches 💎
                
                **Hinweis**: Zu hohe Werte können zu 0 Ergebnissen führen!
                """)
                
                if st.button("✅ Anwenden", key="faiss_apply", type="primary"):
                    st.session_state.faiss_confidence = custom_conf
                    st.toast(f"✅ Custom-Schwelle aktiviert ({custom_conf:.2f})")
                    st.rerun()
        
        # Status indicator
        current_conf = st.session_state.get('faiss_confidence', 0.70)
        if current_conf <= 0.65:
            conf_emoji = "📚"
            conf_name = "Breit"
        elif current_conf <= 0.75:
            conf_emoji = "⚖️"
            conf_name = "Standard"
        elif current_conf <= 0.85:
            conf_emoji = "🎯"
            conf_name = "Präzise"
        else:
            conf_emoji = "💎"
            conf_name = "Ultra-Präzise"
        
        st.caption(f"{conf_emoji} **Aktuell**: {current_conf:.2f} ({conf_name})")
        
        # ================================================================
        # NEU: Verarbeitung geklickter Folgefragen
        # ================================================================
        if st.session_state.pending_followup:
            followup_text = st.session_state.pending_followup
            st.session_state.pending_followup = None  # Reset sofort
            
            # Folgefrage als User-Nachricht einfügen
            user_message = {
                "role": "user",
                "content": followup_text,
                "timestamp": datetime.now(),
                "image": None, "pdf_content": None, "excel_data": None,
                "file_metadata": None, "generated_image": None, "internet_image": None
            }
            st.session_state.chat_history.append(user_message)
            try:
                _append_message_to_db(user_message)
            except (RuntimeError, ValueError, sqlite3.Error, PydanticValidationError) as persist_user_exc:
                logger.error(f"Persistenzfehler (Follow-up User): {persist_user_exc}", exc_info=True)
            
            # AI-Antwort generieren
            run_result = _stream_ai_response(followup_text)
            if run_result is None:
                st.rerun()
            response = run_result.text
            generated_image = run_result.generated_image
            graphics = [graphic.model_dump() for graphic in run_result.graphics]
            generated_files = [file_info.model_dump() for file_info in run_result.files]
            internet_image = run_result.internet_image
    
            # Debug-Info sammeln
            debug_info_friendly = None
            debug_info_technical = None
            orchestrator_type = None
            orchestrator_id = None
            llm_calls = None
            reasoning_complexity = None
            reasoning_token_budget = None
            reasoning_enabled = None
            reasoning_trace = None
    
            if hasattr(st.session_state, 'chat_logic') and st.session_state.chat_logic is not None:
                if hasattr(st.session_state.chat_logic, '_last_routing_debug'):
                    debug_info_technical = st.session_state.chat_logic._last_routing_debug
                if hasattr(st.session_state.chat_logic, 'orchestrator') and st.session_state.chat_logic.orchestrator:
                    orch = st.session_state.chat_logic.orchestrator
                    orchestrator_type = type(orch).__name__
                    orchestrator_id = hex(id(orch))
                if hasattr(st.session_state.chat_logic, '_last_complexity'):
                    reasoning_complexity = st.session_state.chat_logic._last_complexity
                if hasattr(st.session_state.chat_logic, '_last_token_budget'):
                    reasoning_token_budget = st.session_state.chat_logic._last_token_budget
                if hasattr(st.session_state.chat_logic, '_last_reasoning_enabled'):
                    reasoning_enabled = st.session_state.chat_logic._last_reasoning_enabled
                if hasattr(st.session_state.chat_logic, 'last_trace') and st.session_state.chat_logic.last_trace:
                    if hasattr(st.session_state.chat_logic.last_trace, 'reasoning'):
                        reasoning_trace = st.session_state.chat_logic.last_trace.reasoning
    
            ai_message = {
                "role": "assistant",
                "content": response,
                "timestamp": datetime.now(),
                "image": None,
                "generated_image": generated_image,
                "graphics": graphics,
                "files": generated_files,
                "internet_image": internet_image,
                "debug_info_friendly": debug_info_friendly,
                "debug_info_technical": debug_info_technical,
                "orchestrator_type": orchestrator_type,
                "orchestrator_id": orchestrator_id,
                "llm_calls": llm_calls,
                "reasoning_complexity": reasoning_complexity,
                "reasoning_token_budget": reasoning_token_budget,
                "reasoning_enabled": reasoning_enabled,
                "reasoning_trace": reasoning_trace,
                "chunk_ids": getattr(st.session_state.chat_logic, '_last_rag_chunk_ids', []) if st.session_state.chat_logic else [],
                "chunk_scores": getattr(st.session_state.chat_logic, '_last_rag_chunk_scores', []) if st.session_state.chat_logic else [],
                "followup_questions": getattr(st.session_state.chat_logic, 'last_followup_questions', []) if st.session_state.chat_logic else [],
            }
            st.session_state.chat_history.append(ai_message)
            try:
                _append_message_to_db(ai_message)
            except (RuntimeError, ValueError, sqlite3.Error, PydanticValidationError) as persist_ai_exc:
                logger.error(f"Persistenzfehler (Follow-up Assistant): {persist_ai_exc}", exc_info=True)
            
            st.rerun()
        
        # Chat input - this will always be at the bottom
        def _persist_text_to_rag(
            rag_store: Any,
            *,
            text: str,
            doc_id: str,
            metadata: dict[str, Any],
        ) -> dict[str, Any]:
            """Persist text in RAG using the best available API surface.

            UnifiedRagStore supports add_document/upsert_documents; legacy stores
            may expose upsert_text. This helper keeps ingest deterministic and
            explicit (general/safe) for user-uploaded normal-chat documents.
            """
            enriched_meta = {
                **metadata,
                "corpus_domain": metadata.get("corpus_domain", "general"),
                "safety_flag": metadata.get("safety_flag", "safe"),
            }

            if hasattr(rag_store, "add_document"):
                result = rag_store.add_document(
                    content=text,
                    doc_id=doc_id,
                    metadata=enriched_meta,
                    corpus_domain=enriched_meta["corpus_domain"],
                    safety_flag=enriched_meta["safety_flag"],
                )
                if isinstance(result, dict):
                    return result
                return {"success": bool(result), "inserted": 1 if result else 0}

            if hasattr(rag_store, "upsert_text"):
                result = rag_store.upsert_text(
                    text=text,
                    doc_id=doc_id,
                    metadata=enriched_meta,
                    build_kg=True,
                )
                if isinstance(result, dict):
                    return result
                return {"success": bool(result), "inserted": 1 if result else 0}

            if hasattr(rag_store, "upsert_documents"):
                result = rag_store.upsert_documents([
                    {
                        "id": doc_id,
                        "text": text,
                        "metadata": enriched_meta,
                    }
                ])
                if isinstance(result, dict):
                    return result
                return {"success": bool(result), "inserted": 1 if result else 0}

            raise RuntimeError("RAG store exposes no supported text upsert API")

        with st.expander("📊 Mermaid Studio", expanded=False):
            if not MERMAID_GENERATOR_AVAILABLE or MermaidGenerator is None:
                st.info("Mermaid-Generator ist nicht verfügbar.")
            else:
                col_type, col_title = st.columns([1, 2])
                with col_type:
                    mermaid_type = st.selectbox(
                        "Typ",
                        ["flowchart", "mindmap", "gantt", "classDiagram", "sequenceDiagram",
                         "stateDiagram", "erDiagram", "pie"],
                        key="mermaid_type_selector",
                    )
                with col_title:
                    mermaid_title = st.text_input(
                        "Titel",
                        value="Diagramm",
                        key="mermaid_title_input",
                    )

                mermaid_source_text = st.text_area(
                    "Inhalt",
                    height=140,
                    placeholder="Beschreibe Prozess, Struktur oder Sequenz...",
                    key="mermaid_source_text",
                )

                if st.button("✨ Mermaid erzeugen", key="generate_mermaid_button"):
                    try:
                        req = MermaidDiagramRequest(
                            diagram_type=mermaid_type,
                            title=mermaid_title,
                            text=mermaid_source_text,
                        )
                        diagram = MermaidGenerator.from_text(
                            text=req.text or "",
                            diagram_type=req.diagram_type,
                            title=req.title,
                        )
                        safe_code = MermaidGenerator.sanitize_mermaid_code(diagram["code"])
                        persistence_saved = False
                        chat_db = st.session_state.get('chat_db')
                        if chat_db is not None:
                            try:
                                chat_db.save_mermaid_diagram(
                                    session_id=st.session_state.chat_session_id,
                                    diagram_type=diagram["type"],
                                    title=diagram["title"],
                                    mermaid_code=safe_code,
                                    metadata={"diagram_id": diagram["id"], "source": "chat_tab"},
                                )
                                persistence_saved = True
                            except (RuntimeError, ValueError, sqlite3.Error) as persist_exc:
                                logger.error(
                                    f"Mermaid-Diagramm generiert, aber Persistenz fehlgeschlagen: {persist_exc}",
                                    exc_info=True,
                                )

                        st.session_state["mermaid_last_diagram"] = {
                            "code": safe_code,
                            "type": diagram["type"],
                            "title": diagram["title"],
                            "id": diagram["id"],
                            "persisted": persistence_saved,
                        }
                    except PydanticValidationError as validation_exc:
                        st.error(f"Ungültige Mermaid-Eingabe: {validation_exc}")
                    except (RuntimeError, ValueError) as mermaid_exc:
                        logger.error(f"Mermaid-Generierung fehlgeschlagen: {mermaid_exc}", exc_info=True)
                        st.error("Mermaid-Diagramm konnte nicht erzeugt werden.")

                last_diagram = st.session_state.get("mermaid_last_diagram")
                if last_diagram:
                    preview_html = MermaidGenerator.get_render_html(
                        last_diagram["code"], diagram_id=f"preview_{last_diagram['id']}"
                    )
                    components.html(preview_html, height=420, scrolling=True)

                    with st.expander("Mermaid-Code anzeigen", expanded=False):
                        st.code(last_diagram["code"], language="mermaid")

                    if last_diagram["persisted"]:
                        st.success("Mermaid-Diagramm erzeugt und in der Session gespeichert.")
                    else:
                        st.success("Mermaid-Diagramm erzeugt.")

                    export_html = MermaidGenerator.get_export_html(
                        last_diagram["code"], diagram_id=last_diagram["id"]
                    )
                    st.download_button(
                        label="⬇️ Diagramm als HTML exportieren (offline rendern + SVG-Speichern)",
                        data=export_html,
                        file_name=f"{last_diagram['id']}.html",
                        mime="text/html",
                        key=f"mermaid_export_{last_diagram['id']}",
                    )

                chat_db = st.session_state.get('chat_db')
                if chat_db is not None:
                    try:
                        existing_diagrams = chat_db.get_session_diagrams(st.session_state.chat_session_id)
                        if existing_diagrams:
                            st.caption(f"Gespeicherte Diagramme in dieser Session: {len(existing_diagrams)}")
                    except (RuntimeError, ValueError, sqlite3.Error) as list_exc:
                        logger.warning(f"Mermaid-Sessionliste konnte nicht geladen werden: {list_exc}")

        user_input = st.chat_input("Ihre Nachricht an die AI...")
        
        if user_input:
            try:
                validated_input = UserInput(text=user_input)
            except PydanticValidationError as validation_exc:
                st.error(f"Ungültige Eingabe: {validation_exc}")
                return

            normalized_user_input = validated_input.text

            # Add user message to history
            user_message = {
                "role": "user", 
                "content": normalized_user_input,
                "timestamp": datetime.now(),
                "image": None,
                "pdf_content": None,
                "excel_data": None,
                "file_metadata": None,
                "generated_image": None,
                "internet_image": None
            }
            
            # Handle uploaded file with type detection
            file_path = None
            file_type = None
            extracted_content = None
            file_name = None
            
            if uploaded_file:
                # Detect file type
                file_name = uploaded_file.name
                file_ext = file_name.split('.')[-1].lower()
                
                # Save temporarily
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}")
                temp_file.write(uploaded_file.read())
                temp_file.close()
                file_path = temp_file.name

                try:
                    FileUpload(
                        file_path=file_path,
                        file_name=file_name,
                        file_type=file_ext,
                        file_size=max(1, int(os.path.getsize(file_path))),
                        mime_type=getattr(uploaded_file, 'type', None),
                    )
                except PydanticValidationError as upload_validation_exc:
                    st.error(f"Ungültiger Upload: {upload_validation_exc}")
                    if os.path.exists(file_path):
                        os.unlink(file_path)
                    return
                
                # Process by file type
                logger.info(
                    "📎 Datei-Upload empfangen: name=%r ext=%s add_to_rag=%s",
                    file_name, file_ext, add_to_rag,
                )
                with st.spinner(f"📄 Verarbeite {file_name}..."):
                    if file_ext == 'pdf':
                        # ========== SOTA PDF PROCESSING (Docling → Legacy Fallback) ==========
                        try:
                            extraction_method = "unknown"
                            text = None
                            pdf_metadata = {}
                            _docling_result_for_rag = None
                            
                            # ── PRIMÄR: Docling SOTA (AI Layout + TableFormer + OCR) ──
                            try:
                                from utils.docling_processor import DoclingProcessor
                                processor = DoclingProcessor.get_instance()
                                _docling_result_for_rag = processor.convert_file(file_path)
                                
                                # ── Page-Loss-Ratio bestimmt ob Partial-Ergebnis nutzbar ist ──
                                _page_loss = _docling_result_for_rag.metadata.get('page_loss_ratio', 0.0) if _docling_result_for_rag.metadata else 0.0
                                _is_usable_partial = (
                                    _docling_result_for_rag.is_partial
                                    and _page_loss <= 0.1
                                    and _docling_result_for_rag.text
                                    and _docling_result_for_rag.text.strip()
                                )
                                
                                if (_docling_result_for_rag.success
                                        and (not _docling_result_for_rag.is_partial or _is_usable_partial)
                                        and _docling_result_for_rag.text
                                        and _docling_result_for_rag.text.strip()):
                                    text = _docling_result_for_rag.text
                                    extraction_method = "docling"
                                    pdf_metadata = {
                                        "extraction_method": "docling",
                                        "docling_tables": _docling_result_for_rag.num_tables,
                                        "docling_pages": _docling_result_for_rag.num_pages,
                                        "processing_time_s": _docling_result_for_rag.processing_time_s,
                                    }
                                    _partial_hint = " (PARTIAL, akzeptiert: page_loss={:.0%})".format(_page_loss) if _is_usable_partial else ""
                                    logger.info(
                                        f"✅ Docling PDF: {len(text)} Zeichen, "
                                        f"{_docling_result_for_rag.num_tables} Tabellen, "
                                        f"{_docling_result_for_rag.processing_time_s:.1f}s{_partial_hint}"
                                    )
                                elif _docling_result_for_rag.is_partial and _page_loss > 0.1:
                                    # Signifikanter Page-Loss (>10%) → Legacy-Fallback
                                    logger.warning(
                                        f"⚠️ Docling: signifikanter Datenverlust für {file_name} "
                                        f"(page_loss={_page_loss:.0%}), nutze Legacy-Fallback"
                                    )
                                    _docling_result_for_rag = None
                                else:
                                    logger.info(f"Docling konnte PDF nicht verarbeiten: {_docling_result_for_rag.error}, Fallback...")
                                    _docling_result_for_rag = None
                            except ImportError:
                                logger.debug("DoclingProcessor nicht verfügbar, nutze Legacy")
                            except (OSError, RuntimeError, ValueError) as docling_e:
                                logger.warning(f"Docling PDF-Extraktion fehlgeschlagen: {docling_e}, Fallback...")
                                _docling_result_for_rag = None
                            
                            # ── FALLBACK: pdfminer (echter Nicht-Docling-Pfad) ──
                            # Root-Cause-Fix: Der frühere AdvancedPDFProcessor-Fallback
                            # delegierte selbst an Docling (zirkulär) und schlug durch
                            # einen force_ocr-TypeError immer fehl.
                            if not text:
                                try:
                                    from pdfminer.high_level import extract_text as _pdfminer_extract
                                    _fallback_text = _pdfminer_extract(file_path) or ""
                                    if _fallback_text.strip():
                                        text = _fallback_text
                                        extraction_method = "pdfminer"
                                        pdf_metadata = {"extraction_method": "pdfminer"}
                                except ImportError:
                                    logger.debug("pdfminer nicht verfügbar für PDF-Fallback")
                                except Exception as _pm_exc:
                                    logger.warning(f"pdfminer PDF-Fallback fehlgeschlagen: {_pm_exc}")
                            
                            if text and len(text.strip()) > 0:
                                extracted_content = text
                                user_message["pdf_content"] = text
                                user_message["file_metadata"] = pdf_metadata
                                file_type = "pdf"
                                
                                # UI Feedback
                                st.success(f"✅ PDF verarbeitet: {len(text)} Zeichen extrahiert")
                                st.info(f"📊 Extraktionsmethode: {extraction_method}")
                                
                                # RAG Store Integration (nutzt vorberechnetes Docling-Ergebnis)
                                if add_to_rag and st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'orchestrator'):
                                    if st.session_state.chat_logic.orchestrator and hasattr(st.session_state.chat_logic.orchestrator, 'tools'):
                                        rag_store = st.session_state.chat_logic.orchestrator.tools.rag
                                        if rag_store:
                                            try:
                                                with st.spinner("💾 Speichere PDF in RAG Store..."):
                                                    result = rag_store.upsert_pdf(
                                                        file_path,
                                                        metadata={
                                                            "source": file_name,
                                                            "uploaded_at": str(datetime.now()),
                                                            "corpus_domain": "general",
                                                            "safety_flag": "safe",
                                                        },
                                                        build_kg=True,
                                                        extract_tables=True,
                                                        _docling_result=_docling_result_for_rag,
                                                    )
                                                    st.session_state.uploaded_documents.append({
                                                        "file": file_name,
                                                        "type": "pdf",
                                                        "timestamp": datetime.now(),
                                                        "chunks": result.get("inserted", 0)
                                                    })
                                                    st.success(f"✅ PDF in RAG Store gespeichert: {result.get('inserted', 0)} Chunks")
                                                    
                                                    # OPTIMIZATION #2: Invalidate KG cache after document upload
                                                    try:
                                                        from kg_dashboard import KGSessionManager
                                                        KGSessionManager.on_document_upload()
                                                    except ImportError:
                                                        logger.debug("KG Dashboard Modul nicht verfuegbar nach Dokument-Upload")
                                                    
                                            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as rag_e:
                                                logger.error(f"RAG Store Integration fehlgeschlagen: {rag_e}", exc_info=True)
                                                raise
                                elif not add_to_rag:
                                    st.info(
                                        "ℹ️ PDF nicht in Wissensbasis aufgenommen — "
                                        "Inhalt steht nur im aktuellen Chat-Turn zur Verfügung."
                                    )
                            else:
                                st.error(f"❌ Keine Inhalte aus PDF extrahiert")
                                
                        except (OSError, RuntimeError, ValueError) as pdf_e:
                            st.error(f"❌ PDF-Verarbeitung fehlgeschlagen: {pdf_e}")
                            logger.error(f"PDF processing error: {pdf_e}", exc_info=True)
                            raise
                        finally:
                            # SOTA (2026-08-28): Kühle AUX-GPU-Modelle (Docling + EasyOCR)
                            # nach dem Import entladen, damit die 3060 Ti-VRAM für den
                            # LLM-Query-Path (Reranker/NLI/Embeddings) frei ist. Lazy-Load
                            # stellt sie bei nächster Verwendung transparent wieder her.
                            # Heiße Modelle bleiben resident. Defensive: Release-Fehler
                            # dürfen den (ggf. bereits erfolgreichen) Import nicht stören.
                            try:
                                from utils.aux_model_release import release_cold_aux_models
                                release_cold_aux_models(reason="pdf_import")
                            except Exception as _aux_rel_exc:
                                logger.debug(
                                    f"AUX-Release nach PDF-Import übersprungen: {_aux_rel_exc}"
                                )
                    
                    elif file_ext in ['xlsx', 'xls', 'xlsm'] and pandas_available and pd is not None:  # type: ignore
                        # ========== ENHANCED EXCEL PROCESSING ==========
                        try:
                            from document_processors.enhanced_excel_processor import get_enhanced_excel_processor
                            
                            excel_processor = get_enhanced_excel_processor()
                            result = excel_processor.process(file_path, include_formulas=False)
                            
                            if result.get('success') and result.get('text'):
                                extracted_content = result['text']
                                user_message["excel_data"] = result['text']
                                user_message["file_metadata"] = result.get('metadata', {})
                                file_type = "excel"
                                
                                # UI Feedback mit erweiterten Details
                                metadata = result.get('metadata', {})
                                st.success(f"✅ Excel verarbeitet: {metadata.get('sheet_count', 0)} Sheet(s)")
                                
                                with st.expander("📊 Excel-Details"):
                                    st.write(f"**Summary:** {result.get('summary', 'N/A')}")
                                    st.write(f"**Sheets:** {', '.join(metadata.get('sheet_names', []))}")
                                    
                                    # Preview erstes Sheet
                                    if result.get('sheets'):
                                        first_sheet = result['sheets'][0]
                                        st.markdown(f"**{first_sheet['sheet_name']}:**")
                                        st.markdown(first_sheet['markdown_table'][:2000])  # Preview
                                
                                # RAG Store Integration
                                if add_to_rag and st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'orchestrator'):
                                    if st.session_state.chat_logic.orchestrator and hasattr(st.session_state.chat_logic.orchestrator, 'tools'):
                                        rag_store = st.session_state.chat_logic.orchestrator.tools.rag
                                        if rag_store:
                                            try:
                                                with st.spinner("💾 Speichere Excel in RAG Store..."):
                                                    rag_result = _persist_text_to_rag(
                                                        rag_store,
                                                        text=extracted_content,
                                                        doc_id=f"excel_{file_name}",
                                                        metadata={
                                                            "source": file_name,
                                                            "type": "excel",
                                                            "uploaded_at": str(datetime.now()),
                                                            "corpus_domain": "general",
                                                            "safety_flag": "safe",
                                                        },
                                                    )
                                                    st.session_state.uploaded_documents.append({
                                                        "file": file_name,
                                                        "type": "excel",
                                                        "timestamp": datetime.now(),
                                                        "chunks": rag_result.get("inserted", 0)
                                                    })
                                                    st.success(f"✅ Excel in RAG Store gespeichert: {rag_result.get('inserted', 0)} Chunks")
                                            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as rag_e:
                                                logger.error(f"RAG Store Integration fehlgeschlagen: {rag_e}", exc_info=True)
                                                raise
                                elif not add_to_rag:
                                    st.info(
                                        "ℹ️ Excel-Datei nicht in Wissensbasis aufgenommen — "
                                        "Inhalt steht nur im aktuellen Chat-Turn zur Verfügung."
                                    )
                            else:
                                st.warning("⚠️ Excel-Datei konnte nicht verarbeitet werden")
                                if result.get('metadata', {}).get('error'):
                                    st.error(f"Fehler: {result['metadata']['error']}")
                                
                        except ImportError as excel_import_e:
                            st.error(
                                "❌ Enhanced Excel Processor nicht verfügbar. "
                                "Bitte Modul 'document_processors.enhanced_excel_processor' bereitstellen."
                            )
                            raise RuntimeError(
                                "Enhanced Excel Processor fehlt"
                            ) from excel_import_e
                        except (OSError, RuntimeError, ValueError) as excel_e:
                            st.error(f"❌ Excel-Verarbeitung fehlgeschlagen: {excel_e}")
                            logger.error(f"Excel processing error: {excel_e}", exc_info=True)
                            raise
                    
                    elif file_ext == 'pptx':
                        # ========== POWERPOINT PROCESSING ==========
                        try:
                            from document_processors.pptx_processor import get_pptx_processor
                            
                            pptx_processor = get_pptx_processor()
                            result = pptx_processor.process(file_path)
                            
                            if result.get('success') and result.get('text'):
                                extracted_content = result['text']
                                user_message["pptx_content"] = result['text']
                                user_message["file_metadata"] = result.get('metadata', {})
                                file_type = "powerpoint"
                                
                                # UI Feedback
                                metadata = result.get('metadata', {})
                                slide_count = metadata.get('slide_count', 0)
                                st.success(f"✅ PowerPoint verarbeitet: {slide_count} Slide(s)")
                                
                                with st.expander("📊 PowerPoint-Details"):
                                    st.write(f"**Titel:** {metadata.get('title', 'N/A')}")
                                    st.write(f"**Autor:** {metadata.get('author', 'N/A')}")
                                    st.write(f"**Slides:** {slide_count}")
                                    
                                    # Preview erste 3 Slides
                                    slides = result.get('slides', [])
                                    for i, slide in enumerate(slides[:3], 1):
                                        st.markdown(f"**Slide {i}:**")
                                        st.text(slide['text'][:500])
                                        if slide.get('tables'):
                                            st.caption(f"📊 {len(slide['tables'])} Tabelle(n) enthalten")
                                        if slide.get('images'):
                                            st.caption(f"🖼️ {len(slide['images'])} Bild(er) enthalten")
                                
                                # RAG Store Integration
                                if add_to_rag and st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'orchestrator'):
                                    if st.session_state.chat_logic.orchestrator and hasattr(st.session_state.chat_logic.orchestrator, 'tools'):
                                        rag_store = st.session_state.chat_logic.orchestrator.tools.rag
                                        if rag_store:
                                            try:
                                                with st.spinner("💾 Speichere PowerPoint in RAG Store..."):
                                                    rag_result = _persist_text_to_rag(
                                                        rag_store,
                                                        text=extracted_content,
                                                        doc_id=f"pptx_{file_name}",
                                                        metadata={
                                                            "source": file_name,
                                                            "type": "powerpoint",
                                                            "uploaded_at": str(datetime.now()),
                                                            "corpus_domain": "general",
                                                            "safety_flag": "safe",
                                                        },
                                                    )
                                                    st.session_state.uploaded_documents.append({
                                                        "file": file_name,
                                                        "type": "powerpoint",
                                                        "timestamp": datetime.now(),
                                                        "chunks": rag_result.get("inserted", 0)
                                                    })
                                                    st.success(f"✅ PowerPoint in RAG Store gespeichert: {rag_result.get('inserted', 0)} Chunks")
                                            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as rag_e:
                                                logger.error(f"RAG Store Integration fehlgeschlagen: {rag_e}", exc_info=True)
                                                raise
                                elif not add_to_rag:
                                    st.info(
                                        "ℹ️ PowerPoint nicht in Wissensbasis aufgenommen — "
                                        "Inhalt steht nur im aktuellen Chat-Turn zur Verfügung."
                                    )
                            else:
                                st.warning("⚠️ PowerPoint-Datei konnte nicht verarbeitet werden")
                                if result.get('metadata', {}).get('error'):
                                    st.error(f"Fehler: {result['metadata']['error']}")
                                
                        except ImportError as pptx_import_e:
                            st.error("❌ PowerPoint Processor nicht verfügbar. Bitte python-pptx installieren.")
                            raise RuntimeError("PowerPoint Processor fehlt") from pptx_import_e
                        except (OSError, RuntimeError, ValueError) as pptx_e:
                            st.error(f"❌ PowerPoint-Verarbeitung fehlgeschlagen: {pptx_e}")
                            logger.error(f"PowerPoint processing error: {pptx_e}", exc_info=True)
                            raise
                    
                    elif file_ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']:
                        # ========== IMAGE PROCESSING (existing) ==========
                        # FIX: Speichere Bild als Bytes statt Dateipfad (verhindert MediaFileStorageError)
                        try:
                            with open(file_path, 'rb') as img_file:
                                image_bytes = img_file.read()
                            user_message["image"] = image_bytes  # Speichere Bytes statt Pfad
                            file_type = "image"
                            st.info(f"🖼️ Bild wird verarbeitet: {file_name}")
                        except OSError as img_e:
                            st.error(f"❌ Bild konnte nicht geladen werden: {img_e}")
                            logger.error(f"Image loading error: {img_e}", exc_info=True)
                            raise
                    
                    elif file_ext in ['txt', 'csv']:
                        # ========== TEXT FILE PROCESSING ==========
                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                text = f.read()
                            
                            if text:
                                extracted_content = text
                                user_message["file_content"] = text
                                file_type = "text"
                                st.success(f"✅ Textdatei verarbeitet: {len(text)} Zeichen")

                                # RAG Store Integration
                                if add_to_rag and st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'orchestrator'):
                                    if st.session_state.chat_logic.orchestrator and hasattr(st.session_state.chat_logic.orchestrator, 'tools'):
                                        rag_store = st.session_state.chat_logic.orchestrator.tools.rag
                                        if rag_store:
                                            with st.spinner("💾 Speichere Textdatei in RAG Store..."):
                                                rag_result = _persist_text_to_rag(
                                                    rag_store,
                                                    text=text,
                                                    doc_id=f"text_{file_name}",
                                                    metadata={
                                                        "source": file_name,
                                                        "type": "text",
                                                        "uploaded_at": str(datetime.now()),
                                                        "corpus_domain": "general",
                                                        "safety_flag": "safe",
                                                    },
                                                )
                                                st.session_state.uploaded_documents.append({
                                                    "file": file_name,
                                                    "type": "text",
                                                    "timestamp": datetime.now(),
                                                    "chunks": rag_result.get("inserted", 0),
                                                })
                                                st.success(f"✅ Textdatei in RAG Store gespeichert: {rag_result.get('inserted', 0)} Chunks")
                                elif not add_to_rag:
                                    st.info(
                                        "ℹ️ Textdatei nicht in Wissensbasis aufgenommen — "
                                        "Inhalt steht nur im aktuellen Chat-Turn zur Verfügung."
                                    )
                        except OSError as txt_e:
                            st.error(f"❌ Textdatei-Verarbeitung fehlgeschlagen: {txt_e}")
                            raise
                    
                    else:
                        st.warning(f"⚠️ Dateityp '{file_ext}' wird noch nicht unterstützt")
                
                # Cleanup: Lösche temporäre Datei nach Verarbeitung
                # (außer bei Bildern, da wir die Bytes bereits geladen haben)
                if file_path and os.path.exists(file_path):
                    try:
                        os.unlink(file_path)
                        logger.debug(f"Temporäre Datei gelöscht: {file_path}")
                    except OSError as cleanup_e:
                        logger.warning(f"Cleanup fehlgeschlagen für {file_path}: {cleanup_e}")
            
            # Erweitere User-Input mit Datei-Kontext
            if extracted_content:
                user_input_extended = f"{normalized_user_input}\n\n[Hochgeladene Datei: {file_name}]\n\n{extracted_content[:3000]}"
                if len(extracted_content) > 3000:
                    user_input_extended += f"\n\n... ({len(extracted_content) - 3000} weitere Zeichen)"
            else:
                user_input_extended = normalized_user_input
            
            st.session_state.chat_history.append(user_message)
            try:
                _append_message_to_db(user_message)
            except (RuntimeError, ValueError, sqlite3.Error, PydanticValidationError) as persist_user_exc:
                logger.error(f"Persistenzfehler (User-Nachricht): {persist_user_exc}", exc_info=True)
                st.warning("User-Nachricht konnte nicht persistent gespeichert werden.")
            
            # Get AI response with LIVE DEBUG DISPLAY
            # NEU: Live-Debug-Container für Echtzeit-Status
            live_debug_container = st.empty()
            live_debug_data = {
                'routing': None,
                'tools': [],
                'llm_calls': 0,
                'status': 'Analysiere Anfrage...',
                'steps': ['Analysiere Anfrage...'],
            }
            
            def update_live_debug():
                """Aktualisiert die Live-Debug-Anzeige nutzerfreundlich"""
                with live_debug_container.container():
                    st.markdown("### 🔄 Live-Status")
                    
                    # Hauptstatus
                    status_icon = "⏳" if live_debug_data['status'] != "Fertig" else "✅"
                    st.info(f"{status_icon} **Status:** {live_debug_data['status']}")

                    if live_debug_data['steps']:
                        st.markdown("**Verarbeitungsschritte:**")
                        last_index = len(live_debug_data['steps']) - 1
                        for index, label in enumerate(live_debug_data['steps'][-8:]):
                            absolute_index = max(0, len(live_debug_data['steps']) - 8) + index
                            step_icon = "⏳" if absolute_index == last_index else "✅"
                            st.caption(f"{step_icon} {label}")
                    
                    # Routing-Info (nutzerfreundlich)
                    if live_debug_data['routing']:
                        routing_text = live_debug_data['routing']
                        if "normal" in routing_text.lower():
                            st.success("💬 **Antwortmodus:** Direkter Chat (keine Recherche nötig)")
                        elif "agent" in routing_text.lower():
                            st.warning("🔍 **Antwortmodus:** Recherche mit externen Tools")
                        else:
                            st.info(f"🔀 **Routing:** {routing_text}")
                    
                    # Tools (vereinfacht)
                    if live_debug_data['tools']:
                        st.markdown("**🛠️ Verwendete Tools:**")
                        tool_list = ", ".join(live_debug_data['tools'])
                        st.caption(tool_list)
                    
                    # LLM-Calls (einfach)
                    if live_debug_data['llm_calls'] > 0:
                        st.caption(f"🤖 KI-Aufrufe: {live_debug_data['llm_calls']}")
            
            # Initiale Debug-Anzeige
            update_live_debug()
            
            # Progress-Callback für Live-Updates
            def progress_callback(step: str, details: str = ""):
                """Callback für Fortschritt-Updates"""
                live_debug_data['status'] = step
                
                # Mapping von technischen zu nutzerfreundlichen Begriffen
                friendly_steps = {
                    "Routing-Analyse": "Analysiere Anfragetyp...",
                    "Standard Chat": "Direkter Chat gewählt",
                    "Einfacher Chat": "Beantworte direkt",
                    "Agent-Modus": "Starte Recherche",
                    "Cache-Hit": "Antwort aus Zwischenspeicher",
                    "Tool-Ausführung": f"Nutze Tool: {details}",
                    "LLM-Call": "KI generiert Antwort..."
                }
                
                live_debug_data['status'] = friendly_steps.get(step, step)
                if live_debug_data['steps'][-1] != live_debug_data['status']:
                    live_debug_data['steps'].append(live_debug_data['status'])
                
                # Routing-Info extrahieren
                if "routing" in step.lower():
                    if "normal" in details.lower() or "einfach" in details.lower():
                        live_debug_data['routing'] = "normal"
                    elif "agent" in details.lower():
                        live_debug_data['routing'] = "agent"
                
                # Tool-Info extrahieren
                if "tool" in step.lower() and details:
                    tool_name = details.split(":")[0].strip() if ":" in details else details
                    if tool_name not in live_debug_data['tools']:
                        live_debug_data['tools'].append(tool_name)
                
                # LLM-Call-Counter
                if "llm" in step.lower():
                    live_debug_data['llm_calls'] += 1
                
                update_live_debug()
            
            # Get AI response mit Progress-Callback
            try:
                # FIX: Wenn Bild als Bytes gespeichert, temporäre Datei für Vision-Analyse erstellen
                temp_image_path_for_vision = None
                if file_type == "image" and user_message.get("image"):
                    try:
                        # Erstelle temporäre Datei aus Bytes für Vision-Analyse
                        image_data = user_message["image"]
                        if isinstance(image_data, (bytes, bytearray)):
                            # Bytes → Temporäre Datei
                            temp_file = tempfile.NamedTemporaryFile(mode='wb', suffix='.jpg', delete=False)
                            temp_file.write(image_data)
                            temp_file.close()
                            temp_image_path_for_vision = temp_file.name
                            logger.info(f"🖼️ Temporäres Bild für Vision-Analyse: {temp_image_path_for_vision}")
                        elif isinstance(image_data, str) and os.path.exists(image_data):
                            # Alter Pfad - direkt nutzen
                            temp_image_path_for_vision = image_data
                    except OSError as img_temp_e:
                        logger.error(f"Fehler beim Erstellen temporärer Bild-Datei: {img_temp_e}")
                        raise
                
                if file_type == "image" and temp_image_path_for_vision:
                    run_result = _stream_ai_response(
                        normalized_user_input,
                        temp_image_path_for_vision,
                        status_callback=lambda label: progress_callback(label),
                    )
                else:
                    run_result = _stream_ai_response(
                        user_input_extended,
                        status_callback=lambda label: progress_callback(label),
                    )

                if run_result is None:
                    st.rerun()
                response = run_result.text
                generated_image = run_result.generated_image
                graphics = [graphic.model_dump() for graphic in run_result.graphics]
                generated_files = [file_info.model_dump() for file_info in run_result.files]
                internet_image = run_result.internet_image
                
                # Cleanup: Lösche temporäre Vision-Datei nach Analyse
                if temp_image_path_for_vision and temp_image_path_for_vision != file_path:
                    try:
                        if os.path.exists(temp_image_path_for_vision):
                            os.unlink(temp_image_path_for_vision)
                            logger.debug(f"Vision-Datei gelöscht: {temp_image_path_for_vision}")
                    except OSError as cleanup_e:
                        logger.warning(f"Vision-Datei Cleanup fehlgeschlagen: {cleanup_e}")
            
            except (RuntimeError, ValueError, OSError) as e:
                live_debug_data['status'] = f"Fehler: {str(e)}"
                update_live_debug()
                raise
            
            # Sammle NUTZERFREUNDLICHE Debug-Informationen
            debug_info_friendly = None
            debug_info_technical = None
            
            # Nutzerfreundliche Zusammenfassung
            if live_debug_data['routing'] or live_debug_data['tools']:
                friendly_parts = []
                
                # Routing-Info
                if live_debug_data['routing'] == "normal":
                    friendly_parts.append("✅ **Antwortmodus:** Direkter Chat (keine Recherche nötig)")
                elif live_debug_data['routing'] == "agent":
                    friendly_parts.append("🔍 **Antwortmodus:** Recherche mit externen Tools")
                
                # Tools
                if live_debug_data['tools']:
                    tool_names = ", ".join(live_debug_data['tools'])
                    friendly_parts.append(f"🛠️ **Verwendete Tools:** {tool_names}")
                
                # LLM-Calls
                if live_debug_data['llm_calls'] > 0:
                    friendly_parts.append(f"🤖 **KI-Aufrufe:** {live_debug_data['llm_calls']}")
                
                debug_info_friendly = "\n".join(friendly_parts)
            
            # Technische Details (optional)
            orchestrator_type = None
            orchestrator_id = None
            llm_calls = None
            
            if hasattr(st.session_state.chat_logic, '_last_routing_debug'):
                debug_info_technical = st.session_state.chat_logic._last_routing_debug
            
            if hasattr(st.session_state.chat_logic, 'orchestrator'):
                orch = st.session_state.chat_logic.orchestrator
                orchestrator_type = type(orch).__name__
                orchestrator_id = hex(id(orch))
            
            if hasattr(st.session_state.chat_logic, 'model') and hasattr(st.session_state.chat_logic.model, '_llm_call_count'):
                llm_calls = st.session_state.chat_logic.model._llm_call_count
            
            # Clear live debug nach Abschluss (ohne künstliche Verzögerung)
            live_debug_container.empty()
            
            # ================================================================
            # NEU: REASONING-OPTIMIZER - Daten sammeln für Message-History
            # ================================================================
            reasoning_complexity = None
            reasoning_token_budget = None
            reasoning_enabled = None
            reasoning_trace = None
            
            # WICHTIG: Erst prüfen, ob chat_logic existiert!
            if hasattr(st.session_state, 'chat_logic') and st.session_state.chat_logic is not None:
                if hasattr(st.session_state.chat_logic, '_last_complexity'):
                    reasoning_complexity = st.session_state.chat_logic._last_complexity
                
                if hasattr(st.session_state.chat_logic, '_last_token_budget'):
                    reasoning_token_budget = st.session_state.chat_logic._last_token_budget
                
                if hasattr(st.session_state.chat_logic, '_last_reasoning_enabled'):
                    reasoning_enabled = st.session_state.chat_logic._last_reasoning_enabled
                
                # Reasoning-Trace aus last_trace extrahieren (falls vorhanden)
                if hasattr(st.session_state.chat_logic, 'last_trace') and st.session_state.chat_logic.last_trace:
                    if hasattr(st.session_state.chat_logic.last_trace, 'reasoning'):
                        reasoning_trace = st.session_state.chat_logic.last_trace.reasoning
            
            # Add AI response to history
            ai_message = {
                "role": "assistant", 
                "content": response, 
                "timestamp": datetime.now(),
                "image": None,
                "generated_image": generated_image,
                "graphics": graphics,
                "files": generated_files,
                "internet_image": internet_image,
                "generated_diagram_backend": st.session_state.get("last_generated_diagram_backend"),
                "generated_diagram_type": st.session_state.get("last_generated_diagram_type"),
                "debug_info_friendly": debug_info_friendly,
                "debug_info_technical": debug_info_technical,
                "orchestrator_type": orchestrator_type,
                "orchestrator_id": orchestrator_id,
                "llm_calls": llm_calls,
                # NEU: Reasoning-Optimizer Daten
                "reasoning_complexity": reasoning_complexity,
                "reasoning_token_budget": reasoning_token_budget,
                "reasoning_enabled": reasoning_enabled,
                "reasoning_trace": reasoning_trace,
                # ★ SOTA v3: Chunk-IDs for feedback loop
                "chunk_ids": getattr(st.session_state.chat_logic, '_last_rag_chunk_ids', []),
                "chunk_scores": getattr(st.session_state.chat_logic, '_last_rag_chunk_scores', []),
                # Pre-extrahierte Follow-Up-Fragen aus dem Orchestrator
                "followup_questions": getattr(st.session_state.chat_logic, 'last_followup_questions', []) if st.session_state.chat_logic else [],
            }
            st.session_state.chat_history.append(ai_message)
            try:
                _append_message_to_db(ai_message)
            except (RuntimeError, ValueError, sqlite3.Error, PydanticValidationError) as persist_ai_exc:
                logger.error(f"Persistenzfehler (Assistant-Nachricht): {persist_ai_exc}", exc_info=True)
                st.warning("Assistant-Nachricht konnte nicht persistent gespeichert werden.")
            
            # Cleanup temp file
            if file_path and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except OSError as cleanup_err:
                    logger.warning(f"Temp file cleanup failed: {cleanup_err}")
            
            # Rerun to update chat display
            st.rerun()
        
        # Clear chat button at the very bottom
        if st.button("🗑️ Chat löschen", key="clear_chat"):
            # Lösche UI-Historie
            st.session_state.chat_history = []
            st.session_state.pop("mermaid_last_diagram", None)
            st.session_state.chat_session_id = f"chat_{uuid.uuid4().hex[:16]}"
            st.session_state.chat_history_loaded_from_db = False
            
            # Lösche interne Bot-Historie (WICHTIG!)
            if st.session_state.chat_logic and hasattr(st.session_state.chat_logic, 'reset_context'):
                st.session_state.chat_logic.reset_context()
                st.toast("✅ Chat-Kontext vollständig zurückgesetzt", icon="🗑️")
            
            st.rerun()
    
    else:
        st.info("🚀 Bitte laden Sie zuerst das AI-System, um zu chatten.")
        
