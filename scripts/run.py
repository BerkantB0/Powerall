#!/usr/bin/env python3
"""Launch the local API and Next.js interface together, without exporting secrets."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from powerall.server import load_env

def main():
    production = '--production' in sys.argv
    if not (ROOT / 'dashboard/node_modules').exists():
        print('Frontend dependencies are missing. Run: cd dashboard && npm ci')
        return 1
    config = load_env(ROOT / '.env')
    if production and not (ROOT / 'dashboard/.next/BUILD_ID').exists():
        print('Production build is missing. Run: cd dashboard && npm run build')
        return 1
    environment = os.environ.copy()
    # Never send Bright credentials to the frontend process, even if inherited.
    environment.pop('BRIGHT_USERNAME', None)
    environment.pop('BRIGHT_PASSWORD', None)
    environment['POWERALL_PORT'] = config.get('POWERALL_PORT', '8765')
    environment['NEXT_TELEMETRY_DISABLED'] = '1'
    children = []
    def stop(*_):
        for child in children:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        children.append(subprocess.Popen([sys.executable, '-m', 'powerall.server'], cwd=ROOT, start_new_session=True))
        command = ['npm', 'run', 'start'] if production else ['npm', 'run', 'dev']
        children.append(subprocess.Popen(command, cwd=ROOT / 'dashboard', env=environment, start_new_session=True))
        print('Powerall is starting. Open http://localhost:5173', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        code = next((child.returncode for child in children if child.returncode is not None), 0)
        return code or 0
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)

if __name__ == '__main__':
    sys.exit(main())
