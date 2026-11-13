from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from flex.config import Settings
from flex.ui.edge_overlay import FlexEdgeOverlay
from flex.ui.main_window import (
    HAPPY_EXPRESSION_DURATION_MS,
    LOVE_EXPRESSION_DURATION_MS,
    FlexMainWindow,
    _is_unclear_response,
    _is_gratitude_message,
    _message_expression,
)


@pytest.fixture(autouse=True)
def avoid_installing_global_mouse_hook(monkeypatch):
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_notch_love_expression_uses_heart_and_pauses_blinks(tmp_path):
    app = _application()
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    eyes = overlay._eyes
    eyes.set_voice_mode(True)

    try:
        neutral_image = eyes.grab().toImage()
        overlay.set_expression("love")
        app.processEvents()
        assert not eyes._love_pixmap.isNull()
        assert eyes._expression_animation is not None
        assert eyes._expression_animation.state() == QAbstractAnimation.State.Running
        assert not eyes._blink_timer.isActive()

        eyes._blink()
        assert eyes._blink_animation is None
        eyes.expression_progress = 1.0
        assert eyes._eye_dimensions() == (34.0, 34.0)
        assert neutral_image != eyes.grab().toImage()

        overlay.set_expression("neutral")
        assert eyes._expression_animation is not None
        assert eyes._expression_animation.state() == QAbstractAnimation.State.Running
        eyes._blink()
        assert eyes._blink_animation is None
        QTest.qWait(350)
        app.processEvents()
        assert eyes._blink_timer.isActive()
    finally:
        overlay.close()
        app.processEvents()


def test_wonder_expression_animates_with_larger_eyes(tmp_path):
    app = _application()
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    eyes = overlay._eyes
    eyes.set_voice_mode(True)

    try:
        neutral_image = eyes.grab().toImage()
        overlay.set_expression("wonder")
        assert eyes._expression_animation is not None
        assert eyes._expression_animation.state() == QAbstractAnimation.State.Running
        eyes.expression_progress = 1.0
        assert eyes._expression == "wonder"
        assert eyes._eye_dimensions() == (38.0, 38.0)
        assert neutral_image != eyes.grab().toImage()
    finally:
        overlay.close()
        app.processEvents()


def test_confused_expression_keeps_eye_size_and_changes_second_eye(tmp_path):
    app = _application()
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    eyes = overlay._eyes
    eyes.set_voice_mode(True)

    try:
        neutral_image = eyes.grab().toImage()
        overlay.set_expression("confused")
        assert eyes._expression_animation is not None
        assert eyes._expression_animation.state() == QAbstractAnimation.State.Running
        eyes.expression_progress = 1.0
        assert eyes._eye_dimensions() == (20.0, 38.0)
        assert neutral_image != eyes.grab().toImage()
    finally:
        overlay.close()
        app.processEvents()


def test_sad_expression_is_not_supported(tmp_path):
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    try:
        with pytest.raises(ValueError, match="Unknown notch expression"):
            overlay.set_expression("sad")
    finally:
        overlay.close()


def test_notch_clicks_do_not_request_love(tmp_path):
    app = _application()
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    overlay._set_eye_mode(True)
    overlay._visible = True
    overlay.show()
    app.processEvents()
    try:
        for _ in range(4):
            QTest.mouseClick(overlay._eyes, Qt.MouseButton.LeftButton)
        assert overlay._eyes._expression == "neutral"
    finally:
        overlay.close()
        app.processEvents()


def test_notch_has_no_mouse_pat_reaction(tmp_path):
    app = _application()
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    overlay._set_eye_mode(True)
    overlay._visible = True
    overlay.show()
    app.processEvents()
    try:
        assert not hasattr(overlay, "_track_notch_pat")
        assert not hasattr(overlay, "love_requested")
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.parametrize(
    ("text", "expression"),
    (
        ("I love you, FLEX!", "love"),
        ("I appreciate you, FLEX.", "love"),
        ("You are so sweet.", "love"),
        ("That was wonderful.", "wonder"),
        ("That was amazing.", "wonder"),
        ("I am so shocked by that.", "wonder"),
        ("Please open Notepad.", None),
    ),
)
def test_message_expression_detection(text, expression):
    assert _message_expression(text) == expression


@pytest.mark.parametrize(
    ("text", "is_unclear"),
    (
        ("I couldn't understand what you said. Could you repeat that?", True),
        ("I cannot understand the request; please clarify your request.", True),
        ("I'm not sure what you mean.", True),
        ("Please open Notepad.", False),
        ("I understand. Here is the answer.", False),
    ),
)
def test_unclear_response_detection(text, is_unclear):
    assert _is_unclear_response(text) is is_unclear


@pytest.mark.parametrize(
    "text",
    (
        "Flex, you are great!",
        "You did the task perfectly.",
        "parfect",
        "nice work",
        "you are doing great",
        "That was excellent.",
        "Thanks, I appreciate your help.",
    ),
)
def test_gratitude_reaction_recognizes_admiration(text):
    assert _is_gratitude_message(text)


@pytest.mark.parametrize(
    "text",
    (
        "The weather will be great tomorrow.",
        "Please find a perfect study folder.",
        "I need help with a great big file.",
    ),
)
def test_gratitude_reaction_ignores_unrelated_positive_words(text):
    assert not _is_gratitude_message(text)


def test_main_window_does_not_monitor_selection_for_reactions(tmp_path):
    app = _application()
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        assert not hasattr(window, "selection_worker")
        assert not hasattr(window, "_handle_selected_text")
        assert window.edge_overlay._eyes._expression == "neutral"
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


@pytest.mark.parametrize(
    ("show_expression", "expression", "duration"),
    (
        ("_show_love_expression", "love", LOVE_EXPRESSION_DURATION_MS),
        ("_show_wonder_expression", "wonder", 4_000),
        ("_show_confused_expression", "confused", HAPPY_EXPRESSION_DURATION_MS),
    ),
)
def test_timed_reactions_have_requested_durations(
    tmp_path,
    show_expression,
    expression,
    duration,
):
    app = _application()
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        getattr(window, show_expression)()
        assert window.edge_overlay._eyes._expression == expression
        assert window._happy_expression_timer.interval() == duration
        assert window._happy_expression_timer.isActive()
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_unclear_response_shows_confused_expression_for_four_seconds(tmp_path):
    app = _application()
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        window._handle_response("I couldn't understand that. Could you repeat it?")
        assert window.edge_overlay._eyes._expression == "confused"
        assert window._happy_expression_timer.interval() == 4_000
        assert window._happy_expression_timer.isActive()
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


@pytest.mark.parametrize(
    ("expression_id", "command"),
    (
        ("neutral", "natural9090-test neutral"),
        ("happy", "natural9090-test happy"),
        ("sleep", "natural9090-test sleep"),
        ("love", "natural9090-test love"),
        ("wonder", "natural9090-test wonder"),
        ("confused", "natural9090-test confused"),
    ),
)
def test_typing_expression_id_previews_notch_without_sending_request(
    tmp_path,
    expression_id,
    command,
):
    app = _application()
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        window.input_box.setText(command)
        window._handle_submit()
        assert window.edge_overlay._eyes._expression == expression_id
        assert window._happy_expression_timer.interval() == 4_000
        assert window._happy_expression_timer.isActive()
        assert window.input_box.text() == ""
        assert window._request_worker is None
        assert window.status_label.text() == f"Expression preview: {expression_id}"
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_invalid_expression_test_command_does_not_send_request(tmp_path):
    app = _application()
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        window.input_box.setText("natural9090-test unknown")
        window._handle_submit()
        assert window._request_worker is None
        assert window.input_box.text() == ""
        assert "Use natural9090-test <emotion ID>" in window.status_label.text()
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()
