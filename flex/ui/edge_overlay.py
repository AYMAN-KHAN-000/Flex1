from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    Property,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QIcon,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class _NotchEyes(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(50, 40)
        self.setMouseTracking(True)
        self._gaze_x = 0.0
        self._gaze_y = 0.0
        self._target_gaze_x = 0.0
        self._target_gaze_y = 0.0
        self._blink_progress = 0.0
        self._voice_mode = False
        self._expression = "neutral"
        self._expression_from = "neutral"
        self._expression_progress = 1.0
        self._expression_animation: QPropertyAnimation | None = None
        self._blink_animation: QPropertyAnimation | None = None
        self._wake_blinks_remaining = 0
        self._wake_blink_sequence_active = False
        self._wake_blink_timer = QTimer(self)
        self._wake_blink_timer.setSingleShot(True)
        self._wake_blink_timer.setInterval(40)
        self._wake_blink_timer.timeout.connect(self._blink)
        self._sleep_z_pixmap = QPixmap(
            str(Path(__file__).parent / "assets" / "sleep_z.png")
        )
        if self._sleep_z_pixmap.isNull():
            logger.error("Could not load the FLEX sleep expression image.")
        self._love_pixmap = QPixmap(
            str(Path(__file__).parent / "assets" / "love_heart.png")
        )
        if self._love_pixmap.isNull():
            logger.error("Could not load the FLEX love expression image.")
        self._sleep_animation_phase = 0.0
        self._sleep_animation_timer = QTimer(self)
        self._sleep_animation_timer.setInterval(80)
        self._sleep_animation_timer.timeout.connect(self._advance_sleep_animation)
        self._gaze_timer = QTimer(self)
        self._gaze_timer.setInterval(16)
        self._gaze_timer.timeout.connect(self._ease_gaze)
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(3200)
        self._blink_timer.timeout.connect(self._blink)
        self._blink_timer.start()

    def set_gaze_position(self, global_position: QPoint) -> None:
        if self._expression == "sleep":
            return
        center = self.mapToGlobal(self.rect().center())
        self._target_gaze_x = max(
            -1.0,
            min(1.0, (global_position.x() - center.x()) / 150),
        )
        self._target_gaze_y = max(
            -1.0,
            min(1.0, (global_position.y() - center.y()) / 100),
        )
        if not self._gaze_timer.isActive():
            self._gaze_timer.start()

    def set_voice_mode(self, enabled: bool) -> None:
        self._voice_mode = enabled
        if enabled:
            self.setFixedSize(144, 72)
            if self._expression == "sleep":
                self._sleep_animation_timer.start()
        else:
            self.setFixedSize(50, 40)
        self._request_repaint()

    def set_expression(self, expression: str) -> None:
        if expression not in {
            "neutral",
            "happy",
            "sleep",
            "love",
            "wonder",
            "confused",
        }:
            raise ValueError(f"Unknown notch expression: {expression}")
        if expression == self._expression:
            return
        if self._expression_animation is not None:
            self._expression_animation.stop()
            self._expression_animation.deleteLater()
            self._expression_animation = None
        waking_from_sleep = self._expression == "sleep" and expression != "sleep"
        leaving_love = self._is_love_transition_active() and expression != "love"
        self._expression_from = self._expression
        self._expression = expression
        self._sleep_animation_phase = 0.0
        if expression == "love":
            self._blink_timer.stop()
            self._wake_blink_timer.stop()
            self._wake_blinks_remaining = 0
            self._wake_blink_sequence_active = False
            if self._blink_animation is not None:
                self._blink_animation.stop()
                self._blink_animation.deleteLater()
                self._blink_animation = None
            self._blink_progress = 0.0
        if expression == "sleep" and self._voice_mode:
            self._sleep_animation_timer.start()
            self._gaze_timer.stop()
            self._gaze_x = self._gaze_y = 0.0
            self._target_gaze_x = self._target_gaze_y = 0.0
            if self._blink_animation is not None:
                self._blink_animation.stop()
                self._blink_animation.deleteLater()
                self._blink_animation = None
            self._wake_blink_timer.stop()
            self._wake_blinks_remaining = 0
            self._wake_blink_sequence_active = False
            self._blink_progress = 0.0
        else:
            self._sleep_animation_timer.stop()
        self._expression_progress = 0.0
        self._expression_animation = QPropertyAnimation(
            self,
            b"expression_progress",
            self,
        )
        self._expression_animation.setDuration(300)
        self._expression_animation.setStartValue(0.0)
        self._expression_animation.setEndValue(1.0)
        self._expression_animation.setEasingCurve(
            QEasingCurve.Type.InOutCubic
        )
        if waking_from_sleep and expression != "love":
            self._expression_animation.finished.connect(
                self._start_wake_blinks
            )
        if leaving_love:
            self._expression_animation.finished.connect(
                self._resume_blinking
            )
        self._expression_animation.start()
        self._request_repaint()

    def _resume_blinking(self) -> None:
        if self._expression != "love":
            self._blink_timer.start()

    def _start_wake_blinks(self) -> None:
        if (
            self._expression in {"sleep", "love"}
            or not self._voice_mode
            or self._is_love_transition_active()
        ):
            return
        self._blink_timer.stop()
        self._wake_blinks_remaining = 2
        self._wake_blink_sequence_active = True
        self._blink()

    @Property(float)
    def expression_progress(self) -> float:
        return self._expression_progress

    @expression_progress.setter
    def expression_progress(self, progress: float) -> None:
        self._expression_progress = max(0.0, min(1.0, progress))
        self._request_repaint()

    def _is_love_transition_active(self) -> bool:
        return (
            self._expression == "love"
            or (
                self._expression_from == "love"
                and self._expression_progress < 1.0
            )
        )

    def _advance_sleep_animation(self) -> None:
        self._sleep_animation_phase = (
            self._sleep_animation_phase + 0.035
        ) % 1.0
        self._request_repaint()

    def _request_repaint(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            parent.update(self.geometry())
            effect = parent.graphicsEffect()
            if effect is not None and effect.isEnabled():
                effect.update()
        self.update()

    def _ease_gaze(self) -> None:
        if self._expression == "sleep":
            self._gaze_timer.stop()
            return
        self._gaze_x += (self._target_gaze_x - self._gaze_x) * 0.25
        self._gaze_y += (self._target_gaze_y - self._gaze_y) * 0.25
        if (
            abs(self._target_gaze_x - self._gaze_x) < 0.01
            and abs(self._target_gaze_y - self._gaze_y) < 0.01
        ):
            self._gaze_x = self._target_gaze_x
            self._gaze_y = self._target_gaze_y
            self._gaze_timer.stop()
        self._request_repaint()

    @Property(float)
    def blink_progress(self) -> float:
        return self._blink_progress

    @blink_progress.setter
    def blink_progress(self, progress: float) -> None:
        self._blink_progress = max(0.0, min(1.0, progress))
        self._request_repaint()

    def _blink(self) -> None:
        if self._expression == "sleep" or self._is_love_transition_active():
            if self._is_love_transition_active():
                self._blink_progress = 0.0
                self._blink_timer.stop()
            self._wake_blinks_remaining = 0
            self._wake_blink_timer.stop()
            return
        if self._blink_animation is not None:
            if self._blink_animation.state() == QAbstractAnimation.State.Running:
                return
            self._blink_animation.deleteLater()

        if self._wake_blinks_remaining:
            self._wake_blinks_remaining -= 1
        self._blink_animation = QPropertyAnimation(
            self,
            b"blink_progress",
            self,
        )
        self._blink_animation.setDuration(
            50 if self._wake_blink_sequence_active else 100
        )
        self._blink_animation.setStartValue(self._blink_progress)
        self._blink_animation.setEndValue(1.0)
        self._blink_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._blink_animation.finished.connect(self._open_eyes)
        self._blink_animation.start()

    def _open_eyes(self) -> None:
        if self._blink_animation is not None:
            self._blink_animation.deleteLater()
        self._blink_animation = QPropertyAnimation(
            self,
            b"blink_progress",
            self,
        )
        self._blink_animation.setDuration(
            80 if self._wake_blink_sequence_active else 150
        )
        self._blink_animation.setStartValue(self._blink_progress)
        self._blink_animation.setEndValue(0.0)
        self._blink_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._blink_animation.finished.connect(self._continue_wake_blinks)
        self._blink_animation.start()

    def _continue_wake_blinks(self) -> None:
        if self._wake_blinks_remaining:
            self._wake_blink_timer.start()
        else:
            self._wake_blink_sequence_active = False
            self._blink_timer.start()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)



        if not self._voice_mode:
            # Crisp circular button on the left matching right action buttons
            btn_size = 36.0
            btn_x = (self.width() - btn_size) / 2
            btn_y = (self.height() - btn_size) / 2
            btn_rect = QRectF(btn_x, btn_y, btn_size, btn_size)

            btn_path = QPainterPath()
            btn_path.addEllipse(btn_rect)
            btn_grad = QLinearGradient(btn_rect.topLeft(), btn_rect.bottomLeft())
            btn_grad.setColorAt(0, QColor(44, 42, 50, 255))
            btn_grad.setColorAt(1, QColor(16, 15, 20, 255))
            painter.fillPath(btn_path, btn_grad)
            painter.setPen(QPen(QColor(255, 255, 255, 42), 1.0))
            painter.drawPath(btn_path)

        wonder_weight = self._expression_weight("wonder")
        love_weight = self._expression_weight("love")
        confused_weight = self._expression_weight("confused")
        base_eye_weight = max(
            0.0,
            1.0 - wonder_weight - love_weight,
        )
        eye_rects = self._eye_rects()
        for eye in eye_rects:
            if base_eye_weight <= 0.0:
                continue
            painter.save()
            painter.setOpacity(base_eye_weight)
            radius = min(eye.width(), eye.height()) / 2
            eye_path = QPainterPath()
            eye_path.addRoundedRect(eye, radius, radius)

            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(
                eye.translated(0, 1.5),
                radius,
                radius,
            )
            painter.fillPath(shadow_path, QColor(20, 0, 36, 210))

            gradient = QLinearGradient(eye.topLeft(), eye.bottomRight())
            gradient.setColorAt(0.0, QColor("#f0c5ff"))
            gradient.setColorAt(0.2, QColor("#d65bff"))
            gradient.setColorAt(0.58, QColor("#a522ed"))
            gradient.setColorAt(1.0, QColor("#6200a9"))
            painter.fillPath(eye_path, gradient)

            painter.save()
            painter.setClipPath(eye_path)
            painter.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
            painter.drawLine(
                eye.left() + radius * 0.5,
                eye.top() + 1.8,
                eye.right() - radius * 0.5,
                eye.top() + 1.8,
            )
            painter.restore()

            painter.setPen(QPen(QColor(238, 183, 255, 180), 0.8))
            painter.drawPath(eye_path)
            happy_progress = self._expression_weight("happy")
            if happy_progress > 0.0:
                happy_eye = QRectF(
                    eye.left() - eye.width() * 0.1,
                    eye.bottom() - eye.height() * 0.43,
                    eye.width() * 1.2,
                    eye.height() * 0.53,
                )
                painter.save()
                painter.setOpacity(happy_progress)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(5, 4, 8))
                painter.drawEllipse(happy_eye)
                painter.restore()
            painter.restore()

        if confused_weight > 0.0:
            painter.save()
            painter.setOpacity(confused_weight)
            right_eye = eye_rects[1]
            confused_eye = QRectF(
                right_eye.left() - right_eye.width() * 0.1,
                right_eye.bottom() - right_eye.height() * 0.43,
                right_eye.width() * 1.2,
                right_eye.height() * 0.53,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(5, 4, 8))
            painter.drawEllipse(confused_eye)
            painter.restore()

        if wonder_weight > 0.0:
            for eye in eye_rects:
                size = min(44.0, self.height() - 6) if self._voice_mode else 19.0
                wonder_rect = QRectF(
                    eye.center().x() - size / 2,
                    eye.center().y() - size / 2,
                    size,
                    size,
                )
                wonder_path = QPainterPath()
                wonder_path.addEllipse(wonder_rect)
                gradient = QLinearGradient(
                    wonder_rect.topLeft(),
                    wonder_rect.bottomRight(),
                )
                gradient.setColorAt(0.0, QColor("#f0c5ff"))
                gradient.setColorAt(0.35, QColor("#ba43ff"))
                gradient.setColorAt(1.0, QColor("#6200a9"))
                painter.save()
                painter.setOpacity(wonder_weight)
                painter.fillPath(wonder_path, gradient)
                painter.setPen(QPen(QColor("#e9b5ff"), 1.2))
                painter.drawPath(wonder_path)
                painter.restore()

        if love_weight > 0.0 and not self._love_pixmap.isNull():
            painter.save()
            painter.setOpacity(love_weight)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if self._voice_mode:
                for eye in eye_rects:
                    painter.drawPixmap(
                        eye.toAlignedRect(),
                        self._love_pixmap,
                    )
            else:
                heart_size = min(30, self.width() - 6, self.height() - 4)
                heart_rect = QRectF(
                    (self.width() - heart_size) / 2,
                    (self.height() - heart_size) / 2,
                    heart_size,
                    heart_size,
                )
                painter.drawPixmap(
                    heart_rect.toAlignedRect(),
                    self._love_pixmap,
                )
            painter.restore()
        if (
            self._voice_mode
            and self._expression_weight("sleep") > 0.0
            and not self._sleep_z_pixmap.isNull()
        ):
            sleep_progress = self._expression_weight("sleep")
            z_sizes = ((8, 12), (13, 20), (19, 29))
            z_positions = ((136, 8), (120, 22), (102, 38))
            for index, ((z_width, z_height), (base_x, base_y)) in enumerate(
                zip(z_sizes, z_positions)
            ):
                phase = (self._sleep_animation_phase + index * 0.22) % 1.0
                z_image = self._sleep_z_pixmap.scaled(
                    QSize(z_width, z_height),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                z_y = round(base_y - phase * 7)
                painter.setOpacity(
                    sleep_progress * (1.0 - phase * 0.35)
                )
                painter.drawPixmap(base_x, z_y, z_image)
            painter.setOpacity(1.0)
        painter.end()

    def _expression_weight(self, expression: str) -> float:
        if self._expression == expression:
            return self._expression_progress
        if self._expression_from == expression:
            return 1.0 - self._expression_progress
        return 0.0

    def _eye_dimensions(self) -> tuple[float, float]:
        current = self._expression_metrics(self._expression_from)
        target = self._expression_metrics(self._expression)
        progress = self._expression_progress
        width = current[0] + (target[0] - current[0]) * progress
        height = current[1] + (target[1] - current[1]) * progress
        closed_width, closed_height = (
            (30.0, 12.0) if self._voice_mode else (9.5, 5.0)
        )
        width += (closed_width - width) * self._blink_progress
        height += (closed_height - height) * self._blink_progress
        return width, height

    def _expression_metrics(self, expression: str) -> tuple[float, float, float]:
        if expression == "sleep" and self._voice_mode:
            return 38.0, 9.0, 54.0
        if expression == "love":
            return (34.0, 34.0, 64.0) if self._voice_mode else (17.0, 17.0, 20.0)
        if expression == "wonder":
            return (38.0, 38.0, 58.0) if self._voice_mode else (19.0, 19.0, 15.0)
        return (
            20.0 if self._voice_mode else 6.5,
            38.0 if self._voice_mode else 16.0,
            64.0 if self._voice_mode else 15.0,
        )

    def _eye_centers(self) -> tuple[float, float]:
        from_separation = self._expression_metrics(self._expression_from)[2]
        to_separation = self._expression_metrics(self._expression)[2]
        separation = from_separation + (
            to_separation - from_separation
        ) * self._expression_progress
        center = self.width() / 2
        return center - separation / 2, center + separation / 2

    def _eye_rects(self) -> tuple[QRectF, QRectF]:
        width, height = self._eye_dimensions()
        left_center, right_center = self._eye_centers()
        max_move_x = 12.0 if self._voice_mode else 8.0
        gaze_x = 0.0 if self._expression == "sleep" else self._gaze_x
        gaze_y = 0.0 if self._expression == "sleep" else self._gaze_y
        horizontal_clearance = max(
            0.0,
            min(
                left_center - width / 2 - 1.0,
                self.width() - right_center - width / 2 - 1.0,
            ),
        )
        gaze_offset_x = round(gaze_x * min(max_move_x, horizontal_clearance))
        max_move_y = 8.0 if self._voice_mode else 6.0
        vertical_clearance = max(0.0, (self.height() - height) / 2 - 2.5)
        gaze_offset_y = round(gaze_y * min(max_move_y, vertical_clearance))
        center_y = round(self.height() / 2 + gaze_offset_y)
        return tuple(
            QRectF(
                round(center_x + gaze_offset_x - width / 2),
                round(center_y - height / 2),
                width,
                height,
            )
            for center_x in (left_center, right_center)
        )


class _MouseHookPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MouseHookData(ctypes.Structure):
    _fields_ = [
        ("point", _MouseHookPoint),
        ("mouse_data", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("extra_info", ctypes.c_size_t),
    ]


class _TaskNotificationCard(QWidget):
    dismissed = Signal()

    def __init__(
        self,
        task: dict,
        state: str,
        width: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        self.state = state
        self._scale = width / 580
        self.setFixedSize(width, round(194 * self._scale))
        self.setAccessibleName(
            f"{task['title']} task {state} notification"
        )
        self.setToolTip("Click to dismiss task notification")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismissed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self._scale, self._scale)

        card_rect = QRectF(2, 2, 576, 190)
        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 48, 48)
        painter.fillPath(card_path, QColor("#000000"))

        icon_rect = QRectF(28, 36, 76, 76)
        icon_path = QPainterPath()
        icon_path.addEllipse(icon_rect)
        icon_gradient = QLinearGradient(icon_rect.topLeft(), icon_rect.bottomLeft())
        icon_gradient.setColorAt(0, QColor("#d946ff"))
        icon_gradient.setColorAt(1, QColor("#7a12d2"))
        painter.fillPath(icon_path, icon_gradient)
        self._draw_task_icon(painter, icon_rect)

        title = self.task["title"].strip().title()
        title_is_time = title.casefold().endswith(" time")
        started = self.state == "started"
        heading = f"{title}{'!' if title_is_time else ' time!'}" if started else (
            title if title_is_time else f"{title} time"
        )
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 21, QFont.Weight.Bold))
        painter.drawText(QRectF(116, 35, 285, 34), Qt.AlignmentFlag.AlignVCenter, heading)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        subtitle = (
            f"Your scheduled {self.task.get('category') or 'task'} starts now."
            if started
            else f"The {heading.rstrip('!').lower()} task you set is over"
        )
        painter.drawText(
            QRectF(118, 72, 285, 36),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            subtitle,
        )

        if started:
            painter.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
            painter.drawText(
                QRectF(35, 142, 365, 32),
                Qt.AlignmentFlag.AlignVCenter,
                self._time_range(),
            )
        else:
            painter.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
            painter.drawText(
                QRectF(116, 112, 285, 38),
                Qt.AlignmentFlag.AlignCenter,
                "Task ended",
            )

        status_rect = QRectF(416, 36, 144, 38)
        status_path = QPainterPath()
        status_path.addRoundedRect(status_rect, 20, 20)
        status_gradient = QLinearGradient(status_rect.topLeft(), status_rect.bottomLeft())
        status_gradient.setColorAt(0, QColor("#d541ff"))
        status_gradient.setColorAt(1, QColor("#8613dd"))
        painter.fillPath(status_path, status_gradient)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(
            status_rect,
            Qt.AlignmentFlag.AlignCenter,
            "Started" if started else "Ended",
        )
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(416, 78, 144, 20),
            Qt.AlignmentFlag.AlignCenter,
            "FLEX Task Manager",
        )
        painter.end()

    def _time_range(self) -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        timezone = ZoneInfo(self.task["timezone"])
        start = datetime.fromisoformat(self.task["start"]).astimezone(timezone)
        start_text = start.strftime("%I:%M %p").lstrip("0")
        if self.task.get("end") is None:
            return start_text
        end = datetime.fromisoformat(self.task["end"]).astimezone(timezone)
        end_text = end.strftime("%I:%M %p").lstrip("0")
        return f"{start_text} – {end_text}"

    def _draw_task_icon(self, painter: QPainter, rect: QRectF) -> None:
        category = str(self.task.get("category") or "").casefold()
        painter.save()
        painter.setPen(
            QPen(
                QColor("#ffffff"),
                2.4,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        if any(word in category for word in ("study", "read", "book", "learn")):
            icon = QPainterPath()
            icon.moveTo(rect.left() + 18, rect.top() + 22)
            icon.quadTo(rect.left() + 28, rect.top() + 17, rect.center().x(), rect.top() + 24)
            icon.lineTo(rect.center().x(), rect.bottom() - 20)
            icon.quadTo(rect.left() + 28, rect.bottom() - 25, rect.left() + 18, rect.bottom() - 20)
            icon.closeSubpath()
            icon.moveTo(rect.center().x(), rect.top() + 24)
            icon.quadTo(rect.right() - 28, rect.top() + 17, rect.right() - 18, rect.top() + 22)
            icon.lineTo(rect.right() - 18, rect.bottom() - 20)
            icon.quadTo(rect.right() - 28, rect.bottom() - 25, rect.center().x(), rect.bottom() - 20)
            icon.closeSubpath()
            painter.drawPath(icon)
        elif any(word in category for word in ("exercise", "workout", "run", "fitness")):
            painter.drawLine(rect.left() + 18, rect.center().y(), rect.right() - 18, rect.center().y())
            painter.drawLine(rect.left() + 22, rect.top() + 27, rect.left() + 22, rect.bottom() - 27)
            painter.drawLine(rect.left() + 16, rect.top() + 32, rect.left() + 16, rect.bottom() - 32)
            painter.drawLine(rect.right() - 22, rect.top() + 27, rect.right() - 22, rect.bottom() - 27)
            painter.drawLine(rect.right() - 16, rect.top() + 32, rect.right() - 16, rect.bottom() - 32)
        elif any(word in category for word in ("program", "coding", "code", "develop")):
            path = QPainterPath()
            path.moveTo(rect.left() + 30, rect.top() + 24)
            path.lineTo(rect.left() + 20, rect.center().y())
            path.lineTo(rect.left() + 30, rect.bottom() - 24)
            path.moveTo(rect.right() - 30, rect.top() + 24)
            path.lineTo(rect.right() - 20, rect.center().y())
            path.lineTo(rect.right() - 30, rect.bottom() - 24)
            painter.drawPath(path)
            painter.drawLine(rect.center().x() + 8, rect.top() + 22, rect.center().x() - 8, rect.bottom() - 22)
        else:
            painter.drawEllipse(QRectF(rect.left() + 24, rect.top() + 24, 28, 28))
            painter.drawLine(rect.center().x(), rect.top() + 31, rect.center().x(), rect.center().y() + 1)
            painter.drawLine(rect.center().x(), rect.bottom() - 25, rect.center().x(), rect.bottom() - 24)
        painter.restore()


class FlexEdgeOverlay(QWidget):
    """Compact edge-triggered FLEX overlay that stays fixed near a screen edge."""

    send_requested = Signal(str)
    voice_input_toggled = Signal(bool)
    wake_requested = Signal()
    _global_click = Signal(int, int)
    _mouse_down_messages = {0x0201, 0x0204, 0x0207, 0x020B}

    def __init__(self, settings) -> None:
        super().__init__()
        self.settings = settings
        self._show_delay = QTimer(self)
        self._show_delay.setSingleShot(True)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(16)
        self._visible = False
        self._eye_mode = False
        self._line_thickness = max(2, self.settings.edge_line_thickness)
        self._line_bar_overlap = 0
        self._screen_edge_gap = 2
        self._shadow_padding = 24
        self._bar_animation: QParallelAnimationGroup | None = None
        self._line_animation: QPropertyAnimation | None = None
        self._bar_hidden_position = QPoint()
        self._mouse_hook = None
        self._mouse_hook_callback = None
        self._user32 = None
        self._task_notification_queue: list[tuple[dict, str]] = []
        self._task_notification_card: _TaskNotificationCard | None = None
        self._task_notification_animation: QPropertyAnimation | None = None
        self._task_notification_duration_ms = 7_000
        self._task_notification_auto_dismiss = QTimer(self)
        self._task_notification_auto_dismiss.setSingleShot(True)
        self._task_notification_auto_dismiss.timeout.connect(
            self._dismiss_task_notification
        )

        self._build_ui()
        self._configure_window()
        self._position_overlay()
        self._edge_line_resting_geometry = self.edge_line.geometry()
        self._show_delay.timeout.connect(self._do_show)
        self._poll_timer.timeout.connect(self._check_pointer_state)
        self._global_click.connect(
            self._handle_global_click,
            Qt.ConnectionType.QueuedConnection,
        )
        self._poll_timer.start()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._install_global_mouse_hook()
        self.show()
        self._bar_resting_position = self._shadow_host.pos()
        self._bar_hidden_position = self._offscreen_position()
        self._set_idle_mask()
        if app is not None:
            app.aboutToQuit.connect(self._remove_global_mouse_hook)

    def _build_ui(self) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        container = QWidget(self)
        container.setObjectName("edgeOverlayContainer")
        overlay_stylesheet = """
            QWidget#edgeOverlayContainer {
                background: transparent;
                border: none;
            }
            QWidget#edgeOverlayBar {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(52, 51, 60, 255),
                    stop: 0.1 rgba(22, 21, 26, 255),
                    stop: 0.5 rgba(4, 4, 6, 255),
                    stop: 0.88 rgba(8, 8, 12, 255),
                    stop: 1 rgba(28, 27, 34, 255)
                );
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 36px;
            }
            QLineEdit#overlayInput {
                border: 1px solid rgba(255, 255, 255, 0.13);
                border-radius: 18px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(28, 27, 34, 255),
                    stop: 0.25 rgba(14, 13, 17, 255),
                    stop: 1 rgba(7, 7, 9, 255)
                );
                color: white;
                font-size: 11pt;
                min-height: 36px;
                max-height: 36px;
                padding: 0 14px;
            }
            QLineEdit#overlayInput:focus {
                border: 1px solid rgba(186, 67, 255, 0.5);
            }
            QPushButton#overlayAction {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(46, 45, 54, 255),
                    stop: 0.4 rgba(25, 24, 30, 255),
                    stop: 1 rgba(14, 13, 17, 255)
                );
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 17px;
                color: #edf6ff;
                min-width: 34px;
                max-width: 34px;
                min-height: 34px;
                max-height: 34px;
            }
            QPushButton#overlayAction:hover {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(62, 60, 72, 255),
                    stop: 0.4 rgba(35, 33, 42, 255),
                    stop: 1 rgba(22, 20, 27, 255)
                );
                border: 1px solid rgba(255, 255, 255, 0.28);
            }
            QPushButton#overlayAction:checked {
                border: 1.5px solid #ba43ff;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(80, 25, 115, 255),
                    stop: 1 rgba(35, 10, 55, 255)
                );
            }
            QPushButton#overlaySend {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(46, 45, 54, 255),
                    stop: 0.4 rgba(25, 24, 30, 255),
                    stop: 1 rgba(14, 13, 17, 255)
                );
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 17px;
                color: #edf6ff;
                min-width: 34px;
                max-width: 34px;
                min-height: 34px;
                max-height: 34px;
            }
            QPushButton#overlaySend:hover {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 rgba(62, 60, 72, 255),
                    stop: 0.4 rgba(35, 33, 42, 255),
                    stop: 1 rgba(22, 20, 27, 255)
                );
                border: 1px solid rgba(255, 255, 255, 0.28);
            }
            """
        overlay_stylesheet = overlay_stylesheet.replace(
            "border-radius: 36px;",
            f"border-radius: {self.settings.bar_height // 2}px;",
        )
        container.setStyleSheet(overlay_stylesheet)

        self.edge_line = QWidget(container)
        line_color = QColor(self.settings.edge_line_color)
        if not line_color.isValid():
            line_color = QColor("#a6a6a6")
        line_color.setAlphaF(self.settings.edge_line_opacity)
        self.edge_line.setStyleSheet(
            "background: "
            f"rgba({line_color.red()}, {line_color.green()}, {line_color.blue()}, "
            f"{line_color.alpha()}); border: none; border-radius: "
            f"{self._line_thickness // 2}px;"
        )
        line_shadow = QGraphicsDropShadowEffect(self.edge_line)
        line_shadow.setBlurRadius(5)
        line_shadow.setColor(QColor(0, 0, 0, 180))
        line_shadow.setOffset(0, 2)
        self.edge_line.setGraphicsEffect(line_shadow)
        bar = QWidget(container)
        bar.setObjectName("edgeOverlayBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 0, 12, 0)
        bar_layout.setSpacing(8)

        self._eyes = _NotchEyes(bar)
        self._eyes.setAccessibleName("FLEX voice chat indicator")
        bar_layout.addWidget(self._eyes)

        self.input_box = QLineEdit()
        self.input_box.setObjectName("overlayInput")
        self.input_box.setPlaceholderText("Type or speak...")
        self.input_box.returnPressed.connect(self._emit_send)
        bar_layout.addWidget(self.input_box, 1)

        self.voice_button = QPushButton()
        self.voice_button.setObjectName("overlayAction")
        self.voice_button.setCheckable(True)
        self.voice_button.setToolTip("Voice input")
        self.voice_button.setAccessibleName("Voice input")
        self.voice_button.setIcon(self._make_button_icon("microphone"))
        self.voice_button.setIconSize(QSize(18, 18))
        self.voice_button.clicked.connect(self._toggle_voice_placeholder)
        bar_layout.addWidget(self.voice_button)

        self.send_button = QPushButton()
        self.send_button.setObjectName("overlaySend")
        self.send_button.setToolTip("Send message")
        self.send_button.setAccessibleName("Send message")
        self.send_button.setIcon(self._make_button_icon("send"))
        self.send_button.setIconSize(QSize(18, 18))
        self.send_button.clicked.connect(self._emit_send)
        bar_layout.addWidget(self.send_button)

        shadow_host = QWidget(container)
        shadow_layout = QVBoxLayout(shadow_host)
        shadow_layout.setContentsMargins(0, 0, 0, 0)
        shadow_layout.addWidget(bar)
        bar_shadow = QGraphicsDropShadowEffect(shadow_host)
        bar_shadow.setBlurRadius(10)
        bar_shadow.setColor(QColor(0, 0, 0, 120))
        bar_shadow.setOffset(0, 2)
        shadow_host.setGraphicsEffect(bar_shadow)
        self._shadow_host = shadow_host
        self._bar = bar
        self._bar_layout = bar_layout
        self._bar_shadow = bar_shadow
        self._bar_opacity = QGraphicsOpacityEffect(bar)
        self._bar_opacity.setOpacity(0.0)
        bar.setGraphicsEffect(self._bar_opacity)
        shadow_host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.resize(self.settings.bar_width, self.settings.bar_height)
        content_height = (
            self.settings.bar_height
            + self._line_thickness
            - self._line_bar_overlap
        )
        self.setFixedSize(
            self.settings.bar_width + 2 * self._shadow_padding,
            content_height + 2 * self._shadow_padding,
        )
        self._container = container
        self._container.setGeometry(
            0,
            0,
            self.width(),
            self.height(),
        )

    def _configure_window(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _position_overlay(self) -> None:
        primary_screen = QApplication.primaryScreen()
        if primary_screen is None:
            return
        screen_geometry = primary_screen.geometry()
        content_width = self.settings.bar_width
        content_height = (
            self.settings.bar_height
            + self._line_thickness
            - self._line_bar_overlap
        )
        padding = self._shadow_padding
        width = content_width + 2 * padding
        height = content_height + 2 * padding
        x = screen_geometry.left() + max(
            0,
            (screen_geometry.width() - width) // 2,
        )
        y = screen_geometry.top() - padding
        if self.settings.edge_position == "top":
            y += self._screen_edge_gap
        if self.settings.edge_position == "bottom":
            y = screen_geometry.bottom() - content_height + 1 - padding
        elif self.settings.edge_position == "left":
            x = screen_geometry.left() - padding
            y = screen_geometry.top() + max(
                0,
                (screen_geometry.height() - content_height) // 2 - padding,
            )
        elif self.settings.edge_position == "right":
            x = screen_geometry.right() - content_width + 1 - padding
            y = screen_geometry.top() + max(
                0,
                (screen_geometry.height() - content_height) // 2 - padding,
            )
        self.setGeometry(x, y, width, height)
        self._container.setGeometry(0, 0, width, height)
        self._edge_line_host_position()

    def _edge_line_host_position(self) -> None:
        padding = self._shadow_padding
        line_width = min(
            max(1, self.settings.edge_line_length * 2 // 3),
            max(1, self.width() - 2 * padding),
        )
        self._shadow_host.setGeometry(
            padding,
            padding + self._line_thickness - self._line_bar_overlap,
            self.settings.bar_width,
            self.settings.bar_height,
        )
        shadow_layout = self._shadow_host.layout()
        if shadow_layout is not None:
            shadow_layout.activate()
        self.edge_line.setGeometry(
            self._edge_line_x_for_bar_center(line_width),
            padding,
            line_width,
            self._line_thickness,
        )
        self._apply_bar_mask()

    def _apply_bar_mask(self) -> None:
        bar_path = QPainterPath()
        bar_path.addRoundedRect(
            QRectF(self._bar.rect()),
            self._bar.height() / 2,
            self._bar.height() / 2,
        )
        self._bar.setMask(QRegion(bar_path.toFillPolygon().toPolygon()))

    def _edge_line_x_for_bar_center(self, line_width: int) -> int:
        bar_center_x = self._bar.mapTo(
            self._container,
            self._bar.rect().center(),
        ).x()
        return bar_center_x - (line_width - 1) // 2

    def _align_edge_line_to_bar(self) -> None:
        geometry = self.edge_line.geometry()
        self.edge_line.move(
            self._edge_line_x_for_bar_center(geometry.width()),
            geometry.y(),
        )

    @staticmethod
    def _make_button_icon(kind: str) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if kind == "microphone":
            painter.setPen(
                QPen(
                    QColor("#edf6ff"),
                    2.0,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            path = QPainterPath()
            path.addRoundedRect(QRectF(12, 5, 8, 14), 4, 4)
            painter.drawPath(path)
            arc = QPainterPath()
            arc.moveTo(8, 13)
            arc.cubicTo(8, 20.5, 24, 20.5, 24, 13)
            painter.drawPath(arc)
            painter.drawLine(16, 21, 16, 26)
            painter.drawLine(12, 26, 20, 26)
        else:
            plane = QPainterPath()
            plane.moveTo(7, 16)
            plane.lineTo(25, 7)
            plane.lineTo(17, 25)
            plane.lineTo(13.5, 17.5)
            plane.closeSubpath()
            painter.fillPath(plane, QColor("#edf6ff"))
            painter.setPen(
                QPen(
                    QColor(32, 30, 38, 200),
                    1.4,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawLine(13.5, 17.5, 25, 7)
        painter.end()
        return QIcon(pixmap)

    def _line_hit_region(self) -> QRegion:
        line_origin = self.edge_line.mapTo(self, QPoint(0, 0))
        padding = self._shadow_padding
        return QRegion(
            QRect(
                line_origin.x() - padding,
                line_origin.y() - padding,
                self.edge_line.width() + 2 * padding,
                self._line_thickness + 2 * padding,
            )
        )

    def _trigger_rect(self) -> QRect:
        line_origin = self.edge_line.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(line_origin) or QApplication.primaryScreen()
        if screen is not None and self.settings.edge_position == "top":
            screen_top = screen.geometry().top()
            return QRect(
                line_origin.x(),
                screen_top,
                self.edge_line.width(),
                line_origin.y() + self.edge_line.height() - screen_top,
            )
        return QRect(line_origin, self.edge_line.size())

    def _set_idle_mask(self) -> None:
        self.setMask(self._line_hit_region())

    def _set_interactive_mask(self) -> None:
        bar_origin = self._shadow_host.mapTo(self, QPoint(0, 0))
        padding = self._shadow_padding
        self.setMask(
            self._line_hit_region()
            | QRegion(
                QRect(
                    bar_origin.x() - padding,
                    bar_origin.y() - padding,
                    self._shadow_host.width() + 2 * padding,
                    self._shadow_host.height() + 2 * padding,
                )
            )
        )

    def _offscreen_position(self) -> QPoint:
        screen = QApplication.primaryScreen()
        if screen is None:
            return self._bar_resting_position
        screen_geometry = screen.geometry()
        window_origin = self.mapToGlobal(QPoint(0, 0))
        if self.settings.edge_position == "bottom":
            y = screen_geometry.bottom() + 1 - window_origin.y()
            return QPoint(self._bar_resting_position.x(), y)
        if self.settings.edge_position == "left":
            x = screen_geometry.left() - self._shadow_host.width() - window_origin.x()
            return QPoint(x, self._bar_resting_position.y())
        if self.settings.edge_position == "right":
            x = screen_geometry.right() + 1 - window_origin.x()
            return QPoint(x, self._bar_resting_position.y())
        y = screen_geometry.top() - self._shadow_host.height() - window_origin.y()
        return QPoint(self._bar_resting_position.x(), y)

    def _animate_bar(self, visible: bool) -> None:
        animation_was_running = (
            self._bar_animation is not None
            and self._bar_animation.state() == QAbstractAnimation.State.Running
        )
        if self._bar_animation is not None:
            if animation_was_running:
                self._bar_animation.stop()
            self._bar_animation.deleteLater()
        if visible and not animation_was_running:
            self._shadow_host.move(self._bar_hidden_position)
            self._bar_opacity.setOpacity(0.0)

        self._bar_animation = QParallelAnimationGroup(self)
        self._bar_animation.finished.connect(self._finish_bar_animation)
        duration = max(1, self.settings.animation_duration_ms)
        position_animation = QPropertyAnimation(
            self._shadow_host,
            b"pos",
            self._bar_animation,
        )
        position_animation.setDuration(duration)
        position_animation.setStartValue(self._shadow_host.pos())
        position_animation.setEndValue(
            self._bar_resting_position if visible else self._bar_hidden_position
        )
        position_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic if visible else QEasingCurve.Type.InCubic
        )
        position_animation.valueChanged.connect(
            lambda _position: self._set_interactive_mask()
        )
        self._bar_animation.addAnimation(position_animation)

        if visible:
            self._bar_opacity.setOpacity(1.0)
        self._set_interactive_mask()
        self._bar_animation.start()

    def _animate_edge_line_jump(self) -> None:
        if self._line_animation is not None:
            self._line_animation.stop()
            self._line_animation.deleteLater()

        self.edge_line.setGeometry(self._edge_line_resting_geometry)
        if self.settings.edge_position == "bottom":
            offset = QPoint(0, -4)
        elif self.settings.edge_position == "left":
            offset = QPoint(4, 0)
        elif self.settings.edge_position == "right":
            offset = QPoint(-4, 0)
        else:
            offset = QPoint(0, 4)

        resting_geometry = self._edge_line_resting_geometry
        expansion = max(4, resting_geometry.width() // 12)
        enlarged_geometry = QRect(
            resting_geometry.x() - expansion // 2,
            resting_geometry.y() + 2,
            resting_geometry.width() + expansion,
            resting_geometry.height() + 2,
        )
        animation = QPropertyAnimation(self.edge_line, b"geometry", self)
        animation.setDuration(140)
        animation.setStartValue(resting_geometry)
        animation.setKeyValueAt(
            0.45,
            QRect(
                enlarged_geometry.x() + offset.x(),
                enlarged_geometry.y() + offset.y(),
                enlarged_geometry.width(),
                enlarged_geometry.height(),
            ),
        )
        animation.setEndValue(resting_geometry)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(
            lambda: self.edge_line.setGeometry(resting_geometry)
        )
        self._line_animation = animation
        animation.start()

    def _finish_bar_animation(self) -> None:
        if self._visible:
            self._shadow_host.move(self._bar_resting_position)
            self._bar_opacity.setOpacity(1.0)
            self._set_interactive_mask()
        else:
            self._shadow_host.move(self._bar_hidden_position)
            self._bar_opacity.setOpacity(0.0)
            self._set_idle_mask()

    def _install_global_mouse_hook(self) -> None:
        if sys.platform != "win32":
            return

        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_hook = user32.SetWindowsHookExW
        set_hook.argtypes = (
            ctypes.c_int,
            callback_type,
            ctypes.c_void_p,
            ctypes.c_ulong,
        )
        set_hook.restype = ctypes.c_void_p
        get_module_handle = kernel32.GetModuleHandleW
        get_module_handle.argtypes = (ctypes.c_wchar_p,)
        get_module_handle.restype = ctypes.c_void_p
        self._user32 = user32
        self._user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        self._user32.CallNextHookEx.restype = ctypes.c_ssize_t

        def mouse_hook_callback(code: int, message: int, data_pointer: int) -> int:
            if code >= 0 and message in self._mouse_down_messages:
                mouse_data = ctypes.cast(
                    data_pointer,
                    ctypes.POINTER(_MouseHookData),
                ).contents
                self._global_click.emit(
                    int(mouse_data.point.x),
                    int(mouse_data.point.y),
                )
            return self._user32.CallNextHookEx(
                self._mouse_hook,
                code,
                message,
                data_pointer,
            )

        self._mouse_hook_callback = callback_type(mouse_hook_callback)
        module_handle = get_module_handle(None)
        self._mouse_hook = set_hook(
            14,
            self._mouse_hook_callback,
            module_handle,
            0,
        )
        if not self._mouse_hook:
            logger.warning(
                "Could not install the global click-away hook (Windows error %s).",
                ctypes.get_last_error(),
            )

    def _handle_global_click(self, x: int, y: int) -> None:
        if not self._visible:
            return
        if self._eye_mode:
            return
        clicked_widget = QApplication.widgetAt(QPoint(x, y))
        if (
            isinstance(clicked_widget, QWidget)
            and (clicked_widget is self or self.isAncestorOf(clicked_widget))
        ):
            self._focus_input()
            return
        bar_origin = self._bar.mapToGlobal(QPoint(0, 0))
        bar_rect = QRect(bar_origin, self._bar.size())
        if bar_rect.contains(QCursor.pos()) or bar_rect.contains(x, y):
            self._focus_input()
            return
        self._do_hide()

    def _remove_global_mouse_hook(self) -> None:
        if self._mouse_hook and self._user32 is not None:
            if not self._user32.UnhookWindowsHookEx(self._mouse_hook):
                logger.warning(
                    "Could not remove the global click-away hook (Windows error %s).",
                    ctypes.get_last_error(),
                )
            self._mouse_hook = None
        self._mouse_hook_callback = None

    def activate_overlay_mode(self) -> None:
        self.show()
        self.raise_()
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        self._check_pointer_state()

    def _check_pointer_state(self) -> None:
        position = QCursor.pos()
        self._eyes.set_gaze_position(position)
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        if screen is None:
            return
        triggered = self._trigger_rect().contains(position)

        if triggered and not self._visible and not self._show_delay.isActive():
            self._show_delay.start(self.settings.hover_delay_ms)
        elif not triggered:
            self._show_delay.stop()

    def eventFilter(self, watched, event) -> bool:
        if self._visible and event.type() == QEvent.Type.MouseButtonPress:
            if isinstance(watched, QWidget) and (
                watched is self or self.isAncestorOf(watched)
            ):
                if self._eye_mode and self._eyes._expression == "sleep":
                    self.wake_requested.emit()
                    return True
                if (
                    self._eye_mode
                    and watched is not self._eyes
                ):
                    self._set_eye_mode(False)
                    self.voice_input_toggled.emit(False)
                else:
                    self._focus_input()
                return super().eventFilter(watched, event)
            global_position = event.globalPosition().toPoint()
            self._handle_global_click(global_position.x(), global_position.y())
        return super().eventFilter(watched, event)

    def _toggle_voice_placeholder(self) -> None:
        enabled = self.voice_button.isChecked()
        self._set_eye_mode(enabled)
        self.voice_input_toggled.emit(enabled)

    def set_voice_mode(self, enabled: bool) -> None:
        self.voice_button.setChecked(enabled)
        self._set_eye_mode(enabled)

    def set_expression(self, expression: str) -> None:
        self._eyes.set_expression(expression)

    def _set_eye_mode(self, enabled: bool) -> None:
        self._eye_mode = enabled
        self.voice_button.setChecked(enabled)
        if not enabled:
            self._eyes.set_expression("neutral")
        if enabled:
            self.input_box.setPlaceholderText("Listening...")
        else:
            self.input_box.setPlaceholderText("Type or speak...")
        self._eyes.set_voice_mode(enabled)
        self.input_box.setVisible(not enabled)
        self.send_button.setVisible(not enabled)
        self.voice_button.setVisible(not enabled)
        if enabled:
            self._bar_layout.setAlignment(
                self._eyes,
                Qt.AlignmentFlag.AlignCenter,
            )
        else:
            self._bar_layout.setAlignment(
                self._eyes,
                Qt.AlignmentFlag.AlignVCenter,
            )
        self._bar_layout.activate()
        if self._visible:
            self._eyes.show()
        self._align_edge_line_to_bar()
        self._bar.update()
        self._eyes.update()
        if not enabled and self._visible:
            self._focus_input()

    def _emit_send(self) -> None:
        text = self.input_box.text().strip()
        if not text:
            return
        self.send_requested.emit(text)
        self.input_box.clear()
        self.input_box.setFocus()

    def _do_show(self) -> None:
        if (
            self._task_notification_card is not None
            or self._visible
            or not self._trigger_rect().contains(QCursor.pos())
        ):
            return
        self._visible = True
        self.raise_()
        self.show()
        self.activateWindow()
        self._shadow_host.raise_()
        self._eyes.raise_()
        self._shadow_host.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            False,
        )
        self._animate_bar(True)
        self._animate_edge_line_jump()
        self._focus_input()

    def _focus_input(self) -> None:
        if not self._visible:
            return
        self.raise_()
        self.activateWindow()
        if not self._eye_mode:
            self.input_box.setFocus()

    def _do_hide(self) -> None:
        if not self._visible or self._eye_mode:
            return
        self._visible = False
        self.input_box.clearFocus()
        self._shadow_host.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._animate_bar(False)

    def enqueue_task_notification(
        self,
        task: dict,
        state: str,
        duration_ms: int = 7_000,
    ) -> bool:
        if state not in {"started", "ended"}:
            raise ValueError(f"Unknown task notification state: {state}")
        if QApplication.primaryScreen() is None:
            logger.error("Could not enqueue task notification: no primary screen.")
            return False
        self._task_notification_duration_ms = max(1_000, duration_ms)
        if self._task_notification_card is not None:
            self._task_notification_queue.append((task.copy(), state))
            return True
        self._task_notification_queue.append((task.copy(), state))
        self._show_next_task_notification()
        return True

    def _show_next_task_notification(self) -> None:
        if not self._task_notification_queue:
            self._restore_after_task_notification()
            return
        task, state = self._task_notification_queue.pop(0)
        screen = QApplication.primaryScreen()
        if screen is None:
            logger.error("Could not display task notification: no primary screen.")
            return
        screen_rect = screen.geometry()
        width = min(self.settings.bar_width, screen_rect.width())
        padding = 12
        card = _TaskNotificationCard(task, state, width, self._container)
        height = card.height()
        self.setFixedSize(width + 2 * padding, height + 2 * padding)
        x = screen_rect.left() + (screen_rect.width() - width) // 2 - padding
        y = screen_rect.top()
        if self.settings.edge_position == "bottom":
            y = screen_rect.bottom() - height + 1 - padding
        elif self.settings.edge_position == "left":
            x = screen_rect.left() - padding
            y = screen_rect.top() + (screen_rect.height() - height) // 2 - padding
        elif self.settings.edge_position == "right":
            x = screen_rect.right() - width + 1 - padding
            y = screen_rect.top() + (screen_rect.height() - height) // 2 - padding
        self.setGeometry(x, y, width + 2 * padding, height + 2 * padding)
        self._container.setGeometry(0, 0, self.width(), self.height())
        self._edge_line_host_position()
        self.edge_line.hide()
        self._shadow_host.hide()
        card.dismissed.connect(self._dismiss_task_notification)
        self._task_notification_card = card
        target_x = (self.width() - card.width()) // 2
        target_y = (self.height() - card.height()) // 2
        card.setGeometry(target_x, target_y, card.width(), card.height())
        opacity = QGraphicsOpacityEffect(card)
        opacity.setOpacity(0.0)
        card.setGraphicsEffect(opacity)
        self.setMask(
            QRegion(
                QRect(
                    target_x,
                    target_y,
                    card.width(),
                    card.height(),
                )
            )
        )
        self.show()
        self.raise_()
        card.show()
        if self.settings.edge_position == "bottom":
            start_x, start_y = target_x, target_y + card.height()
        elif self.settings.edge_position == "left":
            start_x, start_y = target_x - card.width(), target_y
        elif self.settings.edge_position == "right":
            start_x, start_y = target_x + card.width(), target_y
        else:
            start_x, start_y = target_x, target_y - card.height()
        animation = QPropertyAnimation(card, b"geometry", self)
        animation.setDuration(max(180, self.settings.animation_duration_ms))
        animation.setStartValue(QRect(start_x, start_y, card.width(), card.height()))
        animation.setEndValue(QRect(target_x, target_y, card.width(), card.height()))
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(
            lambda current=animation: self._finish_task_notification_entrance(
                current
            )
        )
        animation.start()
        fade = QPropertyAnimation(opacity, b"opacity", self)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.finished.connect(fade.deleteLater)
        fade.start()
        self._task_notification_animation = animation
        self._task_notification_auto_dismiss.start(
            self._task_notification_duration_ms
        )

    def _dismiss_task_notification(self) -> None:
        card = self._task_notification_card
        if card is None:
            return
        self._task_notification_auto_dismiss.stop()
        if self._task_notification_animation is not None:
            self._task_notification_animation.stop()
            self._task_notification_animation.deleteLater()
            self._task_notification_animation = None
        opacity = card.graphicsEffect()
        if isinstance(opacity, QGraphicsOpacityEffect):
            fade = QPropertyAnimation(opacity, b"opacity", self)
            fade.setDuration(160)
            fade.setStartValue(opacity.opacity())
            fade.setEndValue(0.0)
            fade.finished.connect(self._finish_task_notification)
            fade.finished.connect(fade.deleteLater)
            fade.start()
            return
        self._finish_task_notification()

    def _finish_task_notification_entrance(
        self,
        animation: QPropertyAnimation,
    ) -> None:
        if self._task_notification_animation is animation:
            self._task_notification_animation = None
        animation.deleteLater()

    def _finish_task_notification(self) -> None:
        if self._task_notification_card is not None:
            self._task_notification_card.hide()
            self._task_notification_card.deleteLater()
            self._task_notification_card = None
        self._task_notification_animation = None
        if self._task_notification_queue:
            self._show_next_task_notification()
        else:
            self._restore_after_task_notification()

    def _restore_after_task_notification(self) -> None:
        content_height = (
            self.settings.bar_height
            + self._line_thickness
            - self._line_bar_overlap
        )
        self.setFixedSize(
            self.settings.bar_width + 2 * self._shadow_padding,
            content_height + 2 * self._shadow_padding,
        )
        self._position_overlay()
        self.clearMask()
        self.edge_line.show()
        self._shadow_host.show()
        if self._visible:
            self._set_interactive_mask()
        else:
            self._set_idle_mask()

    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        self._show_delay.stop()
        self._task_notification_auto_dismiss.stop()
        if self._bar_animation is not None:
            self._bar_animation.stop()
        if self._line_animation is not None:
            self._line_animation.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._remove_global_mouse_hook()
        super().closeEvent(event)
