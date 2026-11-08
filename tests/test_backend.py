"""Backend checks against a real ROM (read-only for the GPU: status and verify only, never load/unload).
Run: .venv\\Scripts\\python -m tests.test_backend PATH\\TO\\stock.rom [expected stored sha256 prefix of the last preset]"""
import os
import sys
import tempfile

os.environ['VBIOS_STUDIO_HOME'] = tempfile.mkdtemp(prefix='vbios-studio-test-')
from vbios_studio import backend  # noqa: E402


def main(rom, expected=None):
    doc = backend.open_rom(rom)
    fields = backend.fields_of(doc)
    print('product:', doc.report['architecture']['product'], '| fields:', len(fields),
          '| editable:', sum(1 for f in fields if f['editable']))
    name, changes = backend.presets()[-1]
    staged = backend.stage(doc, {k: str(v) for k, v in changes.items()})
    rows = backend.summary_rows(staged)
    print(f'preset "{name}": staged changes:', len(rows), [r['key'] for r in rows])
    p = backend.build_profile(staged, 'preset test', rom)
    print('profile:', p.folder.name, '| loader:', p.loader, '| stored sha:', p.stored_sha256[:16])
    if expected:
        assert p.stored_sha256.startswith(expected), 'rebuilt payload differs from the expected one'
        print('MATCHES the expected payload')
    assert [x.name for x in backend.list_profiles()] == ['preset test']
    try:
        backend.stage(doc, {'power_max_w': 'abc'})
        raise AssertionError('bad value accepted')
    except Exception as exc:
        print('bad value refused:', type(exc).__name__, str(exc)[:80])
    gpus = backend.gpu_status()
    v = backend.gpu_verify(gpus[0]['index'])
    print('gpu:', gpus[0]['name'], '| verify:', v['state'], '| dashboard:', backend.override_state(gpus[0], v)[:2])
    print('telemetry:', backend.telemetry())
    from vbios_studio import sensors
    r = sensors.Reader(gpus[0]['index'])
    rows = r.read()
    names = {k for _, k, _, _ in rows}
    print(f'sensors: {len(rows)} readings, card sheet: {len(r.static)} facts; NVAPI extras:',
          sorted(names & {'Hot spot', 'Memory junction', 'GPU core voltage', 'Power consumption (% TDP)'}))
    assert {'GPU clock', 'GPU temperature', 'Board power draw', 'GPU load'} <= names
    print('OK')


if __name__ == '__main__':
    main(*sys.argv[1:])
