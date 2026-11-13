from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QContextMenuEvent, QFont, QTextCursor, QTextFormat
from PySide6.QtWidgets import QApplication, QMenu, QTextBrowser

from flex.ui.chat_renderer import (
    ACCENT_COLOR,
    INLINE_CODE_BACKGROUND,
    ChatResponseBrowser,
    _code_block_text,
    render_markdown_document,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _blocks(document):
    block = document.begin()
    while block.isValid():
        yield block
        block = block.next()


def _fragments(document):
    for block in _blocks(document):
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                yield block, fragment
            iterator += 1


def test_headings_and_inline_formats_render_without_markdown_tokens():
    document = render_markdown_document(
        "# One\n\n## Two\n\n### Three\n\n#### Four\n\n##### Five\n\n###### Six\n\n"
        "***bold italic*** ~~removed~~ `inline`"
    )

    assert [block.blockFormat().headingLevel() for block in list(_blocks(document))[:6]] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    assert "###" not in document.toPlainText()
    assert "bold italic removed inline" in document.toPlainText()
    formats = {
        fragment.text(): fragment.charFormat()
        for _block, fragment in _fragments(document)
    }
    assert formats["bold italic"].fontWeight() == QFont.Weight.Bold
    assert formats["bold italic"].fontItalic()
    assert formats["removed"].fontStrikeOut()
    assert formats["inline"].fontFixedPitch()
    assert formats["inline"].background().color() == INLINE_CODE_BACKGROUND


def test_nested_lists_preserve_order_and_indentation():
    document = render_markdown_document("- first\n  - nested\n    1. nested ordered\n- second")
    items = [block for block in _blocks(document) if block.text()]

    assert [block.text() for block in items] == [
        "first",
        "nested",
        "nested ordered",
        "second",
    ]
    assert [block.textList().format().indent() for block in items] == [1, 2, 3, 1]


def test_links_are_styled_validated_and_exclude_trailing_punctuation():
    document = render_markdown_document(
        "[Portfolio](https://example.com/profile), "
        "https://example.org/path. www.example.net!"
    )
    links = [
        fragment
        for _block, fragment in _fragments(document)
        if fragment.charFormat().isAnchor()
    ]

    assert [fragment.charFormat().anchorHref() for fragment in links] == [
        "https://example.com/profile",
        "https://example.org/path",
        "http://www.example.net",
    ]
    assert "path. www.example.net!" in document.toPlainText()
    assert all(fragment.charFormat().foreground().color() == ACCENT_COLOR for fragment in links)
    assert all(fragment.charFormat().fontUnderline() for fragment in links)


def test_fenced_code_preserves_indentation_and_does_not_link_urls():
    document = render_markdown_document(
        "```python\n  print('https://example.com')\n    next()\n```"
    )
    code_blocks = [block for block in _blocks(document) if block.blockFormat().hasProperty(
        QTextFormat.Property.BlockCodeLanguage
    )]

    assert code_blocks
    assert code_blocks[0].blockFormat().property(
        QTextFormat.Property.BlockCodeLanguage
    ) == "python"
    assert _code_block_text(code_blocks[0]) == (
        "  print('https://example.com')\n    next()"
    )
    assert not any(
        fragment.charFormat().isAnchor()
        for _block, fragment in _fragments(document)
    )


@pytest.mark.parametrize(
    ("language", "code"),
    [
        ("html", "<a href='https://example.com'>link</a>"),
        ("javascript", "const value = 'https://example.com';"),
    ],
)
def test_html_and_javascript_code_stay_plain(language, code):
    document = render_markdown_document(f"```{language}\n{code}\n```")

    assert code in document.toPlainText()
    assert not any(
        fragment.charFormat().isAnchor()
        for _block, fragment in _fragments(document)
    )


def test_blockquotes_tables_and_malformed_fences_remain_readable():
    document = render_markdown_document(
        "> quoted text\n\n| Name | Value |\n| --- | --- |\n| FLEX | ready |\n\n"
        "```html\n<div>unclosed fence"
    )
    blocks = list(_blocks(document))
    tables = [
        frame
        for frame in document.rootFrame().childFrames()
        if frame.__class__.__name__ == "QTextTable"
    ]

    assert any(block.blockFormat().property(QTextFormat.Property.BlockQuoteLevel) for block in blocks)
    assert len(tables) == 1
    assert tables[0].rows() == 2
    assert tables[0].columns() == 2
    assert "quoted text" in document.toPlainText()
    assert "<div>unclosed fence" in document.toPlainText()


def test_parse_failure_falls_back_to_original_plain_text(monkeypatch):
    def fail_parse(_self, _text, _features=None):
        raise RuntimeError("parse failure")

    monkeypatch.setattr("PySide6.QtGui.QTextDocument.setMarkdown", fail_parse)
    document = render_markdown_document("**original** https://example.com")

    assert document.toPlainText() == "**original** https://example.com"


def test_styling_failure_falls_back_to_original_plain_text(monkeypatch):
    def fail_styling(_document):
        raise RuntimeError("styling failure")

    monkeypatch.setattr("flex.ui.chat_renderer._style_tables", fail_styling)
    document = render_markdown_document("**original** response")

    assert document.toPlainText() == "**original** response"


def test_chat_uses_markdown_only_for_ai_and_keeps_chat_append_order(app):
    browser = ChatResponseBrowser()
    assert isinstance(browser, QTextBrowser)

    browser.append_plain_message("You", "# plain user text")
    browser.append_markdown_message("FLEX", "## formatted response")

    assert browser.toPlainText() == "You: # plain user text\nFLEX: \nformatted response"
    heading_fragment = browser.document().begin().next().next().begin().fragment()
    assert heading_fragment.charFormat().fontPointSize() == 20
    browser.close()

    paragraph_browser = ChatResponseBrowser()
    paragraph_browser.append_markdown_message("FLEX", "A plain response.")
    assert paragraph_browser.toPlainText() == "FLEX: A plain response."
    paragraph_browser.close()


def test_code_overflow_and_blockquote_accent_render_in_chat(app):
    browser = ChatResponseBrowser()
    browser.resize(320, 240)
    browser.append_markdown_message(
        "FLEX",
        "> quoted response\n\n```python\n"
        + ("very_long_code_line_" * 30)
        + "\n```",
    )
    browser.show()
    app.processEvents()
    browser.horizontalScrollBar().setValue(0)
    app.processEvents()

    image = browser.viewport().grab().toImage()
    exact_accent_pixels = sum(
        image.pixelColor(x, y).name() == "#71339a"
        for x in range(image.width())
        for y in range(image.height())
    )

    assert browser.horizontalScrollBar().maximum() > 0
    assert browser.openExternalLinks()
    assert exact_accent_pixels > 0
    browser.close()

    table_browser = ChatResponseBrowser()
    table_browser.resize(320, 200)
    table_browser.show()
    table_browser.append_markdown_message(
        "FLEX",
        "| Header | Another header |\n| --- | --- |\n| "
        + ("wide_cell_" * 20)
        + " | "
        + ("another_wide_cell_" * 20)
        + " |",
    )
    app.processEvents()
    assert table_browser.horizontalScrollBar().maximum() > 0
    table_browser.close()


def test_code_context_action_copies_the_entire_block(app, monkeypatch):
    browser = ChatResponseBrowser()
    browser.resize(420, 200)
    browser.append_markdown_message("FLEX", "```python\n  first()\n    second()\n```")
    browser.show()
    app.processEvents()

    class ImmediateMenu(QMenu):
        def exec(self, _position=None):
            copy_action = next(
                action for action in self.actions() if action.text() == "Copy Code"
            )
            copy_action.trigger()

    def create_menu(_self, _position):
        return ImmediateMenu(browser)

    monkeypatch.setattr(ChatResponseBrowser, "createStandardContextMenu", create_menu)

    code_block = next(
        block
        for block in _blocks(browser.document())
        if block.blockFormat().hasProperty(QTextFormat.Property.BlockCodeLanguage)
    )
    local_position = browser.viewport().mapTo(
        browser,
        browser.cursorRect(QTextCursor(code_block)).center(),
    )
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse,
        local_position,
        browser.mapToGlobal(local_position),
    )
    browser.contextMenuEvent(event)

    assert QApplication.clipboard().text() == "  first()\n    second()"
    browser.close()


def test_empty_and_long_responses_render(app):
    empty = render_markdown_document("")
    long_text = "A paragraph. " * 20_000
    long_document = render_markdown_document(long_text)

    assert empty.toPlainText() == ""
    assert long_document.toPlainText().startswith("A paragraph. A paragraph.")
    assert long_document.toPlainText().count("A paragraph.") == 20_000
