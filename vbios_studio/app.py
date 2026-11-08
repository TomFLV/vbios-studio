"""VBIOS Studio main window: Dashboard (GPU, override state, live readings), Editor, Profiles, Log."""
from __future__ import annotations
import os
import sys
import threading
import time
import traceback
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter,
                               QStackedWidget, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget, QHeaderView)

from . import __version__, theme
from . import backend, sensors, simple
from nvidia_vbios.editor import VbiosError
from nvidia_vbios.editor.sections import field_section, opt_in_field_keys, technical_field_keys
from nvidia_vbios.gui import presentation as pr

SECTION_TITLES = {'power': 'Power', 'voltage': 'Voltage', 'clocks': 'Clocks and offsets', 'current': 'Current limits',
                  'fans': 'Fans', 'other': 'Other', 'advanced': 'Advanced: idle power and clock controllers'}
STEPS = {'W': 5, 'mV': 5, 'MHz': 15, 'A': 0.5, '°C': 1, '%': 1, 'rpm': 50, 'RPM': 50, 'kHz': 1}


# ---------------------------------------------------------------- helpers

class Task(QObject):
    """Run fn(*args) on a thread; on_done/on_fail run back on the GUI thread."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, parent, fn, *args, on_done=None, on_fail=None):
        super().__init__(parent)
        self.fn, self.args, self.on_done, self.on_fail = fn, args, on_done, on_fail
        self.done.connect(self._ok)
        self.failed.connect(self._err)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.done.emit(self.fn(*self.args))
        except Exception as exc:
            backend.log('error: ' + ''.join(traceback.format_exception_only(type(exc), exc)).strip())
            self.failed.emit(str(exc) or type(exc).__name__)

    @Slot(object)
    def _ok(self, result):
        if self.on_done:
            self.on_done(result)
        self.deleteLater()

    @Slot(str)
    def _err(self, message):
        if self.on_fail:
            self.on_fail(message)
        self.deleteLater()


def label(text='', name=None, wrap=False):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def card(title=None):
    f = QFrame()
    f.setObjectName('card')
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    if title:
        lay.addWidget(label(title, 'h2'))
    return f, lay


def pill(text, color):
    w = QLabel(text)
    w.setStyleSheet(f'color: {color}; border: 1px solid {color}; border-radius: 10px; padding: 2px 10px; '
                    f'font-weight: 600;')
    w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return w


def page():
    w = QWidget()
    w.setObjectName('page')
    lay = QVBoxLayout(w)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(16)
    return w, lay


def disp(field, value):
    """Stored value -> display text with units."""
    d = pr.to_display(field, value)
    text = pr.value_text(field, d if d is not None else value)
    unit = pr.display_units(field)
    return f'{text} {unit}'.strip() if text is not None else '—'


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.ACCENT))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(6, 6, 52, 52, 12, 12)
    p.setPen(QColor('white'))
    p.setFont(QFont('Segoe UI', 22, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, 'V')
    p.end()
    return QIcon(pm)


class Spin(QDoubleSpinBox):
    """Number box: plain text (400, not 400.000), no wheel changes unless focused."""
    def __init__(self, decimals):
        super().__init__()
        self.setDecimals(decimals)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setKeyboardTracking(False)
        self.setMinimumWidth(130)
        self.setButtonSymbols(QDoubleSpinBox.UpDownArrows)

    def textFromValue(self, value):
        return pr.number(Decimal(repr(round(value, self.decimals()))))

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


# ---------------------------------------------------------------- editor rows

class FieldRow(QFrame):
    edited = Signal(str, object)     # key, stored value text or None (back to the ROM value)

    def __init__(self, field, by_key):
        super().__init__()
        self.setObjectName('row')
        self.field = field
        self.key = field['key']
        self.setToolTip(self._tip(field))
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 6, 10, 6)
        grid.setHorizontalSpacing(12)
        title, experimental = pr.row_title(field)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = label(title, wrap=True)
        self.title.setMinimumWidth(120)
        head.addWidget(self.title, 1)
        if experimental:
            head.addWidget(label('experimental', 'badgeExp'), 0, Qt.AlignTop)
        if field.get('opt_in'):
            head.addWidget(label('XOC', 'badgeAdv'), 0, Qt.AlignTop)
        head.addStretch()
        grid.addLayout(head, 0, 0)
        self.sub = label('', 'muted')
        grid.addWidget(self.sub, 1, 0)
        self.reset = QToolButton()
        self.reset.setText('↺')
        self.reset.setToolTip('Back to the ROM value')
        self.reset.clicked.connect(lambda: self.edited.emit(self.key, None))
        self.reset.setVisible(False)
        grid.addWidget(self.reset, 0, 1, 2, 1)
        self.editor = None
        locked = not field['editable'] or pr.dependency_reason(field, by_key)
        if locked:
            grid.addWidget(label(disp(field, field.get('current')), None), 0, 2, 2, 1, Qt.AlignRight)
            grid.addWidget(label('read-only', 'badgeLock'), 0, 3, 2, 1)
        elif field.get('units') == 'flag':
            self.editor = QComboBox()
            self.editor.addItem(pr.value_text(field, 0), 0)
            self.editor.addItem(pr.value_text(field, 1), 1)
            self.editor.setMinimumWidth(130)
            self.editor.currentIndexChanged.connect(lambda _: self._emit(self.editor.currentData()))
            grid.addWidget(self.editor, 0, 2, 2, 1)
            grid.addWidget(label('', 'muted'), 0, 3, 2, 1)
        else:
            integer = field.get('type') == 'integer' and pr.display_units(field) not in ('A', 'kHz')
            self.editor = Spin(0 if integer else 3)
            self.set_bounds(field.get('bounds'))
            self.editor.setSingleStep(STEPS.get(pr.display_units(field), 1))
            self.editor.valueChanged.connect(lambda v: self._emit(pr.to_stored(field, pr.number(Decimal(repr(v))))))
            grid.addWidget(self.editor, 0, 2, 2, 1)
            grid.addWidget(label(pr.display_units(field), 'muted'), 0, 3, 2, 1)
        grid.setColumnStretch(0, 1)
        self.show_value(field.get('current'), field.get('current'), False, False)

    @staticmethod
    def _tip(field):
        parts = [f"<b>{pr.row_title(field)[0]}</b> <span style='color:{theme.MUTED}'>({field['key']})</span>"]
        reason = pr.read_only_reason(field) if not field['editable'] else field.get('reason')
        if reason:
            parts.append(reason)
        return '<p style="white-space:pre-wrap; max-width:520px">' + '<br><br>'.join(parts) + '</p>'

    def _emit(self, value):
        if self.editor is not None and not self.editor.signalsBlocked():
            self.edited.emit(self.key, value)

    def set_bounds(self, bounds):
        """Allowed range in display units. A rated limit's maximum is its ceiling, so the staged copy's bounds
        (after a raised ceiling) replace the ROM's."""
        if not isinstance(self.editor, Spin):
            return
        b = bounds or {}
        lo, hi = pr.to_display(self.field, b.get('minimum')), pr.to_display(self.field, b.get('maximum'))
        self.editor.blockSignals(True)
        self.editor.setRange(float(lo) if lo is not None else -1e9, float(hi) if hi is not None else 1e9)
        self.editor.blockSignals(False)

    def show_value(self, rom_value, value, changed, dependent, bounds=None):
        """Show value (stored units); mark the row as edited (changed) or moved by another edit (dependent)."""
        if self.editor is not None:
            self.set_bounds(bounds or self.field.get('bounds'))
            self.editor.blockSignals(True)
            if isinstance(self.editor, QComboBox):
                self.editor.setCurrentIndex(1 if str(value) in ('1', '1.0') else 0)
            elif value is not None:
                d = pr.to_display(self.field, value)
                if d is not None:
                    self.editor.setValue(float(d))
            self.editor.blockSignals(False)
        sub = f'ROM: {disp(self.field, rom_value)}'
        if dependent:
            sub += '   ·   follows your edit'
        self.sub.setText(sub)
        self.reset.setVisible(changed)
        self.setProperty('changed', 'true' if changed else 'false')
        self.setProperty('dependent', 'true' if dependent and not changed else 'false')
        self.style().unpolish(self)
        self.style().polish(self)

    def matches(self, text):
        return not text or text in self.title.text().lower() or text in self.key


LEVEL_COLORS = {simple.OK: theme.GOOD, simple.INFO: theme.ACCENT, simple.WARN: theme.WARN, simple.DANGER: theme.BAD}
LEVEL_ICONS = {simple.OK: '✔', simple.INFO: 'ℹ', simple.WARN: '⚠', simple.DANGER: '⛔'}


class SliderCard(QFrame):
    """One everyday setting: big value, slider + exact box, stock marker, reset, live warning, what follows it."""
    changed = Signal(str, float)

    def __init__(self, ctl: simple.Control):
        super().__init__()
        self.ctl = ctl
        self.setObjectName('card')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(ctl.title, 'h2'))
        titles.addWidget(label(ctl.blurb, 'muted', wrap=True))
        head.addLayout(titles, 1)
        self.value = label('', 'big')
        head.addWidget(self.value, 0, Qt.AlignRight | Qt.AlignTop)
        head.addWidget(label(ctl.unit, 'bigUnit'), 0, Qt.AlignTop)
        lay.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.steps = max(1, round((ctl.hi - ctl.lo) / ctl.step))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.steps)
        self.slider.setPageStep(max(1, self.steps // 20))
        self.slider.valueChanged.connect(lambda i: self._user(ctl.lo + i * ctl.step))
        row.addWidget(self.slider, 1)
        self.box = Spin(0 if float(ctl.step).is_integer() else 1)
        self.box.setRange(ctl.lo, ctl.hi)
        self.box.setSingleStep(ctl.step)
        self.box.setMinimumWidth(110)
        self.box.valueChanged.connect(self._user)
        row.addWidget(self.box)
        self.reset = QToolButton()
        self.reset.setText('↺ stock')
        self.reset.clicked.connect(lambda: self._user(ctl.stock))
        row.addWidget(self.reset)
        lay.addLayout(row)
        scale = QHBoxLayout()
        scale.addWidget(label(f'{simple._fmt(ctl.lo)} {ctl.unit}', 'muted'))
        scale.addStretch()
        scale.addWidget(label(f'Stock {simple._fmt(ctl.stock)} {ctl.unit}', 'muted'))
        scale.addStretch()
        scale.addWidget(label(f'{simple._fmt(ctl.hi)} {ctl.unit}', 'muted'))
        lay.addLayout(scale)
        self.warning = label('', None, wrap=True)
        lay.addWidget(self.warning)
        self.follows = label('', 'muted', wrap=True)
        self.follows.hide()
        lay.addWidget(self.follows)
        self.current = None
        self.set_value(ctl.stock)

    def _user(self, value):
        value = round(min(max(value, self.ctl.lo), self.ctl.hi), 3)
        if self.current is not None and abs(value - self.current) < 1e-9:
            return
        self.set_value(value)
        self.changed.emit(self.ctl.key, value)

    def set_value(self, value):
        self.current = value
        for w, v in ((self.slider, round((value - self.ctl.lo) / self.ctl.step)), (self.box, value)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.value.setText(simple._fmt(value))
        changed = abs(value - self.ctl.stock) > 1e-9
        self.reset.setVisible(changed)
        level, text = self.ctl.warn(self.fields, value) if hasattr(self, 'fields') else (simple.OK, '')
        color = LEVEL_COLORS[level]
        self.warning.setText(f"<span style='color:{color}'>{LEVEL_ICONS[level]}</span>&nbsp; {text}")
        self.setStyleSheet(f'QFrame#card {{ border-color: {color if level in (simple.WARN, simple.DANGER) else theme.LINE}; }}'
                           f'QSlider::groove:horizontal {{ height: 6px; background: {theme.LINE}; border-radius: 3px; }}'
                           f'QSlider::sub-page:horizontal {{ background: {color if changed else theme.ACCENT}; '
                           f'border-radius: 3px; }}'
                           f'QSlider::handle:horizontal {{ background: {theme.TEXT}; width: 16px; margin: -6px 0; '
                           f'border-radius: 8px; }}')

    def show_follow(self, follow, fields):
        if not follow:
            self.follows.hide()
            return
        parts = []
        for k, v in follow.items():
            f = fields[k]
            parts.append(f"{pr.row_title(f)[0]} {disp(f, f.get('current'))} → {disp(f, v)}")
        self.follows.setText('Also set, so they do not cap this one:  ' + '   ·   '.join(parts))
        self.follows.show()


class EditorPage(QWidget):
    status = Signal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName('page')
        self.doc = None
        self.staged = None
        self.rom_path = None
        self.pending = {}
        self.rows = {}
        self.sections = {}
        self.generation = 0
        self.timer = QTimer(self, singleShot=True, interval=350, timeout=self._stage)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        top = QHBoxLayout()
        head = QVBoxLayout()
        head.addWidget(label('Editor', 'h1'))
        self.romInfo = label('Open a VBIOS file saved from your card (for example with GPU-Z).', 'muted')
        head.addWidget(self.romInfo)
        top.addLayout(head, 1)
        b = QPushButton('Open ROM…')
        b.clicked.connect(self.choose_rom)
        top.addWidget(b, 0, Qt.AlignTop)
        outer.addLayout(top)

        bar = QHBoxLayout()
        self.preset = QComboBox()
        for name, changes in backend.presets():
            self.preset.addItem(name, changes)
        bar.addWidget(label('Preset', 'muted'))
        bar.addWidget(self.preset, 1)
        b = QPushButton('Apply preset')
        b.clicked.connect(self.apply_preset)
        bar.addWidget(b)
        bar.addStretch(1)
        outer.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.tabs = QTabWidget()
        # Simple: one slider card per everyday setting
        self.simpleScroll = QScrollArea(widgetResizable=True)
        self.simpleScroll.setFrameShape(QFrame.NoFrame)
        self.simpleScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.simpleBody = QWidget()
        self.simpleLay = QVBoxLayout(self.simpleBody)
        self.simpleLay.setContentsMargins(0, 12, 8, 0)
        self.simpleLay.setSpacing(14)
        self.simpleLay.addStretch()
        self.simpleScroll.setWidget(self.simpleBody)
        self.tabs.addTab(self.simpleScroll, 'Simple')
        # Advanced: every field
        adv = QWidget()
        al = QVBoxLayout(adv)
        al.setContentsMargins(0, 12, 0, 0)
        al.setSpacing(12)
        abar = QHBoxLayout()
        self.search = QLineEdit(placeholderText='Search settings…  (power, voltage, fan, clock…)')
        self.search.textChanged.connect(self.filter)
        abar.addWidget(self.search, 1)
        self.advanced = QCheckBox('Show XOC fields')
        self.advanced.setToolTip('Show the advanced, opt-in fields (connector, current and power thresholds, '
                                 'follow-OV and similar). They change limits that protect the hardware.')
        self.advanced.toggled.connect(self.filter)
        abar.addWidget(self.advanced)
        self.readonly = QCheckBox('Show read-only')
        self.readonly.toggled.connect(self.filter)
        abar.addWidget(self.readonly)
        al.addLayout(abar)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.bodyLay = QVBoxLayout(self.body)
        self.bodyLay.setContentsMargins(0, 0, 8, 0)
        self.bodyLay.setSpacing(14)
        self.bodyLay.addStretch()
        self.scroll.setWidget(self.body)
        al.addWidget(self.scroll, 1)
        self.tabs.addTab(adv, 'Advanced')
        split.addWidget(self.tabs)
        self.cards = {}
        self.followed = {}

        side, slay = card('Changes')
        self.changes = QListWidget()
        self.changes.setWordWrap(True)
        slay.addWidget(self.changes, 1)
        self.check = label('No changes yet.', 'muted', wrap=True)
        slay.addWidget(self.check)
        self.name = QLineEdit(placeholderText='Profile name, e.g. 400 W / 1200 mV')
        slay.addWidget(self.name)
        row = QHBoxLayout()
        self.resetAll = QPushButton('Reset all')
        self.resetAll.clicked.connect(lambda: self.set_pending({}))
        row.addWidget(self.resetAll)
        self.saveBtn = QPushButton('Save profile')
        self.saveBtn.clicked.connect(lambda: self.save(load=False))
        row.addWidget(self.saveBtn)
        slay.addLayout(row)
        self.loadBtn = QPushButton('Save and load to GPU')
        self.loadBtn.setObjectName('primary')
        self.loadBtn.clicked.connect(lambda: self.save(load=True))
        slay.addWidget(self.loadBtn)
        slay.addWidget(label('Loading stores the copy in the driver\'s registry key. Nothing is flashed; '
                             'Windows applies it at the next restart.', 'muted', wrap=True))
        side.setMinimumWidth(340)
        side.setMaximumWidth(460)
        split.addWidget(side)
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1000, 400])
        outer.addWidget(split, 1)
        self._buttons()

    # -- ROM
    def choose_rom(self):
        start = str(Path(self.rom_path).parent) if self.rom_path else str(Path.home() / 'Desktop')
        path, _ = QFileDialog.getOpenFileName(self, 'Open a VBIOS file', start, 'VBIOS (*.rom *.bin);;All files (*)')
        if path:
            self.open_rom(path)

    def open_rom(self, path, changes=None):
        self.status.emit(f'Opening {Path(path).name}…')
        Task(self, backend.open_rom, path, on_done=lambda doc: self._opened(path, doc, changes),
             on_fail=lambda m: self._error('Cannot open this file', m))

    def _opened(self, path, doc, changes):
        self.doc, self.rom_path, self.staged = doc, path, None
        QSettings().setValue('last_rom', path)
        arch = doc.report.get('architecture') or {}
        vers = 'VBIOS ' + ', '.join(doc.report.get('versions') or ['?'])
        self.romInfo.setText(f"{arch.get('product', 'Unknown GPU')}  ·  {arch.get('family', '?').title()}  ·  "
                             f"{Path(path).name}  ·  sha256 {doc.report.get('sha256', '')[:12]}…  {vers}")
        self._build_rows()
        self._build_cards()
        self.followed = {}
        self.set_pending(dict(changes or {}))
        self.status.emit(f'Opened {Path(path).name}')

    def _build_rows(self):
        while self.bodyLay.count() > 1:
            item = self.bodyLay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows, self.sections = {}, {}
        fields = backend.fields_of(self.doc)
        by_key = {f['key']: f for f in fields}
        hidden = technical_field_keys(self.doc.report)
        self.opt_in = opt_in_field_keys(self.doc.report)
        for f in pr.section_order(fields):
            if f['key'] in hidden:
                continue
            sec = field_section(f)
            if sec not in self.sections:
                c, lay = card(SECTION_TITLES.get(sec, sec.title()))
                lay.setSpacing(2)
                self.sections[sec] = (c, lay)
                self.bodyLay.insertWidget(self.bodyLay.count() - 1, c)
            row = FieldRow(f, by_key)
            row.edited.connect(self.on_edit)
            self.rows[f['key']] = row
            self.sections[sec][1].addWidget(row)
        self.filter()

    def _build_cards(self):
        while self.simpleLay.count() > 1:
            item = self.simpleLay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.fields_by_key = {f['key']: f for f in backend.fields_of(self.doc)}
        self.cards = {}
        intro = label('Move a slider; supporting limits follow automatically and every change is checked before '
                      'it can be saved. Every field is in the Advanced tab.', 'muted', wrap=True)
        self.simpleLay.insertWidget(0, intro)
        for ctl in simple.controls(self.fields_by_key):
            c = SliderCard(ctl)
            c.fields = self.fields_by_key
            c.set_value(ctl.stock)
            c.changed.connect(self.on_slider)
            self.cards[ctl.key] = c
            self.simpleLay.insertWidget(self.simpleLay.count() - 1, c)
        if not self.cards:
            self.simpleLay.insertWidget(1, label('This ROM has no slider settings; use the Advanced tab.', 'muted'))

    def on_slider(self, key, value):
        """A slider moved: set its field, and replace the supporting limits it set last time with fresh ones."""
        ctl = self.cards[key].ctl
        pending = dict(self.pending)
        for k in self.followed.pop(key, ()):
            pending.pop(k, None)
        if abs(value - ctl.stock) < 1e-9:
            pending.pop(key, None)
            follow = {}
        else:
            pending[key] = simple._fmt(value)
            follow = ctl.follow(self.fields_by_key, value)
            pending.update(follow)
            self.followed[key] = set(follow)
        self.cards[key].show_follow(follow, self.fields_by_key)
        if not self.name.isModified():          # suggest a name until the user types one
            parts = [f"{pending[k]} {u}" for k, u in (('power_max_w', 'W'), ('nvvdd_rel_equation_max_mv', 'mV'))
                     if k in pending]
            self.name.setText(' / '.join(parts) or ('Custom' if pending else ''))
        self.set_pending(pending)

    def filter(self, *_):
        text = self.search.text().strip().lower()
        for sec, (c, _) in self.sections.items():
            any_visible = False
            for key, row in self.rows.items():
                if field_section(row.field) != sec:
                    continue
                show = row.matches(text) and (self.advanced.isChecked() or key not in self.opt_in or
                                              key in self.pending) and \
                    (self.readonly.isChecked() or row.editor is not None)
                row.setVisible(show)
                any_visible |= show
            c.setVisible(any_visible)

    # -- changes
    def apply_preset(self):
        if not self.doc:
            return self._error('No ROM open', 'Open your card\'s VBIOS file first.')
        changes = {k: str(v) for k, v in (self.preset.currentData() or {}).items()}
        if any(k in self.opt_in for k in changes):
            self.advanced.setChecked(True)
        self.name.setText('' if not changes else self.preset.currentText())
        self.followed = {}
        for c in self.cards.values():
            c.show_follow({}, self.fields_by_key)
        self.set_pending(changes)

    def on_edit(self, key, value):
        pending = dict(self.pending)
        field = self.rows[key].field
        if value is None or str(value) == pr.number(field.get('current')):
            pending.pop(key, None)
        else:
            pending[key] = str(value)
        self.set_pending(pending)

    def set_pending(self, pending):
        # Any check still running is for older values: drop its result, and keep Save off until the check for
        # exactly these values is back (saving must never write a copy that differs from what is shown).
        self.generation += 1
        self.pending = pending
        self.staged = None
        self._buttons()
        self.timer.start()
        self.check.setText('Checking…' if pending else 'No changes yet.')

    def _stage(self):
        gen = self.generation
        if not self.doc:
            return
        if not self.pending:
            return self._show_staged(None)
        wanted = dict(self.pending)
        Task(self, backend.stage, self.doc, wanted,
             on_done=lambda d: gen == self.generation and wanted == self.pending and
             (setattr(self, 'staged_for', wanted), self._show_staged(d)),
             on_fail=lambda m: gen == self.generation and self._show_error(m))

    def _show_staged(self, staged):
        self.staged = staged
        after, bounds = {}, {}
        if staged is not None:
            for before, aft in staged.preview:
                f = aft or before
                after[f['key']] = (aft or {}).get('current')
            bounds = {f['key']: f.get('bounds') for f in staged.proposal.manifest['after']['fields']}
        for key, row in self.rows.items():
            rom = row.field.get('current')
            changed = key in self.pending
            dependent = key in after and not changed
            row.show_value(rom, after.get(key, rom) if (changed or dependent) else rom, changed, dependent,
                           bounds.get(key))
        for key, c in self.cards.items():
            value = self.pending.get(key, after.get(key, c.ctl.stock))
            c.set_value(float(value) if value is not None else c.ctl.stock)
        self.changes.clear()
        if staged is not None:
            for before, aft in staged.preview:
                f = aft or before
                title = pr.row_title(f)[0]
                text = f"{title}\n    {disp(f, (before or {}).get('current'))}  →  {disp(f, (aft or {}).get('current'))}"
                item = QListWidgetItem(text)
                if f['key'] not in self.pending:
                    item.setForeground(QColor(theme.MUTED))
                self.changes.addItem(item)
            m = staged.proposal.manifest
            nbytes = m.get('changed_bytes', '?') if isinstance(m, dict) else '?'
            self.check.setText(f"<span style='color:{theme.GOOD}'>✔ Valid copy</span> · {len(staged.preview)} "
                               f"settings, {nbytes} bytes change; checksums are repaired on save.")
        else:
            self.check.setText('No changes yet.')
        self.filter()
        self._buttons()

    def _show_error(self, message):
        self.staged = None
        self.check.setText(f"<span style='color:{theme.BAD}'>✖ {message}</span>")
        for key, c in self.cards.items():
            if key in self.pending:
                c.set_value(float(self.pending[key]))
        self._buttons()

    def _buttons(self):
        ok = self.staged is not None
        self.saveBtn.setEnabled(ok)
        self.loadBtn.setEnabled(ok)
        self.resetAll.setEnabled(bool(self.pending))

    # -- save / load
    def save(self, load):
        if self.staged is None or getattr(self, 'staged_for', None) != self.pending:
            return
        name = self.name.text().strip() or 'Edited VBIOS'
        if load and not self.win.confirm_load(name, [self.changes.item(i).text() for i in range(self.changes.count())]):
            return
        self.status.emit('Saving profile…')
        Task(self, backend.build_profile, self.staged, name, self.rom_path,
             on_done=lambda p: self._saved(p, load), on_fail=lambda m: self._error('Saving failed', m))

    def _saved(self, profile, load):
        self.status.emit(f'Saved profile "{profile.name}"')
        self.win.profiles.refresh()
        if load:
            self.win.load_profile(profile, confirmed=True)

    def _error(self, title, message):
        self.status.emit(f'{title}: {message}')
        QMessageBox.warning(self, title, message)


# ---------------------------------------------------------------- dashboard

class Dashboard(QWidget):
    status = Signal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.gpu = None
        self.verify_report = None
        self.setObjectName('page')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(label('Dashboard', 'h1'), 1)
        b = QPushButton('Refresh')
        b.clicked.connect(self.refresh)
        top.addWidget(b)
        lay.addLayout(top)

        grid = QGridLayout()
        grid.setSpacing(16)
        c, cl = card('Graphics card')
        self.gpuName = label('Looking for NVIDIA GPUs…', 'h2')
        self.gpuInfo = label('', 'muted', wrap=True)
        cl.addWidget(self.gpuName)
        cl.addWidget(self.gpuInfo)
        cl.addStretch()
        grid.addWidget(c, 0, 0)

        c, cl = card('VBIOS override')
        head = QHBoxLayout()
        self.statePill = pill('…', theme.MUTED)
        head.addWidget(self.statePill)
        self.stateHead = label('', 'h2')
        head.addWidget(self.stateHead, 1)
        cl.addLayout(head)
        self.stateDetail = label('', 'muted', wrap=True)
        cl.addWidget(self.stateDetail)
        self.stateProfile = label('', None, wrap=True)
        cl.addWidget(self.stateProfile)
        row = QHBoxLayout()
        self.verifyBtn = QPushButton('Verify')
        self.verifyBtn.clicked.connect(self.refresh)
        row.addWidget(self.verifyBtn)
        self.unloadBtn = QPushButton('Back to stock')
        self.unloadBtn.setObjectName('danger')
        self.unloadBtn.clicked.connect(lambda: self.win.unload())
        row.addWidget(self.unloadBtn)
        self.restartBtn = QPushButton('Restart Windows')
        self.restartBtn.clicked.connect(lambda: self.win.restart())
        row.addWidget(self.restartBtn)
        row.addStretch()
        cl.addLayout(row)
        grid.addWidget(c, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        c, cl = card('Live')
        live = QGridLayout()
        live.setHorizontalSpacing(28)
        self.live = {}
        for i, (key, title, unit) in enumerate([('power', 'Board power', 'W'), ('limit', 'Power limit', 'W'),
                                                ('core', 'Core clock', 'MHz'), ('mem', 'Memory clock', 'MHz'),
                                                ('temp', 'GPU temperature', '°C'), ('load', 'GPU load', '%')]):
            box = QVBoxLayout()
            box.addWidget(label(title, 'muted'))
            val = QHBoxLayout()
            v = label('—', 'big')
            val.addWidget(v)
            val.addWidget(label(unit, 'bigUnit'), 0, Qt.AlignBottom)
            val.addStretch()
            box.addLayout(val)
            self.live[key] = v
            live.addLayout(box, 0, i)
        cl.addLayout(live)
        lim = QHBoxLayout()
        lim.addWidget(label('Limited by', 'muted'))
        self.limiter = label('—', 'h2')
        lim.addWidget(self.limiter, 1)
        cl.addLayout(lim)
        self.liveNote = label('', 'muted')
        cl.addWidget(self.liveNote)
        lay.addWidget(c)

        # Power limit: what Afterburner's power slider does, without Afterburner
        c, cl = card('Power limit')
        cl.addWidget(label('The board power the card may use right now, up to the ceiling in its VBIOS. Windows '
                           'resets it to the boot value at every restart unless "Apply at every logon" is on.',
                           'muted', wrap=True))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.plSlider = QSlider(Qt.Horizontal)
        self.plSlider.setEnabled(False)
        row.addWidget(self.plSlider, 1)
        self.plBox = Spin(0)
        self.plBox.setEnabled(False)
        self.plBox.setSuffix(' W')
        row.addWidget(self.plBox)
        self.plSlider.valueChanged.connect(lambda v: (self.plBox.blockSignals(True), self.plBox.setValue(v),
                                                      self.plBox.blockSignals(False), self._pl_touched()))
        self.plBox.valueChanged.connect(lambda v: (self.plSlider.blockSignals(True), self.plSlider.setValue(int(v)),
                                                   self.plSlider.blockSignals(False), self._pl_touched()))
        self.plMax = QPushButton('Max')
        self.plMax.clicked.connect(lambda: self.plBox.setValue(self.plBox.maximum()))
        row.addWidget(self.plMax)
        cl.addLayout(row)
        row = QHBoxLayout()
        self.plInfo = label('', 'muted')
        row.addWidget(self.plInfo, 1)
        self.plPersist = QCheckBox('Apply at every logon')
        self.plPersist.setChecked(bool(backend.read_state().get('power_limit_on_logon')))
        row.addWidget(self.plPersist)
        self.plApply = QPushButton('Apply')
        self.plApply.setObjectName('primary')
        self.plApply.clicked.connect(self.apply_power_limit)
        row.addWidget(self.plApply)
        cl.addLayout(row)
        self.plTouched = False
        lay.addWidget(c)

        c, cl = card('How it works')
        cl.addWidget(label('1  Open your card\'s VBIOS in the Editor and change what you need, or pick a preset.\n'
                           '2  Save and load it: it is stored in the driver\'s registry key (the card\'s flash chip '
                           'is never written).\n'
                           '3  Restart Windows, then Verify here, and set the power limit above (or raise the Boot '
                           'power in the build so the card starts there).\n'
                           'If the screen stays black after a restart: boot into Safe Mode (power off during boot '
                           'two or three times → Troubleshoot → Startup Settings → 4) and use "Back to stock".',
                           'muted', wrap=True))
        lay.addWidget(c)
        lay.addStretch()
        self.liveTimer = QTimer(self, interval=1500, timeout=self.poll)
        self.liveTimer.start()
        self.busy = False

    def refresh(self):
        self.status.emit('Reading GPU status…')

        def work():
            gpus = backend.gpu_status()
            if not gpus:
                return None, None
            g = gpus[0]
            try:
                v = backend.gpu_verify(g['index']) if (g.get('override') or {}).get('vbios') else None
            except Exception as exc:  # verify needs a newer driver or failed
                v = dict(state='error', message=str(exc))
            return g, v
        Task(self, work, on_done=self._show, on_fail=lambda m: self.status.emit('GPU status failed: ' + m))

    def _show(self, result):
        g, v = result
        self.gpu, self.verify_report = g, v
        self.win.gpu = g
        if g is None:
            self.gpuName.setText('No NVIDIA GPU found')
            return
        self.gpuName.setText(g['name'])
        self.gpuInfo.setText(f"PCI {g.get('location')}  ·  ID {g.get('pci_id')}  ·  subsystem {g.get('subsystem')}\n"
                             f"{(g.get('family') or '').title()}  ·  driver {g.get('nvidia_driver_version')}"
                             + ('' if g.get('driver_supported') else '  (too old for the loader)'))
        state, head, detail = backend.override_state(g, v)
        self.statePill.setText({'active': 'ACTIVE', 'pending': 'PENDING', 'none': 'STOCK', 'rejected': 'NOT RUNNING',
                                'unknown': 'STORED'}[state])
        color = theme.STATE_COLORS[state]
        self.statePill.setStyleSheet(f'color: {color}; border: 1px solid {color}; border-radius: 10px; '
                                     f'padding: 2px 10px; font-weight: 600;')
        self.stateHead.setText(head)
        self.stateDetail.setText(detail)
        stored = (g.get('override') or {}).get('vbios') or {}
        prof = backend.profile_for_sha(stored.get('sha256')) if stored else None
        self.stateProfile.setText(f"Profile: <b>{prof.name}</b>" if prof else
                                  (f"Stored image sha256 {stored.get('sha256', '')[:16]}… (not a saved profile)"
                                   if stored else ''))
        self.unloadBtn.setEnabled(bool(stored))
        self.win.set_sidebar_state(state, head)
        self.win.profiles.mark(stored.get('sha256') if stored else None, state)
        self.status.emit('GPU status updated')

    def poll(self):
        if self.busy or not self.isVisible():
            return
        self.busy = True
        Task(self, backend.telemetry, on_done=self._live, on_fail=lambda m: setattr(self, 'busy', False))

    def _live(self, t):
        self.busy = False
        if not t:
            self.liveNote.setText('nvidia-smi is not available.')
            return
        f = lambda k: t.get(k, '—').split('.')[0] if t.get(k) not in (None, '[N/A]') else '—'  # noqa: E731
        self.live['power'].setText(f('power.draw'))
        self.live['limit'].setText(f"{f('power.limit')} / {f('power.max_limit')}")
        self.live['core'].setText(f('clocks.sm'))
        self.live['mem'].setText(f('clocks.mem'))
        self.live['temp'].setText(f('temperature.gpu'))
        self.live['load'].setText(f('utilization.gpu'))
        why = backend.limiters(t.get('clocks_event_reasons.active'))
        color = theme.GOOD if why and why.startswith('Nothing') else theme.MUTED if why == 'Idle' else theme.WARN
        self.limiter.setText(f"<span style='color:{color}'>{why or '—'}</span>")
        self.liveNote.setText(f"VBIOS reported by the driver: {t.get('vbios_version', '?')}   ·   power limit = "
                              f"current / highest allowed")
        try:
            lo, hi = float(t['power.min_limit']), float(t['power.max_limit'])
            now, boot = float(t['power.limit']), float(t['power.default_limit'])
        except (KeyError, ValueError):
            return
        if self.plSlider.maximum() != int(hi) or self.plSlider.minimum() != int(lo):
            for w in (self.plSlider, self.plBox):
                w.blockSignals(True)
                w.setRange(int(lo), int(hi))
                w.blockSignals(False)
            for w in (self.plSlider, self.plBox, self.plMax):
                w.setEnabled(True)
        if not self.plTouched:
            for w in (self.plSlider, self.plBox):
                w.blockSignals(True)
                w.setValue(int(now))
                w.blockSignals(False)
        persist = backend.read_state().get('power_limit_on_logon')
        self.plInfo.setText(f'Now {now:.0f} W  ·  boot value {boot:.0f} W  ·  range {lo:.0f}–{hi:.0f} W'
                            + (f'  ·  {persist} W is applied at every logon' if persist else ''))

    def _pl_touched(self):
        self.plTouched = True

    def apply_power_limit(self):
        index = self.win.gpu_index()
        if index is None:
            return
        watts = int(self.plBox.value())
        persist = self.plPersist.isChecked()
        was = bool(backend.read_state().get('power_limit_on_logon'))
        self.status.emit('Waiting for the administrator prompt…')

        def done(_):
            self.plTouched = False
            self.status.emit(f'Power limit set to {watts} W' + (', and at every logon' if persist else ''))
        Task(self, backend.set_power_limit, index, watts, True if persist else (False if was else None),
             on_done=done, on_fail=lambda m: QMessageBox.warning(self, 'Power limit', m))


# ---------------------------------------------------------------- sensors (GPU-Z style)

class SensorsPage(QWidget):
    status = Signal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName('page')
        self.reader = None
        self.stats = sensors.Stats()
        self.busy = False
        self.rows = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        top = QHBoxLayout()
        head = QVBoxLayout()
        head.addWidget(label('Sensors', 'h1'))
        head.addWidget(label('Every reading the driver exposes, live: current, minimum, maximum and average since '
                             'the last reset.', 'muted'))
        top.addLayout(head, 1)
        self.rate = QComboBox()
        for s in (1, 2, 5):
            self.rate.addItem(f'Every {s} s', s)
        self.rate.currentIndexChanged.connect(lambda _: self.timer.setInterval(self.rate.currentData() * 1000))
        top.addWidget(self.rate, 0, Qt.AlignTop)
        b = QPushButton('Reset min/max')
        b.clicked.connect(self.stats.reset)
        top.addWidget(b, 0, Qt.AlignTop)
        self.logBtn = QPushButton('Log to file')
        self.logBtn.setCheckable(True)
        self.logBtn.toggled.connect(self.toggle_log)
        top.addWidget(self.logBtn, 0, Qt.AlignTop)
        lay.addLayout(top)
        self.table = QTreeWidget()
        self.table.setColumnCount(5)
        self.table.setHeaderLabels(['Sensor', 'Current', 'Minimum', 'Maximum', 'Average'])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.header().setStretchLastSection(False)
        self.table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 5):
            self.table.header().setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, 150)
        lay.addWidget(self.table, 1)
        self.note = label('', 'muted', wrap=True)
        lay.addWidget(self.note)
        self.timer = QTimer(self, interval=1000, timeout=self.poll)
        self.timer.start()

    def poll(self):
        if self.busy or not self.isVisible() and not self.stats.log:
            return
        self.busy = True

        def work():
            if self.reader is None:
                self.reader = sensors.Reader(self.win.gpu['index'] if self.win.gpu else 0)
            return self.reader.read()
        Task(self, work, on_done=self._show, on_fail=self._fail)

    def _fail(self, message):
        self.busy = False
        self.note.setText(f'Sensors are not available: {message}')

    def _show(self, rows):
        self.busy = False
        self.stats.update(rows)
        if not self.isVisible():
            return
        if [k for _, k, _, _ in rows] != [k for k in self.rows if not k.startswith('§')]:
            self.table.clear()          # the set of sensors changed: rebuild in reading order, grouped
            self.rows = {}
            for g, k, _, _ in rows:
                if '§' + g not in self.rows:
                    head = QTreeWidgetItem([g])
                    f = head.font(0)
                    f.setBold(True)
                    head.setFont(0, f)
                    head.setForeground(0, QColor(theme.ACCENT))
                    self.table.addTopLevelItem(head)
                    self.rows['§' + g] = head
                item = QTreeWidgetItem([f'   {k}'])
                for i in range(1, 5):
                    item.setTextAlignment(i, Qt.AlignRight | Qt.AlignVCenter)
                self.table.addTopLevelItem(item)
                self.rows[k] = item
        for g, k, v, u in rows:
            d = self.stats.data[k]
            item = self.rows[k]
            fmt = lambda x: '—' if x is None else (f'{x:g} {u}'.strip() if isinstance(x, (int, float))  # noqa
                                                   else f'{x} {u}'.strip())
            avg = d['sum'] / d['n'] if d['n'] else None
            item.setText(1, fmt(v))
            if isinstance(v, (int, float)):
                item.setText(2, fmt(d['min']))
                item.setText(3, fmt(d['max']))
                item.setText(4, fmt(round(avg, 2) if avg is not None else None))
        log = self.stats.log
        self.note.setText((f"Logging to {log['path']}   ·   " if log else '') +
                          'Hot spot, memory junction, core voltage, % TDP, video/bus loads and fan RPM come from '
                          'NVAPI interfaces documented by LibreHardwareMonitor; the rest from NVIDIA NVML.')

    def toggle_log(self, on):
        if on:
            folder = backend.DATA / 'logs'
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'sensors-{time.strftime("%Y%m%d-%H%M%S")}.csv'
            self.stats.start_log(path)
            self.logBtn.setText('Stop logging')
            self.status.emit(f'Logging sensors to {path}')
        else:
            self.stats.stop_log()
            self.logBtn.setText('Log to file')


class CardPage(QWidget):
    status = Signal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName('page')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(label('Graphics card', 'h1'), 1)
        b = QPushButton('Copy')
        b.clicked.connect(lambda: QApplication.clipboard().setText(
            '\n'.join(f'{k}: {v}' for k, v in getattr(self, 'facts', []))))
        top.addWidget(b)
        self.saveBtn = QPushButton('Save BIOS…')
        self.saveBtn.clicked.connect(self.save_bios)
        top.addWidget(self.saveBtn)
        lay.addLayout(top)
        c, self.grid_lay = card()
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(10)
        self.grid_lay.addLayout(self.grid)
        lay.addWidget(c)
        self.note = label('Chip, process, die size and transistor count are published specifications looked up from '
                          'the device ID; everything else is read from the driver.', 'muted', wrap=True)
        lay.addWidget(self.note)
        lay.addStretch()
        self.loaded = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self.loaded:
            self.loaded = True
            Task(self, lambda: sensors.Reader(self.win.gpu['index'] if self.win.gpu else 0).static,
                 on_done=self._show, on_fail=lambda m: self.note.setText(f'Not available: {m}'))

    def _show(self, facts):
        self.facts = facts
        half = (len(facts) + 1) // 2
        for i, (k, v) in enumerate(facts):
            col = 0 if i < half else 2
            row = i if i < half else i - half
            self.grid.addWidget(label(k, 'muted'), row, col)
            value = label(str(v), wrap=True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.grid.addWidget(value, row, col + 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(3, 1)

    def save_bios(self):
        g = self.win.gpu
        if not g:
            return QMessageBox.warning(self, 'Save BIOS', 'No GPU found yet.')
        stored = (g.get('override') or {}).get('vbios')
        path, _ = QFileDialog.getSaveFileName(self, 'Save BIOS', str(Path.home() / 'Desktop' / 'running-vbios.rom'),
                                              'VBIOS (*.rom)')
        if not path:
            return

        def work():
            from nvidia_vbios.windows_loader.nvapi import read_vbios_image
            data = read_vbios_image(g['location'])
            Path(path).write_bytes(data)
            return len(data)
        Task(self, work, on_done=lambda n: QMessageBox.information(
            self, 'Save BIOS', f'Saved {n:,} bytes to {path}.' + (
                '\n\nAn edited VBIOS override is loaded, so this is the image the driver is running (the override), '
                'not the card\'s flash chip. For a copy of the original firmware, use the ROM you started from or '
                'remove the override first.' if stored else '\n\nThis is the image the driver is running.')),
            on_fail=lambda m: QMessageBox.warning(self, 'Save BIOS', m))


# ---------------------------------------------------------------- profiles

class ProfileItem(QFrame):
    def __init__(self, p: backend.Profile):
        super().__init__()
        self.profile = p
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)
        head = QHBoxLayout()
        head.addWidget(label(p.name, 'h2'))
        self.badge = QLabel()
        head.addWidget(self.badge)
        head.addStretch()
        head.addWidget(label(p.created.replace('T', '  '), 'muted'))
        lay.addLayout(head)
        rows = [r for r in p.summary if r['key'] in p.changes] or p.summary
        text = '   ·   '.join(f"{r['label']}: {disp(r, r['before'])} → {disp(r, r['after'])}" for r in rows[:6])
        lay.addWidget(label(text or 'No changes', 'muted', wrap=True))
        lay.addWidget(label(f"{p.product}  ·  stored image sha256 {p.stored_sha256[:16]}…", 'muted'))

    def set_badge(self, state):
        if state:
            text, color = state
            self.badge.setText(text)
            self.badge.setStyleSheet(f'color: {color}; border: 1px solid {color}; border-radius: 8px; '
                                     f'padding: 0 8px; font-size: 8pt; font-weight: 600;')
            self.badge.show()
        else:
            self.badge.hide()


class ProfilesPage(QWidget):
    status = Signal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName('page')
        self.marked = (None, None)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        top = QHBoxLayout()
        head = QVBoxLayout()
        head.addWidget(label('Profiles', 'h1'))
        head.addWidget(label(f'Saved builds live in {backend.PROFILES}', 'muted'))
        top.addLayout(head, 1)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setSpacing(4)
        self.list.itemSelectionChanged.connect(self._buttons)
        self.list.itemDoubleClicked.connect(lambda _: self.edit())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.loadBtn = QPushButton('Load to GPU')
        self.loadBtn.setObjectName('primary')
        self.loadBtn.clicked.connect(lambda: self.selected() and self.win.load_profile(self.selected()))
        row.addWidget(self.loadBtn)
        self.editBtn = QPushButton('Open in editor')
        self.editBtn.clicked.connect(self.edit)
        row.addWidget(self.editBtn)
        self.folderBtn = QPushButton('Show files')
        self.folderBtn.clicked.connect(lambda: self.selected() and os.startfile(self.selected().folder))
        row.addWidget(self.folderBtn)
        row.addStretch()
        self.archiveBtn = QPushButton('Archive')
        self.archiveBtn.clicked.connect(self.archive)
        row.addWidget(self.archiveBtn)
        lay.addLayout(row)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for p in backend.list_profiles():
            item = QListWidgetItem()
            w = ProfileItem(p)
            item.setSizeHint(w.sizeHint())
            item.setData(Qt.UserRole, p)
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
        if not self.list.count():
            item = QListWidgetItem('  No profiles yet. Build one in the Editor.')
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
        self.mark(*self.marked)
        self._buttons()

    def mark(self, sha, state):
        self.marked = (sha, state)
        for i in range(self.list.count()):
            w = self.list.itemWidget(self.list.item(i))
            if isinstance(w, ProfileItem):
                on = sha and w.profile.stored_sha256 == sha
                w.set_badge(({'active': ('RUNNING', theme.GOOD), 'pending': ('STORED · RESTART', theme.ACCENT)}
                             .get(state, ('STORED', theme.WARN))) if on else None)

    def selected(self):
        items = self.list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _buttons(self):
        on = self.selected() is not None
        for b in (self.loadBtn, self.editBtn, self.folderBtn, self.archiveBtn):
            b.setEnabled(on)

    def edit(self):
        p = self.selected()
        if p:
            self.win.go('editor')
            self.win.editor.name.setText(p.name)
            self.win.editor.open_rom(str(p.source_path), p.changes)

    def archive(self):
        p = self.selected()
        if p and QMessageBox.question(self, 'Archive profile', f'Move "{p.name}" to the archive folder?\n'
                                      f'Nothing is deleted.') == QMessageBox.Yes:
            backend.archive_profile(p)
            self.refresh()


# ---------------------------------------------------------------- window

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.gpu = None
        self.setWindowTitle('VBIOS Studio')
        self.setWindowIcon(app_icon())
        self.resize(1360, 880)
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QFrame()
        side.setObjectName('sidebar')
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 12)
        sl.setSpacing(2)
        sl.addWidget(label('VBIOS Studio', 'brand'))
        sl.addWidget(label(f'v{__version__} · edit, load, verify', 'brandSub'))
        self.stack = QStackedWidget()
        self.dashboard = Dashboard(self)
        self.editor = EditorPage(self)
        self.profiles = ProfilesPage(self)
        self.sensorsPage = SensorsPage(self)
        self.cardPage = CardPage(self)
        self.logPage, ll = page()
        ll.addWidget(label('Log', 'h1'))
        self.logView = QPlainTextEdit(readOnly=True)
        self.logView.setFont(QFont('Consolas', 9))
        ll.addWidget(self.logView, 1)
        self.pages = {'dashboard': self.dashboard, 'sensors': self.sensorsPage, 'card': self.cardPage,
                      'editor': self.editor, 'profiles': self.profiles, 'log': self.logPage}
        self.navs = {}
        for key, title in [('dashboard', 'Dashboard'), ('sensors', 'Sensors'), ('card', 'Graphics card'),
                           ('editor', 'Editor'), ('profiles', 'Profiles'), ('log', 'Log')]:
            b = QPushButton(title)
            b.setObjectName('nav')
            b.setCheckable(True)
            b.clicked.connect(lambda _, k=key: self.go(k))
            sl.addWidget(b)
            self.navs[key] = b
            self.stack.addWidget(self.pages[key])
        sl.addStretch()
        self.sideState = label('', None, wrap=True)
        self.sideState.setContentsMargins(18, 0, 18, 0)
        sl.addWidget(self.sideState)
        sl.addWidget(label('Engine: NVIDIA VBIOS Vix Editor + Windows Vix Loader by panchovix (GPL-3.0)',
                           'brandSub', wrap=True))
        h.addWidget(side)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.statusBar().setStyleSheet(f'color: {theme.MUTED};')
        for p in (self.dashboard, self.editor, self.profiles, self.sensorsPage, self.cardPage):
            p.status.connect(self.say)
        self.go('dashboard')
        self.dashboard.refresh()
        last = QSettings().value('last_rom')
        if last and Path(last).exists():
            self.editor.open_rom(last)

    def go(self, key):
        self.stack.setCurrentWidget(self.pages[key])
        for k, b in self.navs.items():
            b.setChecked(k == key)
        if key == 'log':
            try:
                self.logView.setPlainText(backend.LOG.read_text(encoding='utf-8'))
                self.logView.verticalScrollBar().setValue(self.logView.verticalScrollBar().maximum())
            except OSError:
                self.logView.setPlainText('(empty)')

    def say(self, text):
        self.statusBar().showMessage(text, 8000)

    def set_sidebar_state(self, state, head):
        color = theme.STATE_COLORS[state]
        self.sideState.setText(f"<span style='color:{color}'>●</span> {head}")

    def gpu_index(self):
        if not self.gpu:
            QMessageBox.warning(self, 'No GPU', 'No NVIDIA GPU was found yet. Refresh the dashboard.')
            return None
        return self.gpu['index']

    def confirm_load(self, name, lines):
        text = (f'Load "{name}" onto {self.gpu["name"] if self.gpu else "the GPU"}?\n\n' + '\n'.join(lines[:14]) +
                '\n\nThe copy is stored in the driver\'s registry key and applies after a Windows restart. If the '
                'screen stays black, boot into Safe Mode and use "Back to stock".')
        return QMessageBox.question(self, 'Load VBIOS', text) == QMessageBox.Yes

    def load_profile(self, p, confirmed=False):
        index = self.gpu_index()
        if index is None:
            return
        if not confirmed and not self.confirm_load(p.name, [r['label'] for r in p.summary]):
            return
        self.say('Waiting for the administrator prompt…')
        Task(self, backend.load_profile, p, index, on_done=lambda r: self._changed(f'"{p.name}" is stored.'),
             on_fail=lambda m: self._load_failed(p, m))

    def _load_failed(self, p, message):
        """Report a failed load only after checking the card: the helper may have stored it anyway."""
        def check():
            gpu = backend.gpu_status()[0]
            return ((gpu.get('override') or {}).get('vbios') or {}).get('sha256') == p.stored_sha256
        def done(stored):
            if stored:
                backend.record_load(p)
                self._changed(f'"{p.name}" is stored (the helper did not report back, but the card shows it).')
            else:
                QMessageBox.warning(self, 'Load failed', f'{message}\n\nThe card still has its previous setting.')
                self.dashboard.refresh()
        Task(self, check, on_done=done, on_fail=lambda m: QMessageBox.warning(self, 'Load failed', message))

    def unload(self):
        index = self.gpu_index()
        if index is None or QMessageBox.question(
                self, 'Back to stock', 'Remove the override so the card runs its own VBIOS again?\n'
                                       'Applies after a Windows restart.') != QMessageBox.Yes:
            return
        Task(self, backend.unload, index, on_done=lambda r: self._changed('The override is removed.'),
             on_fail=lambda m: QMessageBox.warning(self, 'Remove failed', m))

    def _changed(self, what):
        self.dashboard.refresh()
        self.go('dashboard')
        if QMessageBox.question(self, 'Restart Windows', f'{what}\n\nRestart Windows now to apply it?') == \
                QMessageBox.Yes:
            backend.restart_windows()

    def restart(self):
        if QMessageBox.question(self, 'Restart Windows', 'Restart Windows now?') == QMessageBox.Yes:
            backend.restart_windows()


def main(argv=None):
    QApplication.setOrganizationName('VBIOS Studio')
    QApplication.setApplicationName('VBIOS Studio')
    app = QApplication(sys.argv if argv is None else argv)
    app.setStyle('Fusion')
    app.setStyleSheet(theme.STYLE)
    win = Window()
    win.show()
    if os.environ.get('VBIOS_STUDIO_SCREENSHOT'):       # self-test: render pages to PNG files, then quit
        shot = Path(os.environ['VBIOS_STUDIO_SCREENSHOT'])
        if os.environ.get('VBIOS_STUDIO_PRESET'):
            def preset():
                win.editor.preset.setCurrentIndex(int(os.environ['VBIOS_STUDIO_PRESET']))
                win.editor.apply_preset()
            QTimer.singleShot(2000, preset)
        if os.environ.get('VBIOS_STUDIO_SLIDERS'):      # e.g. power_max_w=400,nvvdd_rel_equation_max_mv=1150
            def sliders():
                for item in os.environ['VBIOS_STUDIO_SLIDERS'].split(','):
                    key, value = item.split('=')
                    if key in win.editor.cards:
                        win.editor.cards[key]._user(float(value))
            QTimer.singleShot(2000, sliders)
        pages = os.environ.get('VBIOS_STUDIO_PAGES', 'dashboard,editor,profiles').split(',')

        def capture(i=0):
            if i >= len(pages):
                return app.quit()
            win.go(pages[i])
            QTimer.singleShot(2500, lambda: (win.grab().save(str(shot.with_name(f'{shot.stem}-{pages[i]}.png'))),
                                             capture(i + 1)))
        QTimer.singleShot(3500, capture)
    return app.exec()
