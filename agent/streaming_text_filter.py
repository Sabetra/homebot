"""Incremental output filtering for private reasoning and role continuations."""

from __future__ import annotations


class StreamingTextFilter:
    """Remove private reasoning blocks without delaying ordinary streamed text."""

    _BLOCKS = (
        ("[think]", "[/think]"),
        ("<think>", "</think>"),
        ("<|channel>thought\n", "<channel|>"),
    )
    _FOLLOWUP = ("[follow_up]", "[/follow_up]")
    _STOP = "user:"

    def __init__(self) -> None:
        self._buffer = ""
        self._closing_marker: str | None = None
        self._capturing_followup = False
        self._followup_parts: list[str] = []
        self._stopped = False
        self._lookbehind = max(
            len(self._STOP),
            *(len(opening) for opening, _closing in self._BLOCKS),
            len(self._FOLLOWUP[0]),
        ) - 1

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def followup_block(self) -> str:
        """Raw content captured from a streamed ``[FOLLOW_UP]`` block."""
        return "".join(self._followup_parts).strip()

    def feed(self, chunk: str) -> str:
        if self._stopped or not chunk:
            return ""
        self._buffer += chunk
        return self._drain(final=False)

    def finish(self) -> str:
        if self._stopped:
            return ""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        output: list[str] = []
        while self._buffer:
            lowered = self._buffer.lower()
            if self._closing_marker is not None:
                close_at = lowered.find(self._closing_marker)
                if close_at < 0:
                    if final:
                        if self._capturing_followup:
                            self._followup_parts.append(self._buffer)
                        self._buffer = ""
                    return "".join(output)
                if self._capturing_followup:
                    self._followup_parts.append(self._buffer[:close_at])
                self._buffer = self._buffer[close_at + len(self._closing_marker):]
                self._closing_marker = None
                self._capturing_followup = False
                continue

            candidates: list[tuple[int, str, str | None, bool]] = []
            stop_at = lowered.find(self._STOP)
            if stop_at >= 0:
                candidates.append((stop_at, self._STOP, None, False))
            for opening, closing in self._BLOCKS:
                opening_at = lowered.find(opening)
                if opening_at >= 0:
                    candidates.append((opening_at, opening, closing, False))
            followup_open, followup_close = self._FOLLOWUP
            followup_at = lowered.find(followup_open)
            if followup_at >= 0:
                candidates.append((followup_at, followup_open, followup_close, True))

            if candidates:
                marker_at, marker, candidate_closing, capture_followup = min(
                    candidates,
                    key=lambda item: item[0],
                )
                output.append(self._buffer[:marker_at])
                self._buffer = self._buffer[marker_at + len(marker):]
                if candidate_closing is None:
                    self._buffer = ""
                    self._stopped = True
                    break
                self._closing_marker = candidate_closing
                self._capturing_followup = capture_followup
                continue

            if final:
                output.append(self._buffer)
                self._buffer = ""
                break
            if len(self._buffer) <= self._lookbehind:
                break
            emit_length = len(self._buffer) - self._lookbehind
            output.append(self._buffer[:emit_length])
            self._buffer = self._buffer[emit_length:]
        return "".join(output)