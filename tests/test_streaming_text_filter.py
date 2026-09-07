from agent.streaming_text_filter import StreamingTextFilter


def _filter(chunks: list[str]) -> str:
    text_filter = StreamingTextFilter()
    visible = [text_filter.feed(chunk) for chunk in chunks]
    visible.append(text_filter.finish())
    return "".join(visible)


def test_filter_removes_reasoning_across_chunk_boundaries() -> None:
    assert _filter([
        "[THI",
        "NK]private reasoning[/TH",
        "INK]Final answer",
    ]) == "Final answer"


def test_filter_handles_gemma_reasoning_channel() -> None:
    assert _filter([
        "<|channel>thought\nprivate",
        "<channel|>Public",
    ]) == "Public"


def test_filter_stops_role_continuation_across_chunks() -> None:
    text_filter = StreamingTextFilter()
    chunks = ["Answer\nUS", "ER: injected", "ignored"]
    visible = "".join(text_filter.feed(chunk) for chunk in chunks)
    visible += text_filter.finish()

    assert visible == "Answer\n"
    assert text_filter.stopped


def test_filter_streams_plain_text_without_waiting_for_completion() -> None:
    text_filter = StreamingTextFilter()
    first = text_filter.feed("A sufficiently long ordinary response")

    assert first
    assert first + text_filter.finish() == "A sufficiently long ordinary response"


def test_filter_captures_followups_without_streaming_metadata() -> None:
    text_filter = StreamingTextFilter()
    chunks = [
        "Visible answer.\n\n[FOL",
        "LOW_UP]Wie funktioniert Streaming?|Welche Grenzen gibt es?[/FOLLOW_",
        "UP]",
    ]

    visible = "".join(text_filter.feed(chunk) for chunk in chunks)
    visible += text_filter.finish()

    assert visible == "Visible answer.\n\n"
    assert text_filter.followup_block == (
        "Wie funktioniert Streaming?|Welche Grenzen gibt es?"
    )


def test_filter_captures_truncated_followup_block_at_stream_end() -> None:
    text_filter = StreamingTextFilter()

    visible = text_filter.feed(
        "Antwort.\n\n[FOLLOW_UP]Wie funktioniert Streaming?|Welche Grenzen gibt es?"
    )
    visible += text_filter.finish()

    assert visible == "Antwort.\n\n"
    assert text_filter.followup_block == (
        "Wie funktioniert Streaming?|Welche Grenzen gibt es?"
    )