"""Loopback-only, same-origin API for a single local household."""
import argparse
import json
import os
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .analytics import dashboard
from .bright import Bright
from .storage import Store
from .sync import SyncService
from .tariffs import TariffError, manual_tariff
from .timeutils import UK, iso
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent

def load_env(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, separator, value = line.partition('=')
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        values[key.strip()] = value
    return values

def make_handler(store, sync, token, port):
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}', 'http://localhost:5173', 'http://127.0.0.1:5173'}
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Powerall'
        def log_message(self, *_):
            pass  # Avoid URLs, account metadata and provider response bodies in logs.

        def send(self, status, payload):
            content = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def guard(self, mutation=False):
            if self.headers.get('Host') not in hosts:
                self.send(403, {'error': 'Local access only.'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in origins:
                self.send(403, {'error': 'Origin refused.'})
                return False
            if mutation and not secrets.compare_digest(self.headers.get('X-Powerall-Token', ''), token):
                self.send(403, {'error': 'Session expired. Reload the dashboard.'})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            route = urlsplit(self.path)
            query = parse_qs(route.query)
            try:
                if route.path == '/api/status':
                    state = store.sync_state()
                    self.send(200, {'meters': store.meters(), 'sync': state, 'csrf': token,
                        'today': datetime.now(UK).date().isoformat(), 'timezone': 'Europe/London'})
                elif route.path == '/api/dashboard':
                    meter = query.get('meter', [''])[0]
                    if not any(m['id'] == meter for m in store.meters()):
                        self.send(404, {'error': 'Select a discovered meter.'})
                        return
                    view, anchor = query.get('view', ['day'])[0], query.get('date', [datetime.now(UK).date().isoformat()])[0]
                    # Bound requests so malformed clients cannot exhaust memory.
                    if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', anchor):
                        raise ValueError('Choose a date between 2000 and 2099.')
                    self.send(200, dashboard(store, meter, view, anchor, time.time()))
                elif route.path == '/api/tariffs':
                    meter = query.get('meter', [''])[0]
                    items = store.tariffs(meter)
                    for item in items:
                        item['from_utc'] = iso(item['effective_from'])
                        item['to_utc'] = iso(item['effective_to']) if item['effective_to'] else None
                    self.send(200, {'tariffs': items})
                else:
                    self.send(404, {'error': 'Endpoint not found.'})
            except ValueError as error:
                self.send(400, {'error': str(error)})
            except Exception:
                self.send(500, {'error': 'The local data could not be loaded.'})

        def payload(self):
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Send JSON data.')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                raise ValueError('Request body is empty or too large.')
            item = json.loads(self.rfile.read(length))
            if not isinstance(item, dict):
                raise ValueError('Send a JSON object.')
            return item

        def do_POST(self):
            if not self.guard(True):
                return
            try:
                payload = self.payload()
                route = urlsplit(self.path).path
                if route == '/api/sync':
                    days = payload.get('days')
                    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 730):
                        raise ValueError('Import range must be between 1 and 730 days.')
                    accepted = sync.trigger(days)
                    self.send(202 if accepted else 409, {'started': accepted, 'message': 'Import started.' if accepted else 'An import is already running.'})
                elif route == '/api/tariffs':
                    meter = payload.get('meter')
                    if not any(m['id'] == meter for m in store.meters()):
                        raise ValueError('Select a discovered meter.')
                    item = manual_tariff(payload, meter)
                    editing = payload.get('id') is not None
                    if editing:
                        matches = store.rows("SELECT id FROM tariffs WHERE id=? AND meter_id=? AND source='manual'", (payload['id'], meter))
                        if not matches:
                            self.send(404, {'error': 'Manual tariff not found for this meter.'})
                            return
                        item['id'] = payload['id']
                    # Reject overlapping manual entries rather than silently changing costs.
                    for existing in store.tariffs(meter):
                        if existing['id'] != item['id'] and existing['source'] == 'manual' and item['effective_from'] < (existing['effective_to'] or float('inf')) and existing['effective_from'] < (item['effective_to'] or float('inf')):
                            raise ValueError('This overlaps another manual tariff. Adjust the effective dates of either entry.')
                    store.upsert_tariff(item)
                    self.send(200 if editing else 201, {'saved': True, 'id': item['id']})
                elif route == '/api/tariffs/delete':
                    tariff_id = payload.get('id')
                    matches = store.rows("SELECT id FROM tariffs WHERE id=? AND source='manual'", (tariff_id,))
                    if not matches:
                        self.send(404, {'error': 'Manual tariff not found.'})
                        return
                    store.execute("DELETE FROM tariffs WHERE id=? AND source='manual'", (tariff_id,))
                    self.send(200, {'deleted': True})
                else:
                    self.send(404, {'error': 'Endpoint not found.'})
            except (ValueError, TariffError) as error:
                self.send(400, {'error': str(error)})
            except Exception:
                self.send(500, {'error': 'The request could not be completed.'})
    return Handler

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-auto-sync', action='store_true')
    args = parser.parse_args()
    config = load_env(ROOT / '.env')
    try:
        port = args.port or int(config.get('POWERALL_PORT', 8765))
        initial_days = int(config.get('POWERALL_IMPORT_DAYS', 90))
        refresh_hours = float(config.get('POWERALL_REFRESH_HOURS', 6))
        if not 1024 <= port <= 65535 or not 1 <= initial_days <= 730 or not 0 < refresh_hours <= 168:
            raise ValueError()
    except (ValueError, TypeError):
        parser.error('Use port 1024–65535, import days 1–730 and refresh hours greater than zero and at most 168.')
    store = Store(ROOT / 'data' / 'powerall.sqlite3')
    provider = Bright(os.environ.get('BRIGHT_USERNAME', config.get('BRIGHT_USERNAME')), os.environ.get('BRIGHT_PASSWORD', config.get('BRIGHT_PASSWORD')))
    sync = SyncService(store, provider, initial_days, refresh_hours)
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(store, sync, secrets.token_urlsafe(32), port))
    print(f'Powerall data service: http://127.0.0.1:{port}', flush=True)
    if not args.no_auto_sync:
        sync.schedule()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sync.stop()
        server.server_close()

if __name__ == '__main__':
    main()
