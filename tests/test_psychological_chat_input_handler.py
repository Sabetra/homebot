from contextlib import nullcontext
from types import SimpleNamespace

import wellbeing_session.handlers.chat_input_handler as handler_module
from wellbeing_session.adapters.session_manager_adapter import AddMessageResult
from wellbeing_session.handlers.chat_input_handler import ChatInputHandler


class SessionState(dict):
    def __getattr__(self, name):
        return self[name]

    def __setattr__(self, name, value):
        self[name] = value


class FakeStreamlit:
    def __init__(self, session_id="session-old"):
        self.session_state = SessionState(psych_current_session=session_id)
        self.errors = []
        self.rendered_markdown = []
        self.rerun_count = 0

    def spinner(self, _message):
        return nullcontext()

    def chat_message(self, _role):
        return nullcontext()

    def error(self, message):
        self.errors.append(message)

    def info(self, _message):
        return None

    def markdown(self, message):
        self.rendered_markdown.append(message)

    def rerun(self):
        self.rerun_count += 1


class FakeEmotionalAnalyzer:
    chat_logic = None

    def analyze_emotional_state(self, _content):
        return SimpleNamespace(
            dominant_emotion="neutral",
            confidence=0.2,
            intensity_level="low",
            get_primary_emotions=lambda threshold: [],
        )


class FakeContextManager:
    def should_summarize(self, _messages):
        return False


class FakeSessionManager:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def get_session_context(self, _session_id, max_messages=50):
        return []

    def add_message_with_result(self, session_id, role, content, emotional_markers=None, is_crisis=False):
        self.calls.append((session_id, role, content, emotional_markers))
        return next(self.results)


def build_handler(results):
    session_manager = FakeSessionManager(results)
    handler = ChatInputHandler(
        session_manager=session_manager,
        emotional_analyzer=FakeEmotionalAnalyzer(),
        context_manager=FakeContextManager(),
    )
    return handler, session_manager


def test_rebind_is_applied_before_generating_and_saving_response(monkeypatch):
    fake_st = FakeStreamlit()
    monkeypatch.setattr(handler_module, "st", fake_st)
    handler, session_manager = build_handler([
        AddMessageResult.ok("session-new", rebinding_occurred=True, old_session_id="session-old"),
        AddMessageResult.ok("session-new"),
    ])

    handler.handle_psychological_chat_input("hello", lambda _message: "response")

    assert fake_st.session_state.psych_current_session == "session-new"
    assert [call[:2] for call in session_manager.calls] == [
        ("session-old", "user"),
        ("session-new", "assistant"),
    ]
    assert fake_st.errors == []
    assert fake_st.rerun_count == 1


def test_failed_user_write_stops_response_generation(monkeypatch):
    fake_st = FakeStreamlit()
    monkeypatch.setattr(handler_module, "st", fake_st)
    handler, session_manager = build_handler([
        AddMessageResult.failure("missing session", "session-old"),
    ])
    generated = False

    def generate(_message):
        nonlocal generated
        generated = True
        return "must not be generated"

    handler.handle_psychological_chat_input("hello", generate)

    assert not generated
    assert len(session_manager.calls) == 1
    assert fake_st.errors
    assert fake_st.rerun_count == 0


def test_failed_assistant_write_displays_generated_response_without_false_rerun(monkeypatch):
    fake_st = FakeStreamlit()
    monkeypatch.setattr(handler_module, "st", fake_st)
    handler, _session_manager = build_handler([
        AddMessageResult.ok("session-old"),
        AddMessageResult.failure("write failed", "session-old"),
    ])

    handler.handle_psychological_chat_input("hello", lambda _message: "visible response")

    assert fake_st.errors
    assert fake_st.rendered_markdown == ["visible response"]
    assert fake_st.rerun_count == 0


def test_acute_risk_is_accompanied_by_generated_response(monkeypatch):
    """Neuer Krisen-Vertrag (2026-08, Entscheidung 1b=B — WIP-Design behalten).

    `acute` blockt nicht mehr: Die LLM-Antwort (empathische Krisen-Begleitung)
    wird exakt einmal generiert (mit dem Original-Input) und als
    Assistant-Nachricht gespeichert. Der alte deterministische Krisenblock
    (Fail-Closed) ist ersatzlos entfallen; der frühere rotierende Test
    (acute ⇒ generate() nie, feste Krisenantwort) verankerte das veraltete
    Design und wurde durch diesen Vertragstest ersetzt.
    """
    fake_st = FakeStreamlit()
    monkeypatch.setattr(handler_module, "st", fake_st)
    handler, session_manager = build_handler([
        AddMessageResult.ok("session-old", risk_level="acute"),
        AddMessageResult.ok("session-old"),
    ])
    generated_messages = []

    def generate(message):
        generated_messages.append(message)
        return "empathic crisis accompaniment"

    handler.handle_psychological_chat_input("crisis", generate)

    assert generated_messages == ["crisis"]
    assert session_manager.calls[1][1] == "assistant"
    assert session_manager.calls[1][2] == "empathic crisis accompaniment"
    assert fake_st.errors == []
    assert fake_st.rendered_markdown == []
    assert fake_st.rerun_count == 1
