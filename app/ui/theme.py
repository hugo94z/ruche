"""Thèmes clair et sombre de l'interface."""

from __future__ import annotations

DARK_QSS = """
QWidget { background: #1e1f22; color: #e6e6e6; }
QMainWindow, QDialog { background: #1e1f22; }
QLineEdit, QComboBox, QTextBrowser, QListWidget {
    background: #2b2d31; color: #e6e6e6;
    border: 1px solid #3a3d42; border-radius: 4px; padding: 4px;
}
QListWidget::item:selected { background: #2563eb; color: white; }
QPushButton {
    background: #2f3136; color: #e6e6e6;
    border: 1px solid #3a3d42; border-radius: 4px; padding: 5px 10px;
}
QPushButton:hover { background: #3a3d42; }
QPushButton:disabled { color: #7a7d82; }
QStatusBar { background: #18191c; color: #b9bbbe; }
QLabel { background: transparent; }
QMenu { background: #2b2d31; color: #e6e6e6; border: 1px solid #3a3d42; }
QMenu::item:selected { background: #2563eb; }
QScrollBar:vertical { background: #1e1f22; width: 12px; }
QScrollBar::handle:vertical { background: #4a4d52; border-radius: 6px; }
QCheckBox { background: transparent; }
"""

THEMES = ("clair", "sombre")
LABELS = {"clair": "Thème clair", "sombre": "Thème sombre"}


def apply(app, name: str) -> str:
    """Applique le thème et renvoie celui réellement retenu."""
    if name not in THEMES:
        name = "clair"
    app.setStyleSheet(DARK_QSS if name == "sombre" else "")
    return name


def next_theme(name: str) -> str:
    return "sombre" if name != "sombre" else "clair"
