import py_compile


def test_enhanced_streamlit_bot_parses() -> None:
    py_compile.compile("enhanced_streamlit_bot.py", doraise=True)


def test_chat_tab_parses() -> None:
    py_compile.compile("ui_tabs/chat_tab.py", doraise=True)
