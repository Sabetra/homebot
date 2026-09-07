"""Verify i18n locale files are structurally consistent across all locales.

Guarantees:
- All locale files have the identical set of flattened keys.
- Every locale file is valid JSON.
- The i18n_manager can load each locale without errors.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).resolve().parent.parent / "i18n" / "locales"


def _locales() -> dict[str, Path]:
    return {p.stem: p for p in LOCALES_DIR.glob("*.json")}


def _flatten_keys(obj: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        keys.add(full)
        if isinstance(v, dict):
            keys.update(_flatten_keys(v, full))
    return keys


@pytest.mark.parametrize("locale_file", list(_locales().values()), ids=list(_locales().keys()))
def test_locale_is_valid_json(locale_file: Path) -> None:
    with open(locale_file, encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict)


def test_all_locales_share_same_keys() -> None:
    locales = _locales()
    key_sets = {name: _flatten_keys(json.loads(p.read_text(encoding="utf-8"))) for name, p in locales.items()}

    union = set().union(*key_sets.values())
    for name, keys in key_sets.items():
        missing = union - keys
        assert not missing, f"{name}.json is missing {len(missing)} keys: {sorted(missing)}"


def test_i18n_manager_loads_all_locales() -> None:
    from i18n.i18n_manager import I18nManager

    mgr = I18nManager()
    locales = _locales()
    for name in locales:
        mgr.set_language(name)
        # basic smoke: title should resolve
        val = mgr.t("gui.title")
        assert val, f"gui.title empty for locale {name}"


def test_t_returns_default_when_key_missing() -> None:
    """t(key, default) returns the fallback for unknown keys.

    Regression test (2026-09-01): wellbeing_session_interface renders
    compliance-critical banners (legal disclaimer, EU AI Act disclosure)
    via i18n_t(key, default) — this previously raised TypeError.
    """
    from i18n import t

    result = t("does.not.exist.key", "FALLBACK_TEXT")
    assert result == "FALLBACK_TEXT"


def test_t_returns_key_when_missing_and_no_default() -> None:
    """Backward compatibility: t(key) without default keeps returning the key."""
    from i18n import t

    result = t("does.not.exist.key")
    assert result == "does.not.exist.key"


def test_t_default_does_not_shadow_existing_key() -> None:
    """Existing keys (present in all locales) must take precedence over default."""
    from i18n import set_language, t

    for name in _locales():
        set_language(name)
        assert t("gui.title", "SHOULD_NOT_APPEAR") != "SHOULD_NOT_APPEAR"


def test_i18n_manager_t_accepts_default() -> None:
    """I18nManager.t(key, default) delegates the fallback to its session."""
    from i18n.i18n_manager import I18nManager

    mgr = I18nManager()
    assert mgr.t("does.not.exist.key", "MGR_FALLBACK") == "MGR_FALLBACK"