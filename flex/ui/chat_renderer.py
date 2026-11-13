from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QUrl, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QTextBlock,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
    QTextFormat,
    QTextTable,
    QTextTableCellFormat,
    QTextTableFormat,
)
from PySide6.QtWidgets import QApplication, QTextBrowser

logger = logging.getLogger(__name__)

TEXT_COLOR = QColor("#f2ecf8")
MUTED_COLOR = QColor("#d3c4e3")
ACCENT_COLOR = QColor("#c783f2")
CODE_COLOR = QColor("#eadcf3")
CODE_BACKGROUND = QColor("#09050e")
INLINE_CODE_BACKGROUND = QColor("#24112f")
QUOTE_BACKGROUND = QColor("#17101f")
TABLE_HEADER_BACKGROUND = QColor("#24112f")
TABLE_CELL_BACKGROUND = QColor("#100819")
HEADING_SIZES = {1: 23, 2: 20, 3: 17, 4: 15, 5: 13, 6: 12}


def _is_code_block(block: QTextBlock) -> bool:
    block_format = block.blockFormat()
    return (
        block_format.hasProperty(QTextFormat.Property.BlockCodeLanguage)
        or block_format.nonBreakableLines()
    )


def _is_http_link(href: str) -> bool:
    url = QUrl(href)
    return url.scheme().casefold() in {"http", "https"} and bool(url.host())


def _apply_block_styles(document: QTextDocument) -> None:
    block = document.begin()
    while block.isValid():
        original_format = block.blockFormat()
        block_format = QTextBlockFormat(original_format)
        heading_level = original_format.headingLevel()
        quote_level = original_format.property(QTextFormat.Property.BlockQuoteLevel) or 0
        is_code = _is_code_block(block)
        is_list_item = block.textList() is not None

        if is_code:
            block_format.setBackground(CODE_BACKGROUND)
            block_format.setLeftMargin(10)
            block_format.setRightMargin(10)
            block_format.setTopMargin(2)
            block_format.setBottomMargin(2)
            block_format.setNonBreakableLines(True)
        elif quote_level:
            block_format.setBackground(QUOTE_BACKGROUND)
            block_format.setLeftMargin(12 + int(quote_level) * 10)
            block_format.setRightMargin(6)
            block_format.setTopMargin(3)
            block_format.setBottomMargin(3)
        elif heading_level:
            block_format.setTopMargin(10 if heading_level < 4 else 6)
            block_format.setBottomMargin(4)
        elif is_list_item:
            block_format.setTopMargin(1)
            block_format.setBottomMargin(2)
        else:
            block_format.setTopMargin(2)
            block_format.setBottomMargin(7)

        cursor = QTextCursor(block)
        cursor.setBlockFormat(block_format)
        _apply_block_char_formats(document, block, heading_level, bool(quote_level), is_code)
        block = block.next()


def _apply_block_char_formats(
    document: QTextDocument,
    block: QTextBlock,
    heading_level: int,
    is_quote: bool,
    is_code: bool,
) -> None:
    fragment_iterator = block.begin()
    while not fragment_iterator.atEnd():
        fragment = fragment_iterator.fragment()
        if fragment.isValid():
            char_format = QTextCharFormat(fragment.charFormat())
            char_format.setForeground(
                CODE_COLOR if is_code else MUTED_COLOR if is_quote else TEXT_COLOR
            )
            if is_code:
                char_format.setFontFamilies(["Consolas"])
                char_format.setFontPointSize(10)
            elif char_format.fontFixedPitch():
                char_format.setFontFamilies(["Consolas"])
                char_format.setFontPointSize(10)
                char_format.setForeground(CODE_COLOR)
                char_format.setBackground(INLINE_CODE_BACKGROUND)
            else:
                char_format.setFontFamilies(["Segoe UI"])
                if heading_level:
                    char_format.setFontPointSize(HEADING_SIZES[heading_level])
                    char_format.setFontWeight(QFont.Weight.Bold)

            if char_format.isAnchor():
                if _is_http_link(char_format.anchorHref()):
                    char_format.setForeground(ACCENT_COLOR)
                    char_format.setFontUnderline(True)
                else:
                    char_format.setAnchor(False)
                    char_format.setAnchorHref("")

            cursor = QTextCursor(document)
            cursor.setPosition(fragment.position())
            cursor.setPosition(
                fragment.position() + fragment.length(),
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.mergeCharFormat(char_format)
        fragment_iterator += 1


def _style_tables(document: QTextDocument) -> None:
    pending_frames = [document.rootFrame()]
    while pending_frames:
        frame = pending_frames.pop()
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                table_format = QTextTableFormat(child.format())
                table_format.setBorder(1)
                table_format.setBorderBrush(QColor("#71339a"))
                table_format.setCellPadding(6)
                table_format.setCellSpacing(0)
                child.setFormat(table_format)
                for row in range(child.rows()):
                    for column in range(child.columns()):
                        cell = child.cellAt(row, column)
                        cell_format = QTextTableCellFormat(cell.format())
                        cell_format.setBackground(
                            TABLE_HEADER_BACKGROUND
                            if row == 0
                            else TABLE_CELL_BACKGROUND
                        )
                        cell.setFormat(cell_format)
                        cell_cursor = cell.firstCursorPosition()
                        last_position = cell.lastCursorPosition().position()
                        while (
                            cell_cursor.block().isValid()
                            and cell_cursor.position() <= last_position
                        ):
                            block_format = QTextBlockFormat(
                                cell_cursor.block().blockFormat()
                            )
                            block_format.setNonBreakableLines(True)
                            cell_cursor.setBlockFormat(block_format)
                            if not cell_cursor.movePosition(
                                QTextCursor.MoveOperation.NextBlock
                            ):
                                break
            else:
                pending_frames.append(child)


def render_markdown_document(text: str) -> QTextDocument:
    document = QTextDocument()
    document.setDefaultFont(QFont("Segoe UI", 10))
    try:
        document.setMarkdown(
            text,
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML,
        )
        _apply_block_styles(document)
        _style_tables(document)
    except Exception:
        logger.exception("Could not render an AI response as Markdown; showing plain text.")
        document.setPlainText(text)
    return document


def _code_block_text(block: QTextBlock) -> str | None:
    if not _is_code_block(block):
        return None

    fence = block.blockFormat().property(QTextFormat.Property.BlockCodeFence)
    language = block.blockFormat().property(QTextFormat.Property.BlockCodeLanguage)
    first = block
    while first.previous().isValid():
        previous = first.previous()
        if (
            not _is_code_block(previous)
            or previous.blockFormat().property(QTextFormat.Property.BlockCodeFence) != fence
            or previous.blockFormat().property(QTextFormat.Property.BlockCodeLanguage) != language
        ):
            break
        first = previous

    lines: list[str] = []
    current = first
    while current.isValid() and _is_code_block(current):
        if (
            current.blockFormat().property(QTextFormat.Property.BlockCodeFence) != fence
            or current.blockFormat().property(QTextFormat.Property.BlockCodeLanguage) != language
        ):
            break
        lines.append(current.text())
        current = current.next()
    return "\n".join(lines)


class ChatResponseBrowser(QTextBrowser):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(True)
        self.setOpenLinks(True)
        self.setUndoRedoEnabled(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QTextBrowser#chatOutput {"
            "padding: 10px 0; color: #ffffff; background-color: transparent; "
            "border: 0; font-size: 12pt; }"
        )

    def append_markdown_message(self, speaker: str, text: str) -> None:
        self._append_message(speaker, text, markdown=True)

    def append_plain_message(self, speaker: str, text: str) -> None:
        self._append_message(speaker, text, markdown=False)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        first = self.cursorForPosition(QPoint(0, 0)).block()
        last = self.cursorForPosition(
            QPoint(0, self.viewport().height() - 1)
        ).block()
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#71339a"), 2))
        block = first
        while block.isValid():
            quote_level = block.blockFormat().property(
                QTextFormat.Property.BlockQuoteLevel
            ) or 0
            if quote_level:
                start = QTextCursor(block)
                end = QTextCursor(block)
                end.movePosition(QTextCursor.MoveOperation.EndOfBlock)
                start_rect = self.cursorRect(start)
                end_rect = self.cursorRect(end)
                for level in range(int(quote_level)):
                    x = start_rect.left() - 5 - level * 4
                    painter.drawLine(
                        x,
                        start_rect.top() + 2,
                        x,
                        end_rect.bottom() - 2,
                    )
            if block.position() == last.position():
                break
            block = block.next()
        painter.end()

    def _append_message(self, speaker: str, text: str, *, markdown: bool) -> None:
        document = self.document()
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if document.characterCount() > 1:
            cursor.insertBlock()

        speaker_format = QTextCharFormat()
        speaker_format.setFontFamilies(["Segoe UI"])
        speaker_format.setFontPointSize(10)
        speaker_format.setFontWeight(QFont.Weight.Bold)
        speaker_format.setForeground(ACCENT_COLOR if speaker == "FLEX" else MUTED_COLOR)
        cursor.insertText(f"{speaker}: ", speaker_format)

        if markdown:
            if text:
                rendered_document = render_markdown_document(text)
                first_response_block = rendered_document.begin()
                needs_own_block = (
                    first_response_block.text() == ""
                    or first_response_block.textList() is not None
                    or _is_code_block(first_response_block)
                    or first_response_block.blockFormat().headingLevel() > 0
                    or first_response_block.blockFormat().property(
                        QTextFormat.Property.BlockQuoteLevel
                    )
                )
                if needs_own_block:
                    cursor.insertBlock()
                response_start = cursor.position()
                cursor.insertFragment(QTextDocumentFragment(rendered_document))
                inserted_block = document.findBlock(response_start)
                if inserted_block.isValid():
                    response_cursor = QTextCursor(inserted_block)
                    response_cursor.setBlockFormat(
                        first_response_block.blockFormat()
                    )
        else:
            cursor.insertText(text)

        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def contextMenuEvent(self, event) -> None:
        cursor = self.cursorForPosition(event.pos())
        code = _code_block_text(cursor.block())
        menu = self.createStandardContextMenu(event.pos())
        if code is not None:
            menu.addSeparator()
            copy_code_action = menu.addAction("Copy Code")
            copy_code_action.triggered.connect(
                lambda _checked=False, value=code: QApplication.clipboard().setText(value)
            )
        menu.exec(event.globalPos())
