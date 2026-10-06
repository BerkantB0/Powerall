"""Loopback-only, same-origin API for a single local household."""
import argparse
import json
import os
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from io import BytesIO
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

def make_api(store, sync, token, port, hosted=None):
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}', 'http://localhost:5173', 'http://127.0.0.1:5173'}
    class API:
        def __init__(self, method, path, headers, body):
            self.method, self.path, self.headers = method, path, headers
            self.rfile = BytesIO(body)
            self.store, self.sync, self.token = store, sync, token
            self.session = None
            self.cookie_token = ""
            self.response_headers = {}
            self.result = (500, {'error': 'The request could not be completed.'})

        def send(self, status, payload):
            self.result = (status, payload)

        def handle(self):
            if self.method == 'GET': self.do_GET()
            elif self.method == 'POST': self.do_POST()
            else: self.send(405, {'error': 'Method not allowed.'})
            return self.result

        def cookie(self, value, clear=False):
            from .auth import COOKIE, SESSION_SECONDS
            self.response_headers['Set-Cookie'] = f'{COOKIE}={value}; Path=/; Max-Age={0 if clear else SESSION_SECONDS}; Secure; HttpOnly; SameSite=Lax'

        def authentication(self, route, payload):
            accounts = hosted['accounts']
            client = self.headers.get('X-Powerall-Client-IP', 'unknown')[:100]
            if route in ('/api/auth/login', '/api/auth/register'):
                if route.endswith('register'):
                    value = accounts.register(payload.get('username'), payload.get('password'), payload.get('invite'), client)
                else:
                    value = accounts.login(payload.get('username'), payload.get('password'), client)
                # Prevent old browser sessions surviving an explicit account switch.
                if self.cookie_token: accounts.logout(self.cookie_token)
                self.cookie(value); self.send(200, {'authenticated': True})
            elif route == '/api/auth/logout':
                accounts.logout(self.cookie_token)
                self.cookie('', True); self.send(200, {'signed_out': True})
            elif route == '/api/auth/password':
                value = accounts.change_password(self.session['user_id'], payload.get('password'), payload.get('new_password'), client)
                self.cookie(value); self.send(200, {'changed': True})
            elif route == '/api/account/delete':
                accounts.verify_password(self.session['user_id'], payload.get('password'), client)
                hosted['tenants'].disconnect(self.session['user_id'], delete=True)
                self.cookie('', True); self.send(200, {'deleted': True})
            elif route == '/api/connection':
                if payload.get('disconnect') is not True and not hosted['bright_enabled']:
                    self.send(403, {'error': 'Bright connections are currently paused by the operator.'}); return
                if payload.get('disconnect') is True:
                    hosted['tenants'].disconnect(self.session['user_id'])
                    self.send(200, {'connected': False})
                else:
                    if not self.sync._lock.acquire(blocking=False):
                        self.send(409, {'error': 'Wait for the current import to finish before updating credentials.'}); return
                    try:
                        accounts.set_credentials(self.session['user_id'], payload.get('username'), payload.get('password'))
                        self.sync.provider.clear()
                    finally: self.sync._lock.release()
                    self.sync.trigger()
                    if hosted['tenants'].automatic: self.sync.schedule()
                    self.send(202, {'connected': True, 'message': 'Connection saved. Import queued.' if hasattr(accounts, 'database') else 'Connection saved. Import started.'})
            else: self.send(404, {'error': 'Endpoint not found.'})

        def guard(self, mutation=False):
            if hosted:
                from .auth import COOKIE
                if not secrets.compare_digest(self.headers.get('X-Powerall-Internal', ''), hosted['internal_token']):
                    self.send(403, {'error': 'Access refused.'}); return False
                origin = self.headers.get('Origin')
                if (mutation and origin != hosted['origin']) or (origin and origin != hosted['origin']):
                    self.send(403, {'error': 'Origin refused.'}); return False
                if mutation and self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'):
                    self.send(403, {'error': 'Cross-site request refused.'}); return False
                cookies = SimpleCookie()
                try: cookies.load(self.headers.get('Cookie', ''))
                except Exception: pass
                self.cookie_token = cookies[COOKIE].value if COOKIE in cookies else ''
                route = urlsplit(self.path).path
                if not mutation and route == '/api/config': return True
                if mutation and route in ('/api/auth/login', '/api/auth/register'):
                    return True
                self.session = hosted['accounts'].session(self.cookie_token)
                if not self.session:
                    self.send(401, {'error': 'Sign in to continue.'}); return False
                self.store, self.sync = hosted['tenants'].get(self.session['user_id'])
                self.token = self.session['csrf']
            else:
                if self.headers.get('Host') not in hosts:
                    self.send(403, {'error': 'Local access only.'}); return False
                origin = self.headers.get('Origin')
                if origin and origin not in origins:
                    self.send(403, {'error': 'Origin refused.'}); return False
            if mutation and not secrets.compare_digest(self.headers.get('X-Powerall-Token', ''), self.token):
                self.send(403, {'error': 'Session expired. Reload the dashboard.'}); return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            route = urlsplit(self.path)
            query = parse_qs(route.query)
            try:
                if hosted and route.path == '/api/config':
                    self.send(200, hosted['privacy'])
                elif hosted and route.path == '/api/account/export':
                    with hosted['accounts'].db() as db:
                        user = db.execute('SELECT username,created FROM users WHERE id=?', (self.session['user_id'],)).fetchone()
                    self.send(200, {'account': dict(user), 'timezone': 'Europe/London', 'meters': self.store.meters(),
                        'readings': self.store.rows('SELECT meter_id,start,kwh FROM readings ORDER BY meter_id,start'),
                        'tariffs': [t for m in self.store.meters() for t in self.store.tariffs(m['id'])]})
                elif route.path == '/api/status':
                    state = self.store.sync_state()
                    self.send(200, {'meters': self.store.meters(), 'sync': state, 'csrf': self.token,
                        'today': datetime.now(UK).date().isoformat(), 'timezone': 'Europe/London', 'hosted': bool(hosted), **({'username': self.session['username']} if self.session else {}), 'connected': bool(self.session['connected']) if self.session else True, 'provider_enabled': hosted['bright_enabled'] if hosted else True})
                elif route.path == '/api/dashboard':
                    meter = query.get('meter', [''])[0]
                    if not any(m['id'] == meter for m in self.store.meters()):
                        self.send(404, {'error': 'Select a discovered meter.'})
                        return
                    view, anchor = query.get('view', ['day'])[0], query.get('date', [datetime.now(UK).date().isoformat()])[0]
                    # Bound requests so malformed clients cannot exhaust memory.
                    if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', anchor):
                        raise ValueError('Choose a date between 2000 and 2099.')
                    self.send(200, dashboard(self.store, meter, view, anchor, time.time()))
                elif route.path == '/api/tariffs':
                    meter = query.get('meter', [''])[0]
                    items = self.store.tariffs(meter)
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
            mutation_lock = None
            try:
                payload = self.payload()
                route = urlsplit(self.path).path
                if hosted and hasattr(hosted['accounts'], 'database') and route in ('/api/tariffs','/api/tariffs/delete'):
                    mutation_lock = self.sync._lock
                    if not mutation_lock.acquire():
                        mutation_lock = None
                        self.send(409, {'error': 'An import or tariff update is in progress. Try again shortly.'}); return
                if hosted and (route.startswith('/api/auth/') or route in ('/api/connection', '/api/account/delete')):
                    self.authentication(route, payload)
                elif route == '/api/sync':
                    days = payload.get('days')
                    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 730):
                        raise ValueError('Import range must be between 1 and 730 days.')
                    if hosted:
                        if not hosted['bright_enabled']:
                            self.send(403, {'error': 'Bright imports are currently paused by the operator.'}); return
                        if not self.session['connected']:
                            self.send(400, {'error': 'Connect Bright before importing readings.'}); return
                        previous = self.store.sync_state()['run']
                        if previous and time.time() - previous['started'] < 60:
                            self.send(429, {'error': 'Wait a minute before starting another import.'}); return
                    accepted = self.sync.trigger(days)
                    self.send(202 if accepted else 409, {'started': accepted, 'message': ('Import queued.' if hosted and hasattr(hosted['accounts'], 'database') else 'Import started.') if accepted else 'An import is already queued or running.'})
                elif route == '/api/tariffs':
                    meter = payload.get('meter')
                    if not any(m['id'] == meter for m in self.store.meters()):
                        raise ValueError('Select a discovered meter.')
                    item = manual_tariff(payload, meter)
                    editing = payload.get('id') is not None
                    if editing:
                        matches = self.store.rows("SELECT id FROM tariffs WHERE id=? AND meter_id=? AND source='manual'", (payload['id'], meter))
                        if not matches:
                            self.send(404, {'error': 'Manual tariff not found for this meter.'})
                            return
                        item['id'] = payload['id']
                    # Reject overlapping manual entries rather than silently changing costs.
                    for existing in self.store.tariffs(meter):
                        if existing['id'] != item['id'] and existing['source'] == 'manual' and item['effective_from'] < (existing['effective_to'] or float('inf')) and existing['effective_from'] < (item['effective_to'] or float('inf')):
                            raise ValueError('This overlaps another manual tariff. Adjust the effective dates of either entry.')
                    self.store.upsert_tariff(item)
                    self.send(200 if editing else 201, {'saved': True, 'id': item['id']})
                elif route == '/api/tariffs/delete':
                    tariff_id = payload.get('id')
                    matches = self.store.rows("SELECT id FROM tariffs WHERE id=? AND source='manual'", (tariff_id,))
                    if not matches:
                        self.send(404, {'error': 'Manual tariff not found.'})
                        return
                    self.store.execute("DELETE FROM tariffs WHERE id=? AND source='manual'", (tariff_id,))
                    self.send(200, {'deleted': True})
                else:
                    self.send(404, {'error': 'Endpoint not found.'})
            except (ValueError, TariffError) as error:
                self.send(getattr(error, 'status', 400), {'error': str(error)})
            except Exception:
                self.send(500, {'error': 'The request could not be completed.'})
            finally:
                if mutation_lock: mutation_lock.release()
    return API

def make_handler(store, sync, token, port):
    API = make_api(store, sync, token, port)
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Powerall'
        def log_message(self, *_): pass
        def respond(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 <= length <= 16384: raise ValueError()
                request = API(self.command, self.path, self.headers, self.rfile.read(length))
                status, payload = request.handle()
            except (ValueError, OverflowError):
                status, payload = 400, {'error': 'Request body is too large or invalid.'}
            content = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers(); self.wfile.write(content)
        do_GET = do_POST = respond
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
