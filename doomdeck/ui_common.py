"""Small things shared by the main window and its mixins: lists, sizes and helpers."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .models import AppConfig, Preset

SKILLS = ["Engine default", "1 - I'm too young to die", "2 - Hey, not too rough",
          "3 - Hurt me plenty", "4 - Ultra-violence", "5 - Nightmare!"]
SORTS = ["Name", "Last played", "Most played", "Times launched", "Favorites first"]
# Preset picture sizes: stored copy limit, Setup tab box, preset list icon.
PICTURE_MAX = QtCore.QSize(640, 400)
PICTURE_BOX = QtCore.QSize(160, 100)
PICTURE_ICON = QtCore.QSize(64, 40)


def add_action(menu: QtWidgets.QMenu, text: str, slot) -> QtGui.QAction:
    """Add a menu entry that calls slot() with no arguments.

    triggered() sends a 'checked' flag; swallowing it here means lambdas with default
    arguments are never handed that flag by mistake.
    """
    action = QtGui.QAction(text, menu)
    action.triggered.connect(lambda _checked=False: slot())
    menu.addAction(action)
    return action


def format_time(seconds: int) -> str:
    """12345 -> '3h 25m' (for play time labels)."""
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}h {rest // 60}m" if hours else f"{rest // 60}m"


def format_date(stamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp)) if stamp else "never"

def active_preset_files(config: AppConfig, preset: Preset) -> list[str]:
    """Existing, enabled files of a preset (autoload files excluded), for map/info lookups."""
    disabled = set(preset.disabled_files)
    return [f for f in preset.files if f not in disabled and Path(f).is_file()]
