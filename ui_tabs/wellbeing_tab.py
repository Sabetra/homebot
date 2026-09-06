"""Renderer for the main Streamlit wellbeing tab."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def render_wellbeing_tab(get_or_init_wellbeing_interface: Callable[[], Any]) -> None:
    """Render the wellbeing tab via the lazily initialized interface."""
    wellbeing_iface = get_or_init_wellbeing_interface()
    wellbeing_iface.render_complete_interface()
