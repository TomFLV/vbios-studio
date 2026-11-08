"""Elevated helper: python -m vbios_studio.elevated REQUEST.json RESULT.json (started through UAC by backend)."""
import json
import sys
import traceback
from pathlib import Path

from . import ROOT  # noqa: F401  (vendor on sys.path)


TASK = 'VBIOS Studio power limit'


def power_limit(gpu, watts, persist):
    """Set the board power limit now (nvidia-smi -pl, needs admin) and, if persist, at every logon through a
    scheduled task that runs as SYSTEM with highest privileges; persist False removes that task."""
    import shutil
    import subprocess
    smi = shutil.which('nvidia-smi') or r'C:\Windows\System32\nvidia-smi.exe'
    run = lambda args: subprocess.run(args, capture_output=True, text=True, creationflags=0x08000000)  # noqa: E731
    r = run([smi, '-i', str(gpu), '-pl', str(watts)])
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr).strip() or f'nvidia-smi failed ({r.returncode})')
    out = dict(set_now=(r.stdout or '').strip(), task=None)
    if persist:
        t = run(['schtasks', '/create', '/f', '/tn', TASK, '/sc', 'onlogon', '/ru', 'SYSTEM', '/rl', 'highest',
                 '/delay', '0000:20', '/tr', f'"{smi}" -i {gpu} -pl {watts}'])
        if t.returncode != 0:
            raise RuntimeError('The limit is set now, but the logon task failed: ' + (t.stdout + t.stderr).strip())
        out['task'] = 'created'
    elif persist is False:
        run(['schtasks', '/delete', '/f', '/tn', TASK])
        out['task'] = 'removed'
    return out


def main(request_path, result_path):
    try:
        req = json.loads(Path(request_path).read_text(encoding='utf-8'))
        from nvidia_vbios.windows_loader import operations
        from nvidia_vbios.windows_loader.win32 import Win32Platform
        platform = Win32Platform()
        if req['action'] == 'load':
            report = operations.load(platform, Path(req['payload']).read_bytes(), gpu=req['gpu'],
                                     dry_run=req.get('dry_run', False))
        elif req['action'] == 'unload':
            report = operations.unload(platform, gpu=req['gpu'], dry_run=req.get('dry_run', False))
        elif req['action'] == 'power_limit':
            report = power_limit(req['gpu'], req['watts'], req.get('persist'))
        else:
            raise ValueError(f"unknown action {req['action']}")
        result = dict(ok=True, report=report)
    except Exception as exc:  # reported to the window, which shows it
        result = dict(ok=False, error=str(exc) or type(exc).__name__, trace=traceback.format_exc())
    # write next to the target and rename, so the window never reads a half-written file
    tmp = Path(result_path).with_suffix('.partial')
    tmp.write_text(json.dumps(result, default=str), encoding='utf-8')
    tmp.replace(result_path)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
