"""the right-click menu of tracks, in the track table and the report (D-125).

core decides what each item works on and why an item is off (views.track_choice), the window
only shows the menu and runs the chosen action.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QMenu, QWidget

from neonseek import views
from neonseek.ui.theme.loader import ThemeManager

ICONS = {
    views.MENU_DOWNLOAD: "download",
    views.MENU_PROPERTIES: "info",
    views.MENU_EDIT: "tag",
    views.MENU_FOLDER: "folder",
    views.MENU_EXCLUDE: "state-excluded",
    views.MENU_INCLUDE: "plus",
    views.MENU_DELETE: "trash",
}
# a line after these items
GROUP_ENDS = (views.MENU_DOWNLOAD, views.MENU_FOLDER, views.MENU_INCLUDE)


def build_track_menu(
    parent: QWidget,
    theme: ThemeManager,
    choice: views.TrackChoice,
    handlers: dict[str, Callable[[list[int]], None]],
) -> QMenu:
    """one action per item. an item that does not work is off and its tooltip says why."""
    menu = QMenu(parent)
    menu.setToolTipsVisible(True)
    for item in views.MENU_ITEMS:
        action = menu.addAction(
            theme.icon(ICONS[item], "text.primary"), views.menu_label(item, choice)
        )
        action.setData(item)
        reason = views.menu_reason(item, choice)
        action.setEnabled(reason is None)
        if reason:
            action.setToolTip(reason)
        else:
            targets = list(choice.targets(item))
            action.triggered.connect(lambda _=False, i=item, ids=targets: handlers[i](ids))
        if item in GROUP_ENDS:
            menu.addSeparator()
    return menu
