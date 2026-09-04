"""
I18nManager - Centralized translation manager for multi-language support.

SOTA-Design (2025/2026):
- Session-isoliert via thread-local storage (keine globalen Singleton-Zustände)
- Locale-Negotiation: explicit > session > header/context > fallback
- Fallback chain: current_lang -> de -> en
- Thread-safe für parallele Streamlit-Session-Neustarts
"""

import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).parent / "locales"
SUPPORTED_LANGUAGES = ["de", "bg", "en"]
DEFAULT_LANGUAGE = "de"
FALLBACK_LANGUAGE = "en"

# Thread-local storage für session-isolierte Sprachzustände
_thread_local = threading.local()


# ------------------------------------------------------------------ #
# Module-level convenience functions (session-aware)
# ------------------------------------------------------------------ #

def _get_session_i18n() -> "I18nSession":
    """Retrieve or create the session-isolated i18n context for current thread."""
    if not hasattr(_thread_local, 'session') or _thread_local.session is None:
        _thread_local.session = I18nSession()
    return _thread_local.session


def set_language(lang: str):
    """Module-level convenience: set language for current session."""
    _get_session_i18n().set_language(lang)


def get_current_language() -> str:
    """Module-level convenience: get current language for current session."""
    return _get_session_i18n().get_current_language()


def translate(key: str, **kwargs) -> str:
    """Module-level convenience: translate for current session."""
    return _get_session_i18n().translate(key, **kwargs)


def t(key: str, default: Optional[str] = None, **kwargs) -> str:
    """Module-level convenience: shorthand for translate.

    Args:
        key: Dotted i18n key (e.g. "wellbeing.disclaimer").
        default: Optional fallback text returned when the key is missing in
            all locales. Backward compatible: omit for previous behavior
            (the key itself is returned).
    """
    return _get_session_i18n().t(key, default, **kwargs)


def reset_session():
    """Reset session i18n state (for testing or session cleanup)."""
    _thread_local.session = None


# ------------------------------------------------------------------ #
# I18nSession - per-thread language context
# ------------------------------------------------------------------ #

class I18nSession:
    """
    Session-isolierte i18n-Instanz. Jeder Thread/Session erhält
    seinen eigenen Sprachzustand, der bei Session-Neustarts nicht
    mit anderen Sessions interferiert.
    """

    # Shared locale data (read-only, loaded once)
    _locales: Dict[str, Dict[str, Any]] = {}
    _locales_loaded: bool = False

    @classmethod
    def _ensure_locales_loaded(cls):
        """Lazy-load all locale files on first access."""
        if not cls._locales_loaded:
            cls._load_all_locales()
            cls._locales_loaded = True

    @classmethod
    def _load_all_locales(cls):
        """Load all available locale files."""
        cls._locales.clear()
        for lang in SUPPORTED_LANGUAGES:
            locale_path = LOCALES_DIR / f"{lang}.json"
            if locale_path.exists():
                try:
                    with open(locale_path, "r", encoding="utf-8") as f:
                        cls._locales[lang] = json.load(f)
                    logger.info(f"[I18nSession] Loaded locale: {lang}")
                except (json.JSONDecodeError, IOError) as e:
                    logger.error(f"[I18nSession] Failed to load locale {lang}: {e}")
            else:
                logger.warning(f"[I18nSession] Locale file not found: {locale_path}")

    @classmethod
    def reload_locales(cls):
        """Reload all locale files (useful for runtime updates)."""
        cls._load_all_locales()
        logger.info("[I18nSession] All locales reloaded")

    def __init__(self):
        self._current_language: str = DEFAULT_LANGUAGE
        self._user_preferred_language: Optional[str] = None
        self._ensure_locales_loaded()

    # -- language switching --

    def set_language(self, lang: str):
        """Set the current UI language for this session."""
        normalized = lang.lower().strip() if lang else DEFAULT_LANGUAGE
        if normalized in SUPPORTED_LANGUAGES:
            self._current_language = normalized
            logger.debug(f"[I18nSession] Language set to: {normalized}")
        else:
            logger.warning(f"[I18nSession] Unsupported language: {lang}, falling back to {DEFAULT_LANGUAGE}")
            self._current_language = DEFAULT_LANGUAGE

    def set_user_preference(self, lang: str):
        """Set user's preferred language (persisted preference)."""
        normalized = lang.lower().strip() if lang else DEFAULT_LANGUAGE
        if normalized in SUPPORTED_LANGUAGES:
            self._user_preferred_language = normalized
            self._current_language = normalized
            logger.info(f"[I18nSession] User preference set to: {normalized}")
        else:
            logger.warning(f"[I18nSession] Unsupported user preference: {lang}")

    def get_current_language(self) -> str:
        """Get the current active language code for this session."""
        return self._current_language

    def get_user_preferred_language(self) -> Optional[str]:
        """Get the user's preferred language."""
        return self._user_preferred_language

    def reset_to_default(self):
        """Reset this session to default language."""
        self._current_language = DEFAULT_LANGUAGE
        logger.debug(f"[I18nSession] Reset to default: {DEFAULT_LANGUAGE}")

    # -- translation --

    @staticmethod
    def _nested_get(data: Dict[str, Any], key: str) -> Optional[Any]:
        """Navigate a nested dict using a dotted key path."""
        keys = key.split(".")
        current = data
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return None
        return current

    def _resolve_key(self, key: str) -> Optional[str]:
        """
        Resolve a dotted key to a translation.
        Fallback chain: current_lang -> default_lang -> fallback_lang
        (duplicates removed, order preserved).
        """
        candidate_order = [
            self._current_language,
            DEFAULT_LANGUAGE,
            FALLBACK_LANGUAGE,
        ]
        # Deduplicate while preserving order
        seen: set[str] = set()
        langs_to_try: list[str] = []
        for lang in candidate_order:
            if lang not in seen:
                seen.add(lang)
                langs_to_try.append(lang)
        for lang in langs_to_try:
            if lang in self._locales:
                value = self._nested_get(self._locales[lang], key)
                if value is not None:
                    return str(value)

        logger.warning(f"[I18nSession] Translation key not found: {key}")
        return key

    def translate(self, key: str, **kwargs) -> str:
        """
        Translate a key to the current language.
        Supports variable substitution: translate("greeting.hello", name="Max")
        """
        result = self._resolve_key(key)
        if result is None:
            return key

        if kwargs:
            try:
                result = result.format(**kwargs)
            except (KeyError, IndexError, ValueError) as e:
                logger.warning(f"[I18nSession] Format error for key {key}: {e}")
        return result

    def t(self, key: str, default: Optional[str] = None, **kwargs) -> str:
        """Shorthand for translate() with an optional fallback.

        If the key is not found in any locale, ``default`` is returned
        instead of the raw key. ``t(key)`` behaves exactly as before.
        """
        result = self.translate(key, **kwargs)
        if result == key and default is not None:
            return default
        return result

    def get_prompt(self, prompt_key: str, context: str = "general") -> str:
        """
        Get a system prompt translated to the current language.
        
        Args:
            prompt_key: Prompt identifier (e.g., 'system.chatbot', 'system.psychological')
            context: Optional context for domain-specific prompts
        
        Returns:
            Translated prompt string
        """
        full_key = f"prompts.{context}.{prompt_key}"
        result = self._resolve_key(full_key)

        general_key = f"prompts.general.{prompt_key}"
        if result is None or result == full_key:
            result = self._resolve_key(general_key)

        if result is None or result == general_key:
            logger.warning(f"[I18nSession] Prompt not found: {prompt_key} (context: {context})")
            return prompt_key

        return str(result)

    # -- metadata --

    def get_supported_languages(self) -> List[str]:
        """Return list of supported language codes."""
        return list(SUPPORTED_LANGUAGES)

    def get_language_names(self) -> Dict[str, str]:
        """Return human-readable language names in current language."""
        return {
            "de": self.t("languages.de"),
            "bg": self.t("languages.bg"),
            "en": self.t("languages.en"),
        }


# ------------------------------------------------------------------ #
# Backwards-compatible singleton (for existing import patterns)
# ------------------------------------------------------------------ #

class I18nManager:
    """
    Backwards-compatible wrapper that delegates to the session i18n.
    
    Existing code like `from i18n import I18nManager; i18n = I18nManager()`
    will still work, but the global `i18n` object below is preferred.
    """

    def __init__(self, default_language: Optional[str] = None):
        """
        Create an I18nManager instance.
        
        Args:
            default_language: Optional override for the default language.
                            If provided, this language will be used as the initial
                            language instead of the module default (de).
        """
        # Create a dedicated session for this instance
        self._session = I18nSession()
        if default_language:
            self._session.set_language(default_language)

    def translate(self, key: str, **kwargs) -> str:
        return self._session.translate(key, **kwargs)

    def t(self, key: str, default: Optional[str] = None, **kwargs) -> str:
        return self._session.t(key, default, **kwargs)

    def set_language(self, lang: str):
        self._session.set_language(lang)

    def set_user_preference(self, lang: str):
        self._session.set_user_preference(lang)

    def get_current_language(self) -> str:
        return self._session.get_current_language()

    def get_user_preferred_language(self) -> Optional[str]:
        return self._session.get_user_preferred_language()

    def get_supported_languages(self) -> List[str]:
        return self._session.get_supported_languages()

    def get_language_names(self) -> Dict[str, str]:
        return self._session.get_language_names()

    def get_prompt(self, prompt_key: str, context: str = "general") -> str:
        return self._session.get_prompt(prompt_key, context)

    def reload_locales(self):
        self._session.reload_locales()

    def reset_to_default(self):
        self._session.reset_to_default()


# Global singleton instance (module-level API)
i18n = I18nManager()