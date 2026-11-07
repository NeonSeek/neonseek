"""applies a theme: style sheet from the template, fonts, icons colored with theme tokens."""

from __future__ import annotations

import ctypes
import sys
import tempfile
from importlib import resources
from pathlib import Path
from string import Template

from PySide6.QtCore import (
    QByteArray,
    QCoreApplication,
    QLibraryInfo,
    QObject,
    QRectF,
    Qt,
    QTranslator,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QProxyStyle,
    QStyle,
    QWidget,
)

from neonseek import config, i18n, themes

FONT_FAMILY = "JetBrains Mono"
# pixel sizes at 100 %, everything else follows the text size setting (D-115)
FONT_PX = 12
FONT_PX_LARGE = 17
GLOW_BLUR = 18
# the template uses size tokens up to this many pixels
SIZE_TOKENS = 48

_PACKAGE = resources.files("neonseek.ui.theme")


def template_text() -> str:
    return _PACKAGE.joinpath("style.qss.tmpl").read_text(encoding="utf-8")


def placeholder(token: str) -> str:
    # bg.base becomes ${bg_base}
    return token.replace(".", "_")


def size_tokens(scale: float) -> dict[str, str]:
    """size_12 is 12 px at 100 %, 18 px at 150 %."""
    return {f"size_{n}": f"{round(n * scale)}px" for n in range(SIZE_TOKENS + 1)}


def scale_factor(percent: object) -> float:
    return config.ui_scale(percent) / 100


def render_qss(theme: themes.Theme, extra: dict[str, str], template: str | None = None) -> str:
    """the style sheet of a theme. extra carries fonts and icon paths, colors come only from
    the theme, so a placeholder the theme does not fill fails loudly."""
    values = {placeholder(token): theme.colors[token] for token in themes.TOKENS}
    values.update(extra)
    return Template(template or template_text()).substitute(values)


def svg_text(name: str) -> str:
    return _PACKAGE.joinpath("icons", f"{name}.svg").read_text(encoding="utf-8")


def colored_svg(name: str, color: str) -> bytes:
    return svg_text(name).replace("currentColor", color).encode("utf-8")


# the app logo keeps its own colors in every theme. below this size the simplified logo
# (no grooves, thicker lines) is used, in the window and in the exe icon (scripts/build_icon.py)
LOGO_FULL_FROM = 48
LOGO_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)


def logo_svg(size: int) -> bytes:
    name = "neonseek.svg" if size >= LOGO_FULL_FROM else "neonseek-small.svg"
    return _PACKAGE.joinpath("logo", name).read_bytes()


def logo_image(size: int) -> QImage:
    """the logo as a size x size image, the version chosen by the size."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(logo_svg(size))).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def logo_pixmap(size: int, dpr: float = 1.0) -> QPixmap:
    pixmap = QPixmap.fromImage(logo_image(max(1, round(size * dpr))))
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def app_icon() -> QIcon:
    """the window and taskbar icon, the same sizes as the exe icon."""
    icon = QIcon()
    for size in LOGO_SIZES:
        icon.addPixmap(QPixmap.fromImage(logo_image(size)))
    return icon


_FAMILIES: list[str] = []

# margins and spacing of layouts that set none, in pixels at 100 % (D-194): message boxes,
# the frame of the wizard and small parts. a window gets more room than a part inside it
WINDOW_MARGIN = 20
PART_MARGIN = 10
SPACING_ACROSS = 8
SPACING_DOWN = 10
_MARGINS = (
    QStyle.PixelMetric.PM_LayoutLeftMargin,
    QStyle.PixelMetric.PM_LayoutTopMargin,
    QStyle.PixelMetric.PM_LayoutRightMargin,
    QStyle.PixelMetric.PM_LayoutBottomMargin,
)
_SPACING = {
    QStyle.PixelMetric.PM_LayoutHorizontalSpacing: SPACING_ACROSS,
    QStyle.PixelMetric.PM_LayoutVerticalSpacing: SPACING_DOWN,
}


class RoomyStyle(QProxyStyle):
    """the platform style with layout room that grows with the text size."""

    def __init__(self) -> None:
        super().__init__()
        self.scale = 1.0

    def pixelMetric(self, metric, option=None, widget=None) -> int:  # noqa: N802 - qt name
        if metric in _MARGINS:
            base = WINDOW_MARGIN if widget is not None and widget.isWindow() else PART_MARGIN
            return round(base * self.scale)
        if metric in _SPACING:
            return round(_SPACING[metric] * self.scale)
        return super().pixelMetric(metric, option, widget)


_STYLE: list[RoomyStyle] = []


def roomy_style(app: QApplication) -> RoomyStyle:
    """set once per app and kept alive here, qt does not own a style made in python."""
    if not _STYLE:
        _STYLE.append(RoomyStyle())
        app.setStyle(_STYLE[0])
    return _STYLE[0]


def load_fonts() -> list[str]:
    """jetbrains mono bundled with the app (ofl), registered once. returns the families."""
    if _FAMILIES:
        return list(_FAMILIES)
    families: list[str] = []
    for item in _PACKAGE.joinpath("fonts").iterdir():
        if item.name.lower().endswith(".ttf"):
            font_id = QFontDatabase.addApplicationFontFromData(QByteArray(item.read_bytes()))
            if font_id >= 0:
                families += QFontDatabase.applicationFontFamilies(font_id)
    _FAMILIES.extend(dict.fromkeys(families))
    return list(_FAMILIES)


def set_dark_title_bar(widget: QWidget, dark: bool) -> None:
    """native frame, dark or light title bar from the theme (dwm immersive dark mode)."""
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return
    try:
        value = ctypes.c_int(1 if dark else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(widget.winId())), 20, ctypes.byref(value), ctypes.sizeof(value)
        )
    except (AttributeError, OSError):
        pass  # older windows without the attribute keeps its default bar


class ThemeManager(QObject):
    """the active theme and language. widgets that paint with theme colors listen to changed,
    widgets with fixed texts to language_changed (D-163)."""

    changed = Signal()
    language_changed = Signal()

    def __init__(self, data_dir: Path, log=lambda _: None) -> None:
        super().__init__()
        self.themes = themes.load_themes(data_dir, log)
        self.theme = themes.pick(self.themes, themes.DEFAULT)
        self.scale = 1.0
        self._pixmaps: dict[tuple, QPixmap] = {}
        self._glowing: list[QWidget] = []
        self._windows: list[QWidget] = []
        # qss can only point at files, so the few icons it needs are written here. the folder
        # goes away when the app ends
        self._tmp = tempfile.TemporaryDirectory(prefix="neonseek-qss-")
        self._qss_dir = Path(self._tmp.name)
        self._qt_translator: QTranslator | None = None

    # language

    def set_language(self, code: str) -> None:
        """every text of the window in this language, without restart. qt's own texts (yes,
        no, the menu of a text field) come from the translations shipped with pyside6."""
        code = i18n.set_language(code)
        app = QCoreApplication.instance()
        if app is not None:
            if self._qt_translator is not None:
                app.removeTranslator(self._qt_translator)
                self._qt_translator = None
            if code != i18n.SOURCE_LANGUAGE:
                translator = QTranslator(self)
                folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
                if translator.load(f"qtbase_{code}", folder):
                    app.installTranslator(translator)
                    self._qt_translator = translator
        self.language_changed.emit()

    # colors

    def color(self, token: str) -> QColor:
        return QColor(self.theme.colors[token])

    # applying

    def px(self, size: float) -> int:
        """a size in pixels at 100 %, scaled with the text size setting."""
        return max(1, round(size * self.scale))

    def percent(self) -> int:
        return round(self.scale * 100)

    def font(self, large: bool = False) -> QFont:
        font = QFont(FONT_FAMILY)
        font.setPixelSize(self.px(FONT_PX_LARGE if large else FONT_PX))
        return font

    def apply(self, key: str | None = None, scale: object = None) -> None:
        """a theme and a text size, both without restart. a scale outside the list is 100 %."""
        if key is not None:
            self.theme = themes.pick(self.themes, key)
        if scale is not None:
            self.scale = scale_factor(scale)
        self._pixmaps.clear()
        app = QApplication.instance()
        if app is not None:
            roomy_style(app).scale = self.scale
            app.setFont(self.font())
            app.setStyleSheet(render_qss(self.theme, self._extra()))
        for widget in list(self._glowing):
            self._set_glow(widget)
        for window in self._windows:
            set_dark_title_bar(window, self.theme.dark)
        self.changed.emit()

    def _extra(self) -> dict[str, str]:
        return {
            **size_tokens(self.scale),
            "font_family": FONT_FAMILY,
            "font_size": f"{self.px(FONT_PX)}px",
            "font_size_large": f"{self.px(FONT_PX_LARGE)}px",
            "icon_check": self._qss_icon("state-done", "text.on_accent"),
            "icon_chevron_down": self._qss_icon("chevron-down", "text.muted"),
        }

    def _qss_icon(self, name: str, token: str) -> str:
        color = self.theme.colors[token]
        path = self._qss_dir / f"{name}-{color[1:]}.svg"
        if not path.exists():
            path.write_bytes(colored_svg(name, color))
        return path.as_posix()

    def track_window(self, window: QWidget) -> None:
        self._windows.append(window)
        window.destroyed.connect(lambda *_: self._forget_window(window))
        set_dark_title_bar(window, self.theme.dark)

    def _forget_window(self, window: QWidget) -> None:
        if window in self._windows:
            self._windows.remove(window)

    # glow: only the active element and the main button, never table rows

    def glow(self, widget: QWidget) -> None:
        if widget not in self._glowing:
            self._glowing.append(widget)
            widget.destroyed.connect(lambda *_: self._forget(widget))
        self._set_glow(widget)

    def no_glow(self, widget: QWidget) -> None:
        if widget in self._glowing:
            self._glowing.remove(widget)
        widget.setGraphicsEffect(None)

    def _forget(self, widget: QWidget) -> None:
        if widget in self._glowing:
            self._glowing.remove(widget)

    def _set_glow(self, widget: QWidget) -> None:
        if not self.theme.glow:
            widget.setGraphicsEffect(None)
            return
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(GLOW_BLUR)
        effect.setOffset(0, 0)
        effect.setColor(self.color("accent.primary"))
        widget.setGraphicsEffect(effect)

    # icons

    def pixmap(self, name: str, token: str, size: int, dpr: float = 1.0) -> QPixmap:
        color = self.theme.colors[token]
        key = (name, color, size, dpr)
        cached = self._pixmaps.get(key)
        if cached is not None:
            return cached
        px = max(1, round(size * dpr))
        pixmap = QPixmap(px, px)
        pixmap.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(QByteArray(colored_svg(name, color)))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, px, px))
        painter.end()
        pixmap.setDevicePixelRatio(dpr)
        self._pixmaps[key] = pixmap
        return pixmap

    def icon(self, name: str, token: str = "text.primary", disabled: str = "text.muted") -> QIcon:
        icon = QIcon()
        for size in (16, 20, 24, 32):
            for dpr in (1.0, 2.0):
                icon.addPixmap(self.pixmap(name, token, size, dpr), QIcon.Mode.Normal)
                icon.addPixmap(self.pixmap(name, disabled, size, dpr), QIcon.Mode.Disabled)
        return icon
