from types import SimpleNamespace

from ui_tabs.feedback_tab import _get_pie_autotexts


def test_get_pie_autotexts_supports_pie_container():
    labels = [object(), object()]
    autotexts = [object(), object()]

    result = SimpleNamespace(texts=[labels, autotexts])

    assert _get_pie_autotexts(result) == autotexts


def test_get_pie_autotexts_supports_legacy_tuple():
    autotexts = [object(), object()]

    result = ([], [], autotexts)

    assert _get_pie_autotexts(result) == autotexts