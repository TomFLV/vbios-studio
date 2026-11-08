"""Drive the real window offscreen: open a ROM, edit like a user, check staging, reset, and the rated/ceiling rule.
Never loads anything onto the GPU. Run: .venv\\Scripts\\python -m tests.test_ui PATH\\TO\\stock.rom [sha256 prefix]"""
import os
import sys
import tempfile

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['VBIOS_STUDIO_HOME'] = tempfile.mkdtemp(prefix='vbios-studio-ui-')
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from vbios_studio import app as studio, backend  # noqa: E402


def wait(cond, ms=8000):
    for _ in range(ms // 50):
        if cond():
            return True
        QTest.qWait(50)
    raise AssertionError('timed out')


def main(rom, expected=None):
    """expected: optional sha256 prefix of the first preset's loader payload on this ROM."""
    QApplication.setOrganizationName('VBIOS Studio Test')
    a = QApplication([])
    QSettings().setValue('last_rom', rom)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)     # never confirm a load or restart
    w = studio.Window()
    ed = w.editor
    wait(lambda: ed.doc is not None and ed.rows)
    print('rows:', len(ed.rows))
    row = ed.rows['power_max_w']
    row.editor.setValue(380)
    wait(lambda: ed.staged is not None)
    keys = {(a or b)['key']: (a or {}).get('current') for b, a in ed.staged.preview}
    assert keys == {'power_max_w': keys['power_max_w']} and float(keys['power_max_w']) == 380, keys
    assert ed.saveBtn.isEnabled() and row.property('changed') == 'true'
    print('edit power_max_w -> 380 staged, save enabled')
    rated = next((k for k in ('connector_1_power_rated_w', 'power_default_w') if k in ed.rows and ed.rows[k].editor), None)
    if rated:                                                            # a rated value above its ceiling is refused
        ed.rows[rated].editor.setRange(0, 1e6)
        ed.rows[rated].editor.setValue(float(ed.rows['power_max_w'].field['current']) + 500)
        wait(lambda: ed.staged is None and '✖' in ed.check.text())
        print(f'{rated} above its ceiling refused:', ed.check.text().split('✖')[1][:90].strip())
        ed.on_edit(rated, None)
        wait(lambda: ed.staged is not None)
    row.reset.click()
    wait(lambda: not ed.pending and ed.staged is None)
    assert not ed.saveBtn.isEnabled()
    print('reset back to ROM values')
    if 'power_max_w' in ed.cards:                                         # Simple tab sliders
        card = ed.cards['power_max_w']
        card._user(card.ctl.stock + 50)
        wait(lambda: ed.staged is not None and 'power_max_w' in ed.pending)
        followed = sorted(ed.followed.get('power_max_w', ()))
        print(f'slider power {card.ctl.stock:g} -> {card.ctl.stock + 50:g} W: valid copy; followed:', followed)
        print('   warning:', card.warning.text().split('&nbsp;')[1][:80])
        if 'nvvdd_rel_equation_max_mv' in ed.cards:
            v = ed.cards['nvvdd_rel_equation_max_mv']
            v._user(v.ctl.stock + 50)
            wait(lambda: ed.staged is not None and 'nvvdd_rel_equation_max_mv' in ed.pending)
            print(f'slider voltage +50 mV: valid; followed:', sorted(ed.followed.get('nvvdd_rel_equation_max_mv', ())))
        card._user(card.ctl.stock)
        ed.cards.get('nvvdd_rel_equation_max_mv', card)._user(ed.cards.get('nvvdd_rel_equation_max_mv', card).ctl.stock)
        wait(lambda: not ed.pending)
        print('sliders back to stock: no pending changes')
    ed.search.setText('fan')
    visible = [k for k, r in ed.rows.items() if not r.isHidden()]
    assert visible and all('fan' in k or 'fan' in r.title.text().lower() for k, r in ed.rows.items() if not r.isHidden())
    print('search "fan":', len(visible), 'rows')
    ed.search.setText('')
    ed.preset.setCurrentIndex(1)
    ed.apply_preset()
    wait(lambda: ed.staged is not None)
    want = {k: str(v) for k, v in ed.preset.currentData().items()}
    assert ed.pending == want, f'pending differs from the preset: {set(ed.pending.items()) ^ set(want.items())}'
    p = backend.build_profile(ed.staged, ed.name.text(), rom)
    if expected:
        assert p.stored_sha256.startswith(expected), p.stored_sha256
    w.profiles.refresh()
    assert w.profiles.list.count() == 1
    print(f'preset "{p.name}" built via the window (stored sha {p.stored_sha256[:16]}'
          f'{", as expected" if expected else ""}); profiles:', w.profiles.list.count())
    w.dashboard.refresh()
    wait(lambda: w.gpu is not None)
    print('dashboard:', w.dashboard.statePill.text(), '|', w.dashboard.stateHead.text())
    print('UI OK')


if __name__ == '__main__':
    main(*sys.argv[1:])
