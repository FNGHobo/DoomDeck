"""Look of the application: a dark Fusion style with a red accent.

Kept deliberately small. Everything visual is in this one stylesheet so it is easy
to tweak colours without touching any layout code.
"""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets

ACCENT = "#c0392b"
BACKGROUND = "#1e1f22"
PANEL = "#26282c"
TEXT = "#e4e4e4"
MUTED = "#9a9da3"

STYLESHEET = f"""
QWidget {{ background: {BACKGROUND}; color: {TEXT}; }}
QLineEdit, QPlainTextEdit, QListWidget, QTreeWidget, QComboBox, QSpinBox {{
    background: {PANEL}; border: 1px solid #3a3d42; border-radius: 3px; padding: 2px;
}}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {ACCENT}; color: white; }}
QTabWidget::pane {{ border: 1px solid #3a3d42; }}
QTabBar::tab {{ background: {PANEL}; padding: 6px 12px; border: 1px solid #3a3d42; }}
QTabBar::tab:selected {{ background: {ACCENT}; color: white; }}
QPushButton {{ background: #34373c; border: 1px solid #4a4d52; border-radius: 3px; padding: 5px 10px; }}
QPushButton:hover {{ background: #3f4348; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#launch {{ background: {ACCENT}; color: white; font-weight: bold; font-size: 15px; padding: 8px 28px; }}
QPushButton#launch:hover {{ background: #d84a3b; }}
QLabel#warning {{ color: #f0b429; }}
QLabel#error {{ color: #ff6b5e; }}
QLabel#muted {{ color: {MUTED}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid #4a4d52; }}
"""


def apply(app: QtWidgets.QApplication) -> None:
    """Use the Fusion style (consistent on every desktop) plus the stylesheet."""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    # Palette entries the stylesheet does not cover (e.g. native dialog parts).
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(BACKGROUND))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(PANEL))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor("#34373c"))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(ACCENT))
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("white"))
    app.setPalette(palette)
