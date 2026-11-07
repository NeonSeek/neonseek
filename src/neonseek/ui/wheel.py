"""the mouse wheel scrolls, it never picks a value (owner decision, D-124).

over a list box, a number box or tabs the wheel would change the choice while the user only
wants to scroll the page. one filter on the whole app passes the wheel to the scrolling area
around the box instead.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QTabBar,
    QWidget,
)

GUARDED = (QComboBox, QAbstractSpinBox, QTabBar)


def _guarded(widget: QWidget) -> QWidget | None:
    # the text field inside a number box gets the wheel too
    for candidate in (widget, widget.parentWidget()):
        if isinstance(candidate, GUARDED):
            return candidate
    return None


def _scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


class WheelGuard(QObject):
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - qt name
        if event.type() != QEvent.Type.Wheel or not isinstance(obj, QWidget):
            return False
        box = _guarded(obj)
        if box is None:
            return False
        area = _scroll_area(box)
        if area is not None:
            QApplication.sendEvent(area.viewport(), event)
        return True


def install(app: QApplication) -> WheelGuard:
    """once per app, the app keeps the filter alive."""
    guard = getattr(app, "_neonseek_wheel_guard", None)
    if guard is None:
        guard = WheelGuard(app)
        app.installEventFilter(guard)
        app._neonseek_wheel_guard = guard
    return guard
