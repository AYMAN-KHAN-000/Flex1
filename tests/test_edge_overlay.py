from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from flex.config import Settings
from flex.ui.edge_overlay import FlexEdgeOverlay


def _application() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_overlay_bar_has_gap_from_edge_indicator(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()
    try:
        line_bottom = overlay.edge_line.mapTo(overlay, QPoint(0, 0)).y() + overlay.edge_line.height()
        bar_top = overlay._bar.mapTo(overlay, QPoint(0, 0)).y()

        assert line_bottom - bar_top == overlay._line_bar_overlap
    finally:
        overlay.close()
        app.processEvents()


def test_edge_indicator_is_narrow_with_small_top_inset(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()

    try:
        screen_top = app.primaryScreen().geometry().top()
        line_top = overlay.edge_line.mapToGlobal(QPoint(0, 0)).y()

        assert overlay.edge_line.width() == overlay.settings.edge_line_length * 2 // 3
        assert line_top - screen_top == overlay._screen_edge_gap
    finally:
        overlay.close()
        app.processEvents()


def test_edge_indicator_pops_bigger_then_shrinks_on_hover(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    monkeypatch.setattr(overlay, "_trigger_rect", lambda: QRect(0, 0, 9999, 9999))
    app.processEvents()

    try:
        resting_geometry = overlay.edge_line.geometry()
        overlay._do_show()

        assert overlay._line_animation.duration() == 140
        enlarged_geometry = overlay._line_animation.keyValueAt(0.45)
        assert enlarged_geometry.width() > resting_geometry.width()
        assert enlarged_geometry.height() > resting_geometry.height()

        for _ in range(30):
            QTest.qWait(20)
            app.processEvents()
            if overlay._line_animation.state() == QAbstractAnimation.State.Stopped:
                break
        assert overlay.edge_line.geometry() == resting_geometry
    finally:
        overlay.close()
        app.processEvents()


def test_top_edge_hover_area_includes_screen_edge_over_pill_width(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()

    try:
        screen_top = app.primaryScreen().geometry().top()
        pill_origin = overlay.edge_line.mapToGlobal(QPoint(0, 0))
        trigger = overlay._trigger_rect()

        assert trigger.left() == pill_origin.x()
        assert trigger.right() == pill_origin.x() + overlay.edge_line.width() - 1
        assert trigger.top() == screen_top
        assert trigger.contains(pill_origin.x() + overlay.edge_line.width() // 2, screen_top)
        assert not trigger.contains(pill_origin.x() - 1, screen_top)
    finally:
        overlay.close()
        app.processEvents()


def test_hover_smoothly_slides_bar_in_and_out(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()
    overlay.input_box.setText("Keep this text")
    window_geometry = overlay.geometry()
    line_geometry = overlay.edge_line.geometry()
    bar_host_size = overlay._shadow_host.size()
    bar_geometry = overlay._bar.geometry()
    input_geometry = overlay.input_box.geometry()
    monkeypatch.setattr(overlay, "_trigger_rect", lambda: QRect(0, 0, 9999, 9999))

    try:
        screen = app.primaryScreen().geometry()
        hidden_bar_global = overlay.mapToGlobal(overlay._bar_hidden_position)
        assert hidden_bar_global.y() + bar_host_size.height() <= screen.top()
        overlay._do_show()
        assert overlay._bar_animation.state() == QAbstractAnimation.State.Running

        QTest.qWait(100)
        app.processEvents()
        assert overlay._shadow_host.pos() != overlay._bar_hidden_position
        assert overlay._shadow_host.pos() != overlay._bar_resting_position
        assert overlay._bar_opacity.opacity() == 1.0
        assert overlay.input_box.geometry() == input_geometry
        assert overlay.input_box.text() == "Keep this text"

        QTest.qWait(overlay._bar_animation.duration() + 40)
        app.processEvents()

        assert overlay.geometry() == window_geometry
        assert overlay.edge_line.geometry() == line_geometry
        assert overlay._shadow_host.pos() == overlay._bar_resting_position
        assert overlay._shadow_host.size() == bar_host_size
        assert overlay._bar.geometry() == bar_geometry
        assert overlay.input_box.geometry() == input_geometry
        assert overlay.input_box.text() == "Keep this text"
        assert overlay._bar_opacity.opacity() == 1.0
        assert overlay.input_box.hasFocus()
        pill_overlap = overlay._container.childAt(
            overlay.edge_line.x() + overlay.edge_line.width() // 2,
            overlay._shadow_host.y(),
        )
        assert pill_overlap is overlay._bar

        monkeypatch.setattr(overlay, "_trigger_rect", lambda: QRect(99999, 99999, 1, 1))
        overlay._do_hide()
        assert overlay._bar_animation.state() == QAbstractAnimation.State.Running
        QTest.qWait(160)
        app.processEvents()
        assert overlay._shadow_host.pos() != overlay._bar_resting_position
        assert overlay._shadow_host.pos() != overlay._bar_hidden_position
        assert overlay._bar_opacity.opacity() == 1.0
        QTest.qWait(overlay._bar_animation.duration() + 40)
        app.processEvents()
        assert overlay._shadow_host.pos() == overlay._bar_hidden_position
        assert overlay.input_box.geometry() == input_geometry
        assert overlay._bar_opacity.opacity() == 0.0
    finally:
        overlay.close()
        app.processEvents()


def test_interactive_mask_includes_bar_shadow_padding(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    overlay._set_interactive_mask()
    app.processEvents()
    bar_origin = overlay._shadow_host.mapTo(overlay, QPoint(0, 0))
    padding = overlay._shadow_padding

    try:
        assert overlay.mask().contains(
            QPoint(bar_origin.x() - padding + 1, bar_origin.y() + 10)
        )
        assert overlay.mask().contains(
            QPoint(
                bar_origin.x() + overlay._shadow_host.width() + padding - 2,
                bar_origin.y() + 10,
            )
        )
    finally:
        overlay.close()
        app.processEvents()


def test_parent_container_leaves_room_for_shadow_on_every_side(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()
    padding = overlay._shadow_padding

    try:
        assert overlay._container.geometry().getRect() == (
            0,
            0,
            overlay.width(),
            overlay.height(),
        )
        shadow_bounds = overlay._shadow_host.geometry().adjusted(
            -padding,
            -padding,
            padding,
            padding,
        )
        assert overlay._container.rect().contains(shadow_bounds)
        assert overlay._shadow_host.x() >= padding
        assert overlay._shadow_host.y() >= padding
        assert overlay._container.width() - overlay._shadow_host.geometry().right() - 1 >= padding
        assert overlay._container.height() - overlay._shadow_host.geometry().bottom() - 1 >= padding
    finally:
        overlay.close()
        app.processEvents()


def test_overlay_input_click_keeps_overlay_open_and_focused(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    overlay.activate_overlay_mode()
    overlay._visible = True
    overlay._set_interactive_mask()
    overlay.show()
    app.processEvents()

    try:
        QTest.mouseClick(overlay.input_box, Qt.MouseButton.LeftButton)
        app.processEvents()

        assert overlay._visible is True
        assert overlay.input_box.hasFocus()
    finally:
        overlay.close()
        app.processEvents()


def test_overlay_input_placeholder_reflects_voice_state(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    voice_changes = []
    overlay.voice_input_toggled.connect(voice_changes.append)

    try:
        assert overlay.input_box.placeholderText() == "Type or speak..."

        assert overlay.voice_button.text() == ""
        assert not overlay.voice_button.icon().isNull()
        assert overlay.send_button.text() == ""
        assert not overlay.send_button.icon().isNull()

        overlay.voice_button.setChecked(True)
        overlay._toggle_voice_placeholder()
        assert overlay.input_box.placeholderText() == "Listening..."
        assert voice_changes == [True]

        overlay.voice_button.setChecked(False)
        overlay._toggle_voice_placeholder()
        assert overlay.input_box.placeholderText() == "Type or speak..."
        assert voice_changes == [True, False]
    finally:
        overlay.close()
        app.processEvents()


def test_notch_eyes_follow_pointer_and_blink(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()
    eyes = overlay._eyes
    overlay._poll_timer.stop()

    try:
        center = eyes.mapToGlobal(eyes.rect().center())
        eyes.set_gaze_position(QPoint(center.x() + 200, center.y() + 120))
        assert eyes._target_gaze_x == 1.0
        assert eyes._target_gaze_y == 1.0
        assert eyes._gaze_x < eyes._target_gaze_x
        assert eyes._gaze_y < eyes._target_gaze_y
        QTest.qWait(160)
        app.processEvents()
        assert eyes._gaze_x > 0.9
        assert eyes._gaze_y > 0.9

        open_width, open_height = eyes._eye_dimensions()
        assert open_height > open_width
        eyes._blink()
        assert eyes._blink_animation.state() == QAbstractAnimation.State.Running
        QTest.qWait(60)
        app.processEvents()
        assert 0 < eyes.blink_progress < 1
        closing_width, closing_height = eyes._eye_dimensions()
        assert closing_width > open_width
        assert closing_height < open_height

        QTest.qWait(220)
        app.processEvents()
        assert eyes.blink_progress == 0
        assert eyes._eye_dimensions() == (open_width, open_height)
        assert not eyes.grab().isNull()

        eyes.set_voice_mode(True)
        eyes._gaze_x = eyes._gaze_y = 1.0
        eyes.blink_progress = 1.0
        left_eye, right_eye = eyes._eye_rects()
        eye_bounds = eyes.rect()
        assert eye_bounds.contains(left_eye.toAlignedRect())
        assert eye_bounds.contains(right_eye.toAlignedRect())
        assert not left_eye.intersects(right_eye)

        eyes.blink_progress = 0.0
        open_width, open_height = eyes._eye_dimensions()
        assert open_width == 20
        assert open_height == 38
        eyes._gaze_y = 0.0
        eyes._gaze_x = -1.0
        leftmost_eye = eyes._eye_rects()[0]
        eyes._gaze_x = 1.0
        rightmost_eye = eyes._eye_rects()[0]
        assert rightmost_eye.center().x() - leftmost_eye.center().x() >= 20
        eyes._gaze_x = 0.0
        eyes._gaze_y = -1.0
        top_eye = eyes._eye_rects()[0]
        eyes._gaze_y = 1.0
        bottom_eye = eyes._eye_rects()[0]
        assert bottom_eye.center().y() - top_eye.center().y() >= 12
        for eye in eyes._eye_rects():
            assert eye_bounds.contains(eye.toAlignedRect())
    finally:
        overlay.close()
        app.processEvents()


def test_notch_eyes_clear_previous_gaze_position(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()
    eyes = overlay._eyes
    eyes.set_voice_mode(True)
    eyes._gaze_y = 0.0
    eyes._gaze_x = -1.0
    previous_left_eye = eyes._eye_rects()[0].toAlignedRect()
    eyes.grab()

    try:
        eyes._gaze_x = 1.0
        eyes.update()
        app.processEvents()
        image = eyes.grab().toImage()
        stale_pixel = image.pixelColor(
            previous_left_eye.left() + 1,
            previous_left_eye.center().y(),
        )

        assert not (
            stale_pixel.red() > 85
            and stale_pixel.blue() > 120
            and stale_pixel.blue() > stale_pixel.green() * 1.25
        )
    finally:
        overlay.close()
        app.processEvents()


def test_voice_mode_centers_eyes_and_keeps_them_as_toggle(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()
    overlay._visible = True
    overlay.show()
    overlay._set_interactive_mask()
    voice_changes = []
    overlay.voice_input_toggled.connect(voice_changes.append)

    try:
        QTest.mouseClick(overlay.voice_button, Qt.MouseButton.LeftButton)
        assert overlay.input_box.isHidden()
        assert overlay.voice_button.isHidden()
        assert overlay.send_button.isHidden()
        assert voice_changes == [True]
        assert not overlay._eyes.isHidden()
        assert overlay._eyes._voice_mode
        assert overlay._eyes.height() == overlay._bar.height()
        voice_open_width, voice_open_height = overlay._eyes._eye_dimensions()
        assert voice_open_height > voice_open_width
        assert voice_open_height == 38
        voice_centers = overlay._eyes._eye_centers()
        assert voice_centers[1] - voice_centers[0] >= voice_open_width + 8
        overlay._eyes._gaze_y = 1.0
        for eye in overlay._eyes._eye_rects():
            assert eye.top() >= 0
            assert eye.bottom() + 1.5 <= overlay._eyes.height()
        overlay._eyes._gaze_y = 0.0
        overlay._eyes.blink_progress = 1.0
        left_eye, right_eye = overlay._eyes._eye_rects()
        assert overlay._eyes.rect().contains(left_eye.toAlignedRect())
        assert overlay._eyes.rect().contains(right_eye.toAlignedRect())
        assert not left_eye.intersects(right_eye)
        voice_closed_width, voice_closed_height = overlay._eyes._eye_dimensions()
        assert voice_closed_width > voice_open_width
        assert voice_closed_height < voice_open_height
        assert voice_centers[1] - voice_centers[0] >= voice_closed_width + 8
        overlay._eyes.blink_progress = 0.0
        assert (
            overlay._bar_layout.itemAt(0).alignment()
            & Qt.AlignmentFlag.AlignCenter
        )
        overlay._bar_layout.activate()
        eyes_center = overlay._eyes.mapTo(
            overlay._container,
            overlay._eyes.rect().center(),
        )
        bar_center = overlay._bar.mapTo(
            overlay._container,
            overlay._bar.rect().center(),
        )
        assert eyes_center.x() == bar_center.x()
        assert overlay._eyes.width() == 144
        assert overlay._eyes.parentWidget() is overlay._bar
        assert overlay._eyes.geometry().top() >= 0
        assert overlay._eyes.geometry().bottom() < overlay._bar.height()
        for eye in overlay._eyes._eye_rects():
            assert overlay._eyes.rect().contains(eye.toAlignedRect())

        QTest.mouseClick(overlay._eyes, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert overlay._eye_mode
        assert voice_changes == [True]
        assert overlay.input_box.isHidden()
        assert overlay.voice_button.isHidden()
        assert overlay.send_button.isHidden()

        overlay._handle_global_click(99999, 99999)
        assert overlay._visible is True
        assert overlay._eye_mode

        QTest.mouseClick(overlay._bar, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert not overlay._eye_mode
        assert voice_changes == [True, False]
        assert not overlay.input_box.isHidden()
        assert not overlay.voice_button.isHidden()
        assert not overlay.send_button.isHidden()

        QTest.mouseClick(overlay.voice_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert voice_changes == [True, False, True]
        assert overlay.voice_button.isHidden()
        assert not overlay._eyes.isHidden()
        assert overlay._eye_mode
        left_eye, right_eye = overlay._eyes._eye_rects()
        assert overlay._eyes.rect().contains(left_eye.toAlignedRect())
        assert overlay._eyes.rect().contains(right_eye.toAlignedRect())
        assert not left_eye.intersects(right_eye)
        assert overlay._eyes.width() == 144
        eyes_center = overlay._eyes.mapTo(
            overlay._container,
            overlay._eyes.rect().center(),
        )
        bar_center = overlay._bar.mapTo(
            overlay._container,
            overlay._bar.rect().center(),
        )
        assert eyes_center.x() == bar_center.x()
        assert overlay._eyes.parentWidget() is overlay._bar
        assert overlay._eyes.geometry().top() >= 0
        assert overlay._eyes.geometry().bottom() < overlay._bar.height()
    finally:
        overlay.close()
        app.processEvents()


def test_clicking_eyes_from_text_mode_has_no_mode_action(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    overlay._visible = True
    overlay.show()
    overlay._set_interactive_mask()
    app.processEvents()

    try:
        QTest.mouseClick(overlay._eyes, Qt.MouseButton.LeftButton)
        app.processEvents()

        assert not hasattr(overlay, "set_agent_mode")
        assert not hasattr(overlay, "agent_mode_changed")
        assert not overlay._eye_mode
        assert not overlay.input_box.isHidden()
        assert not overlay.voice_button.isHidden()
        assert not overlay.send_button.isHidden()
    finally:
        overlay.close()
        app.processEvents()


def test_voice_expressions_show_happiness_and_animate_sleep(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()
    eyes = overlay._eyes

    try:
        overlay.set_voice_mode(True)
        neutral_image = eyes.grab().toImage()
        overlay.set_expression("happy")
        assert eyes._expression_animation is not None
        assert eyes._expression_animation.state() == QAbstractAnimation.State.Running
        eyes.expression_progress = 1.0
        happy_image = eyes.grab().toImage()
        assert eyes._expression == "happy"
        assert neutral_image != happy_image

        overlay.set_expression("sleep")
        assert eyes._expression_animation is not None
        assert eyes._expression_animation.state() == QAbstractAnimation.State.Running
        eyes.expression_progress = 1.0
        initial_phase = eyes._sleep_animation_phase
        eyes._advance_sleep_animation()
        assert eyes._expression == "sleep"
        assert eyes._sleep_animation_timer.isActive()
        assert not eyes._sleep_z_pixmap.isNull()
        assert eyes._eye_dimensions() == (38.0, 9.0)
        assert eyes._sleep_animation_phase != initial_phase

        overlay.set_expression("neutral")
        eyes.expression_progress = 1.0
        assert not eyes._sleep_animation_timer.isActive()
        overlay.set_voice_mode(False)
        assert eyes._expression == "neutral"
    finally:
        overlay.close()
        app.processEvents()


def test_sleep_expression_ignores_pointer_gaze(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()

    try:
        eyes = overlay._eyes
        eyes.set_voice_mode(True)
        eyes.set_expression("sleep")
        eyes.expression_progress = 1.0
        eyes._gaze_x = eyes._gaze_y = 0.0
        asleep_rects = eyes._eye_rects()
        eyes.set_gaze_position(QPoint(10_000, 10_000))

        assert not eyes._gaze_timer.isActive()
        assert eyes._target_gaze_x == 0.0
        assert eyes._target_gaze_y == 0.0
        eyes._gaze_x = eyes._gaze_y = 1.0
        assert eyes._eye_rects() == asleep_rects
    finally:
        overlay.close()
        app.processEvents()


def test_waking_from_sleep_blinks_exactly_twice(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()

    try:
        eyes = overlay._eyes
        eyes.set_voice_mode(True)
        eyes.set_expression("sleep")
        eyes.expression_progress = 1.0
        eyes.set_expression("neutral")
        assert eyes._expression_animation is not None
        assert eyes._expression_animation.state() == QAbstractAnimation.State.Running

        QTest.qWait(850)
        app.processEvents()

        assert eyes._wake_blinks_remaining == 0
        assert eyes.blink_progress == 0.0
        assert eyes._blink_timer.isActive()
    finally:
        overlay.close()
        app.processEvents()


def test_voice_mode_eyes_stay_separated_and_notch_ends_are_rounded(
    monkeypatch,
    tmp_path,
):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()

    try:
        overlay._set_eye_mode(True)
        overlay._bar_opacity.setOpacity(1.0)
        app.processEvents()

        assert overlay._eyes.width() == 144
        assert overlay.edge_line.rect().center().x() + overlay.edge_line.x() == (
            overlay._bar.mapTo(
                overlay._container,
                overlay._bar.rect().center(),
            ).x()
        )
        overlay._eyes._gaze_x = -1.0
        overlay._eyes._gaze_y = 0.0
        app.processEvents()
        previous_eye = overlay._eyes._eye_rects()[0].toAlignedRect()
        overlay._eyes.grab()

        overlay._eyes._gaze_x = 1.0
        overlay._eyes._request_repaint()
        app.processEvents()
        image = overlay._eyes.grab().toImage()
        stale_pixel = image.pixelColor(
            previous_eye.left() + 1,
            previous_eye.center().y(),
        )
        assert not (
            stale_pixel.red() > 85
            and stale_pixel.blue() > 120
            and stale_pixel.blue() > stale_pixel.green() * 1.25
        )

        overlay._eyes.blink_progress = 1.0
        left_eye, right_eye = overlay._eyes._eye_rects()
        assert not left_eye.intersects(right_eye)
        assert right_eye.left() - left_eye.right() >= 30

        image = overlay._bar.grab().toImage()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(image.width() - 1, 0).alpha() == 0
        assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() == 255
    finally:
        overlay.close()
        app.processEvents()


def test_voice_mode_click_outside_keeps_notch_visible(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    overlay._visible = True
    overlay._set_eye_mode(True)
    app.processEvents()

    try:
        overlay._handle_global_click(0, 0)
        overlay._do_hide()

        assert overlay._visible
        assert overlay._eye_mode
        assert overlay._bar_animation is None
    finally:
        overlay.close()
        app.processEvents()


def test_first_mic_click_repaints_old_eye_position(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    overlay._visible = True
    overlay.show()
    app.processEvents()
    overlay._bar_opacity.setOpacity(1.0)

    try:
        QTest.mouseClick(overlay.voice_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        image = overlay._eyes.grab().toImage()

        def is_eye_pixel(x: int, y: int) -> bool:
            color = image.pixelColor(x, y)
            return (
                color.red() > 85
                and color.blue() > 120
                and color.blue() > color.green() * 1.25
            )

        assert overlay._eyes._voice_mode
        assert any(
            is_eye_pixel(x, y)
            for x in range(image.width())
            for y in range(image.height())
        )
        bar_image = overlay._bar.grab().toImage()
        background_pixel = bar_image.pixelColor(
            overlay._bar.width() // 2,
            overlay._bar.height() // 2,
        )
        assert background_pixel.alpha() == 255
        assert max(
            background_pixel.red(),
            background_pixel.green(),
            background_pixel.blue(),
        ) < 80
        assert not any(
            (
                (color := bar_image.pixelColor(x, y)).red() > 85
                and color.blue() > 120
                and color.blue() > color.green() * 1.25
            )
            for x in range(24, 64)
            for y in range(10, 46)
        )
    finally:
        overlay.close()
        app.processEvents()


def test_overlay_input_is_tall_enough_for_typed_text(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    app.processEvents()

    try:
        overlay.input_box.setText("Typed text")
        assert overlay.input_box.height() >= 36
        assert overlay.input_box.text() == "Typed text"
    finally:
        overlay.close()
        app.processEvents()


def test_overlay_outside_click_still_dismisses(monkeypatch, tmp_path):
    app = _application()
    monkeypatch.setattr(FlexEdgeOverlay, "_install_global_mouse_hook", lambda _self: None)
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    overlay._visible = True
    overlay.show()
    app.processEvents()
    bar_origin = overlay._bar.mapToGlobal(QPoint(0, 0))
    outside = QPoint(0, 0)
    if QRect(bar_origin, overlay._bar.size()).contains(outside):
        outside = QPoint(bar_origin.x() + overlay._bar.width() + 20, bar_origin.y())

    try:
        overlay._handle_global_click(outside.x(), outside.y())

        assert overlay._visible is False
    finally:
        overlay.close()
        app.processEvents()
