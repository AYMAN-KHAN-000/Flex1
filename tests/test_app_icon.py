import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from flex.ui.app_icon import _ICON_PATH, load_application_icon


def test_flex_application_icon_loads():
    app = QApplication.instance() or QApplication([])
    icon = load_application_icon()

    assert not icon.isNull()
    assert icon.availableSizes()


def test_flex_application_icon_has_rounded_transparent_corners():
    image = QImage(str(_ICON_PATH))

    assert not image.isNull()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(image.width() // 2, 0).alpha() == 255
