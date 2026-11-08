"""Real UAC round trip from a worker thread (as the window does it), as a DRY RUN: nothing is written.
Shows one administrator prompt. Run: .venv\\Scripts\\python -m tests.test_elevated"""
import threading
import time

from vbios_studio import backend


def main():
    p = backend.list_profiles()[0]
    out = {}

    def work():
        t = time.time()
        try:
            out['report'] = backend.run_elevated(dict(action='load', payload=str(p.loader_path), gpu=0, dry_run=True))
        except Exception as exc:
            out['error'] = str(exc)
        out['secs'] = round(time.time() - t, 1)
    th = threading.Thread(target=work)
    th.start()
    th.join()
    if 'error' in out:
        raise SystemExit(f"FAILED after {out['secs']} s: {out['error']}")
    r = out['report']
    print(f"elevated dry run OK after {out['secs']} s: would {[s['op'] + ' ' + s['name'] for s in r['steps']]} "
          f"for {p.name}; dry_run={r['dry_run']}")


if __name__ == '__main__':
    main()
