"""Renderer for the main Streamlit psychology tab."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def render_wellbeing_tab(get_or_init_psych_interface: Callable[[], Any]) -> None:
    """Render the psychology tab via the lazily initialized interface."""
    psych_iface = get_or_init_psych_interface()
    psych_iface.render_complete_interface()
