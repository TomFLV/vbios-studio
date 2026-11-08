"""Everything the window does that is not drawing: ROM documents, profiles, GPU status, verify, elevated load/unload.

ROM work goes through the vendored editor controller (nvidia_vbios.gui.controller, GTK-free) and GPU work through
the vendored Windows loader (nvidia_vbios.windows_loader.operations), so every check is the upstream one.
"""
from __future__ import annotations
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import ROOT  # noqa: F401  (puts vendor/ on sys.path)
from nvidia_vbios import editor
from nvidia_vbios.gui import controller
from nvidia_vbios.windows_loader import operations
from nvidia_vbios.windows_loader.image import extract_chain

DATA = Path(os.environ.get('VBIOS_STUDIO_HOME', Path.home() / 'Documents' / 'VBIOS Studio'))
PROFILES = DATA / 'profiles'
ARCHIVE = PROFILES / '_archive'
STATE = DATA / 'state.json'
LOG = DATA / 'studio.log'
PRESETS = Path(__file__).resolve().parent / 'presets'
NO_WINDOW = 0x08000000


def log(line):
    DATA.mkdir(parents=True, exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def boot_time() -> dt.datetime:
    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    return dt.datetime.now() - dt.timedelta(milliseconds=ctypes.windll.kernel32.GetTickCount64())


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def write_state(state: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')


# ---------------------------------------------------------------- ROM documents (editor controller)

def open_rom(path):
    return controller.open_document(Path(path))


def stage(document, changes):
    """Validated proposal for these changes (upstream plan); raises editor.VbiosError with the reason."""
    return controller.stage_document(document, changes)


def fields_of(document):
    return controller.controls(document.report)


def stored_chain_sha(loader_bin: Path, pci_device: str) -> str:
    """sha256 of the bytes the loader stores in the registry (and verify reads back) for this payload file."""
    vendor, device = (int(x, 16) for x in pci_device.split(':')[:2])
    return sha256(extract_chain(Path(loader_bin).read_bytes(), vendor, device).data)


# ---------------------------------------------------------------- profiles

@dataclass
class Profile:
    folder: Path
    name: str
    created: str
    changes: dict
    summary: list = field(default_factory=list)
    rom: str = ''
    loader: str = ''
    source: str = ''
    stored_sha256: str = ''
    pci_device: str = ''
    product: str = ''

    @property
    def loader_path(self):
        return self.folder / self.loader

    @property
    def source_path(self):
        return self.folder / self.source


def slug(name):
    return re.sub(r'[^A-Za-z0-9._ -]+', '', name).strip().replace(' ', '-') or 'profile'


def list_profiles():
    out = []
    if PROFILES.exists():
        for meta in sorted(PROFILES.glob('*/profile.json')):
            if meta.parent.parent != PROFILES:
                continue
            try:
                d = json.loads(meta.read_text(encoding='utf-8-sig'))
                out.append(Profile(meta.parent, **{k: d.get(k, v) for k, v in Profile(meta.parent, '', '', {}).__dict__.items()
                                                    if k != 'folder'}))
            except (OSError, ValueError, TypeError):
                continue
    return sorted(out, key=lambda p: p.created, reverse=True)


def _write_profile(p: Profile):
    d = {k: v for k, v in p.__dict__.items() if k != 'folder'}
    (p.folder / 'profile.json').write_text(json.dumps(d, indent=2), encoding='utf-8')


def summary_rows(document):
    rows = []
    for before, after in document.preview:
        f = after or before
        rows.append(dict(key=f['key'], label=f.get('label', f['key']),
                         before=None if before is None else before.get('current'),
                         after=None if after is None else after.get('current'),
                         units=f.get('units', '')))
    return rows


def build_profile(document, name, source_path) -> Profile:
    """Save the staged copy as a new profile folder: edited ROM, loader payload (+manifest), source ROM, changes."""
    if document.proposal is None:
        raise editor.VbiosError('INVALID_CHANGES', 'No changes are staged.')
    PROFILES.mkdir(parents=True, exist_ok=True)
    folder = PROFILES / f'{dt.datetime.now():%Y%m%d-%H%M%S}-{slug(name)}'
    folder.mkdir()
    base = slug(name)
    saved = controller.save_document(document, folder / f'{base}.rom').last_saved
    loader = saved.get('loader') or {}
    if 'output' not in loader:
        raise editor.VbiosError('LOADER_FAILED', loader.get('error', 'no loader payload for this GPU family'))
    shutil.copy2(source_path, folder / 'source.rom')
    (folder / 'changes.json').write_text(json.dumps(document.changes, indent=2), encoding='utf-8')
    pci = document.report.get('pci_device', '')
    p = Profile(folder, name, dt.datetime.now().isoformat(timespec='seconds'), dict(document.changes),
                [dict(r, before=_plain(r['before']), after=_plain(r['after'])) for r in summary_rows(document)],
                rom=f'{base}.rom', loader=Path(loader['output']).name, source='source.rom',
                stored_sha256=stored_chain_sha(Path(loader['output']), pci), pci_device=pci,
                product=(document.report.get('architecture') or {}).get('product', ''))
    _write_profile(p)
    log(f'built profile "{name}" in {folder} (stored sha {p.stored_sha256[:16]})')
    return p


def import_build(name, source_rom, changes: dict, created=None) -> Profile:
    """Rebuild an existing change set from its source ROM as a profile (same upstream steps as the editor)."""
    doc = stage(open_rom(source_rom), {k: str(v) for k, v in changes.items()})
    p = build_profile(doc, name, source_rom)
    if created:
        p.created = created
        _write_profile(p)
    return p


def archive_profile(p: Profile):
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    target = ARCHIVE / p.folder.name
    shutil.move(str(p.folder), str(target))
    (target / 'NOTE.md').write_text(f'Archived from VBIOS Studio on {dt.datetime.now():%Y-%m-%d %H:%M}.\n',
                                    encoding='utf-8')
    log(f'archived profile "{p.name}" to {target}')


def presets():
    out = [('Stock (no changes)', {})]
    for f in sorted(PRESETS.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))      # hand-edited files may carry a BOM
            out.append((d.get('name', f.stem), d.get('changes', {})))
        except (OSError, ValueError):
            continue
    return out


def _plain(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------- GPU (loader)

def _platform():
    from nvidia_vbios.windows_loader.win32 import Win32Platform
    return Win32Platform()


def gpu_status():
    return operations.status(_platform())


def gpu_verify(index):
    return operations.verify(_platform(), gpu=index)


def override_state(gpu: dict, verify_report: dict | None):
    """(state id, headline, detail) of a GPU's override, for the dashboard."""
    stored = (gpu.get('override') or {}).get('vbios')
    if not stored:
        return 'none', 'Stock VBIOS', 'No override is stored. The card runs its own firmware.'
    sha = stored.get('sha256') or ''
    st = read_state().get('last_load') or {}
    if verify_report and verify_report.get('state') == 'override-active':
        return 'active', 'Edited VBIOS active', 'The driver is running the stored override (verified byte by byte).'
    if verify_report and verify_report.get('state') == 'unstable':
        return 'unknown', 'Verify unstable', 'The two reads differed. Verify again.'
    if st.get('sha256') == sha and st.get('time') and dt.datetime.fromisoformat(st['time']) > boot_time():
        return 'pending', 'Restart to apply', 'The override is stored. Windows applies it at the next restart.'
    if verify_report and verify_report.get('state') == 'override-not-active':
        return 'rejected', 'Stored but not running', ('The override is stored, but the driver runs a different '
                                                      'image. Restart if you just loaded it; otherwise the driver '
                                                      'refused it.')
    return 'unknown', 'Override stored', 'Verify to see whether the driver is running it.'


def profile_for_sha(sha):
    return next((p for p in list_profiles() if p.stored_sha256 and p.stored_sha256 == sha), None)


def run_elevated(request: dict, timeout=120) -> dict:
    """Run one load/unload in an elevated helper process (UAC prompt) and return its JSON result."""
    # Not tempfile.mkdtemp: its folder is restricted to the current (non-elevated) token, so a file the elevated
    # helper writes there gets an owner this process cannot read. A plain folder under DATA inherits normal access.
    tmp = DATA / 'ipc' / f'{dt.datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-{time.monotonic_ns() % 100000}'
    tmp.mkdir(parents=True)
    req, res = tmp / 'request.json', tmp / 'result.json'
    req.write_text(json.dumps(request), encoding='utf-8')
    exe = Path(sys.executable)
    if getattr(sys, 'frozen', False):           # the portable exe runs its own helper mode
        params, folder = f'--elevated "{req}" "{res}"', str(exe.parent)
    else:
        if exe.name.lower() == 'pythonw.exe':
            exe = exe.with_name('python.exe')
        params, folder = f'-m vbios_studio.elevated "{req}" "{res}"', str(ROOT)

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_ulong), ('fMask', ctypes.c_ulong), ('hwnd', ctypes.c_void_p),
                    ('lpVerb', ctypes.c_wchar_p), ('lpFile', ctypes.c_wchar_p), ('lpParameters', ctypes.c_wchar_p),
                    ('lpDirectory', ctypes.c_wchar_p), ('nShow', ctypes.c_int), ('hInstApp', ctypes.c_void_p),
                    ('lpIDList', ctypes.c_void_p), ('lpClass', ctypes.c_wchar_p), ('hkeyClass', ctypes.c_void_p),
                    ('dwHotKey', ctypes.c_ulong), ('hIcon', ctypes.c_void_p), ('hProcess', ctypes.c_void_p)]
    # NOCLOSEPROCESS (0x40) for a handle, NOASYNC (0x100) because this runs on a worker thread without a message
    # loop. Elevation can still come back without a process handle, so the result file is what we wait for: the
    # helper writes it atomically when it is done, and the folder is removed only after that (or the timeout).
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)
    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=0x40 | 0x100, lpVerb='runas',
                             lpFile=str(exe), lpParameters=params, lpDirectory=folder, nShow=0)
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        shutil.rmtree(tmp, ignore_errors=True)
        if ctypes.get_last_error() == 1223:         # ERROR_CANCELLED
            raise RuntimeError('The administrator prompt was cancelled. Nothing was changed.')
        raise RuntimeError(f'Could not start the administrator helper (error {ctypes.get_last_error()}).')
    deadline = time.monotonic() + timeout
    try:
        if info.hProcess:
            kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000))
            kernel32.CloseHandle(info.hProcess)
        while not res.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        try:
            result = json.loads(res.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            log(f'elevated helper result unreadable: {type(exc).__name__}: {exc}; folder: '
                f'{sorted(p.name for p in tmp.iterdir()) if tmp.exists() else "gone"}')
            raise RuntimeError('The administrator helper did not report back in time. Check the Dashboard: '
                               'the change may have been applied anyway.')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not result.get('ok'):
        raise RuntimeError(result.get('error', 'operation failed'))
    return result['report']


def record_load(p: Profile, when=None):
    write_state(dict(read_state(), last_load=dict(sha256=p.stored_sha256, profile=p.name,
                                                  time=(when or dt.datetime.now()).isoformat(timespec='seconds'))))
    log(f'loaded profile "{p.name}" (restart to apply)')


def load_profile(p: Profile, gpu_index: int, dry_run=False):
    report = run_elevated(dict(action='load', payload=str(p.loader_path), gpu=gpu_index, dry_run=dry_run))
    if not dry_run:
        record_load(p)
    return report


def unload(gpu_index: int):
    report = run_elevated(dict(action='unload', gpu=gpu_index, dry_run=False))
    write_state(dict(read_state(), last_load=dict(sha256='', profile='stock',
                                                  time=dt.datetime.now().isoformat(timespec='seconds'))))
    log(f'removed the override on GPU {gpu_index} (restart to apply)')
    return report


def restart_windows():
    log('restart requested')
    subprocess.run(['shutdown', '/r', '/t', '5', '/c', 'VBIOS Studio: restarting to apply the VBIOS change'],
                   creationflags=NO_WINDOW, check=False)


# ---------------------------------------------------------------- live telemetry

SMI_FIELDS = ('power.draw', 'power.limit', 'power.max_limit', 'clocks.sm', 'clocks.mem', 'temperature.gpu',
              'utilization.gpu', 'vbios_version', 'power.min_limit', 'power.default_limit',
              'clocks_event_reasons.active')

# clocks_event_reasons bits (NVML), in the order a tuner cares about; GPU-Z's PerfCap names in brackets
LIMITERS = ((0x4, 'Power limit (Pwr)'), (0x400, 'Voltage ceiling (VRel)'), (0x80, 'Power brake'),
            (0x40, 'Temperature (HW thermal)'), (0x20, 'Temperature (SW thermal)'), (0x8, 'Hardware slowdown'),
            (0x100, 'Display clock'), (0x2, 'Application clocks'), (0x10, 'Sync boost'))


def limiters(mask_text):
    """Readable reasons from the clocks_event_reasons.active bitmask ('Idle' when only idle, '' when none)."""
    try:
        mask = int(str(mask_text), 16)
    except (TypeError, ValueError):
        return None
    names = [name for bit, name in LIMITERS if mask & bit]
    if names:
        return ', '.join(names)
    return 'Idle' if mask & 0x1 else 'Nothing (free to boost)'


def set_power_limit(gpu_index: int, watts: int, persist):
    """Set the board power limit now and optionally at every logon (one administrator prompt)."""
    report = run_elevated(dict(action='power_limit', gpu=gpu_index, watts=int(watts), persist=persist))
    if persist is not None:
        write_state(dict(read_state(), power_limit_on_logon=int(watts) if persist else None))
    log(f'power limit {watts} W on GPU {gpu_index}' + (' (applied at every logon)' if persist else
                                                       ' (logon task removed)' if persist is False else ''))
    return report


def telemetry():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=' + ','.join(SMI_FIELDS), '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = out.strip().splitlines()[0] if out.strip() else ''
    vals = [v.strip() for v in line.split(',')]
    return dict(zip(SMI_FIELDS, vals)) if len(vals) == len(SMI_FIELDS) else None
