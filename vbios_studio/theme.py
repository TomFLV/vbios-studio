"""Colours and the Qt stylesheet (one dark theme)."""
BG = '#0f1216'
PANEL = '#161b22'
CARD = '#1b222c'
CARD_HI = '#212a36'
LINE = '#2a3441'
TEXT = '#e6edf3'
MUTED = '#8b98a8'
ACCENT = '#4f8cff'
GOOD = '#3fb950'
WARN = '#d29922'
BAD = '#f85149'

STATE_COLORS = {'active': GOOD, 'pending': ACCENT, 'none': MUTED, 'rejected': BAD, 'unknown': WARN}

STYLE = f"""
* {{ font-family: 'Segoe UI'; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QFrame#sidebar {{ background: {PANEL}; border-right: 1px solid {LINE}; }}
QLabel#brand {{ font-size: 15pt; font-weight: 600; padding: 18px 18px 2px 18px; }}
QLabel#brandSub {{ color: {MUTED}; padding: 0 18px 14px 18px; font-size: 9pt; }}
QPushButton#nav {{ text-align: left; padding: 10px 18px; border: none; border-left: 3px solid transparent;
                   background: transparent; font-size: 10.5pt; color: {MUTED}; }}
QPushButton#nav:hover {{ background: {CARD}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {CARD}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}
QFrame#card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; }}
QLabel#h1 {{ font-size: 17pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#big {{ font-size: 20pt; font-weight: 600; }}
QLabel#bigUnit {{ color: {MUTED}; font-size: 10pt; }}
QLabel#badgeExp {{ color: {WARN}; border: 1px solid {WARN}; border-radius: 6px; padding: 0 5px; font-size: 8pt; }}
QLabel#badgeAdv {{ color: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 6px; padding: 0 5px; font-size: 8pt; }}
QLabel#badgeLock {{ color: {MUTED}; border: 1px solid {LINE}; border-radius: 6px; padding: 0 5px; font-size: 8pt; }}
QFrame#row {{ background: transparent; border-radius: 6px; }}
QFrame#row:hover {{ background: {CARD_HI}; }}
QFrame#row[changed="true"] {{ background: #1c2a40; border-left: 3px solid {ACCENT}; }}
QFrame#row[dependent="true"] {{ background: #1e2531; border-left: 3px solid {MUTED}; }}
QPushButton {{ background: {CARD_HI}; border: 1px solid {LINE}; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {LINE}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:disabled {{ background: #2b3d5c; border-color: #2b3d5c; color: #9db3d6; }}
QPushButton#danger {{ border-color: #6e2b2b; color: #ffb4ae; }}
QPushButton#danger:hover {{ border-color: {BAD}; }}
QToolButton {{ background: transparent; border: none; color: {MUTED}; padding: 2px 4px; }}
QToolButton:hover {{ color: {TEXT}; }}
QLineEdit, QDoubleSpinBox, QComboBox, QPlainTextEdit, QListWidget {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px; padding: 5px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QDoubleSpinBox:disabled {{ color: {MUTED}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {LINE}; border-radius: 4px; background: {PANEL}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QListWidget::item {{ padding: 0; border: none; }}
QListWidget::item:selected {{ background: {CARD_HI}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QSplitter::handle {{ background: {BG}; }}
QSlider::groove:horizontal {{ height: 6px; background: {LINE}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:disabled {{ background: {MUTED}; }}
QTreeWidget {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px; }}
QTreeWidget::item {{ padding: 3px 0; }}
QTreeWidget::item:selected {{ background: {CARD_HI}; color: {TEXT}; }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
                        padding: 6px 8px; font-weight: 600; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 18px; border: none;
               border-bottom: 2px solid transparent; font-size: 10.5pt; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; }}
"""
