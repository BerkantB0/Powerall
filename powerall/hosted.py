"""Production WSGI entry point. API replicas are stateless; hosted persistence and work ownership are shared."""
import atexit
import json
import os
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit
from .auth import Accounts
from .tenants import Tenants
from .server import make_api


def read_secret(name, length=None):
    path = os.environ.get(name + '_FILE')
    if not path: raise ValueError(f'{name}_FILE is required for hosted mode.')
    value = Path(path).read_bytes().strip()
    if length and len(value) != length: raise ValueError(f'{name} has an invalid length.')
    return value


def create_app(accounts=None, origin=None, internal_token=None, automatic=True):
    if accounts is None:
        root = os.environ.get('POWERALL_DATA_DIR', '/data')
        # Key is stored as 64 hex characters in a mounted secret, never in the DB/image.
        key = bytes.fromhex(read_secret('POWERALL_ENCRYPTION_KEY', 64).decode())
        from .postgres import Database, PostgresAccounts
        accounts = PostgresAccounts(Database(), root, key)
    origin = origin or os.environ.get('POWERALL_PUBLIC_ORIGIN', '')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('POWERALL_PUBLIC_ORIGIN must be an exact HTTPS origin without a trailing slash.')
    internal_token = internal_token or read_secret('POWERALL_INTERNAL_TOKEN').decode()
    if len(internal_token) < 32: raise ValueError('Internal token must contain at least 32 characters.')
    capacity = os.environ.get('POWERALL_MAX_ACCOUNTS', '').strip()
    max_accounts = int(capacity) if capacity else None
    if max_accounts is not None and max_accounts < 1:
        raise ValueError('Optional account capacity must be positive.')
    accounts.max_users = max_accounts
    bright_enabled = not automatic or os.environ.get('POWERALL_BRIGHT_ENABLED', 'true') == 'true'
    application_id = os.environ.get('POWERALL_BRIGHT_APPLICATION_ID')
    if application_id:
        import uuid
        uuid.UUID(application_id)
    initial_days = int(os.environ.get('POWERALL_IMPORT_DAYS', '90'))
    refresh_hours = float(os.environ.get('POWERALL_REFRESH_HOURS', '6'))
    if not 1 <= initial_days <= 730 or not 0 < refresh_hours <= 168:
        raise ValueError('Hosted import/refresh settings are outside their supported bounds.')
    shared = hasattr(accounts, 'database')
    if shared:
        from .jobs import SharedTenants
        tenants = SharedTenants(accounts, bright_enabled)
    else:
        tenants = Tenants(accounts, initial_days, refresh_hours, automatic and bright_enabled)
    atexit.register(tenants.stop)
    privacy = {
        'operator': os.environ.get('POWERALL_OPERATOR_NAME', ''),
        'contact': os.environ.get('POWERALL_PRIVACY_CONTACT', ''),
        'location': os.environ.get('POWERALL_HOSTING_LOCATION', ''),
        'legal_basis': os.environ.get('POWERALL_PRIVACY_LEGAL_BASIS', ''),
    }
    if automatic and not all(privacy.values()):
        raise ValueError('Operator name, privacy contact, hosting location and legal basis are required.')
    if automatic and not shared:
        tenants.start_housekeeping()
        if bright_enabled: tenants.resume()
    hosted = {'privacy': privacy, 'bright_enabled': bright_enabled, 'accounts': accounts, 'tenants': tenants, 'origin': origin, 'internal_token': internal_token}
    API = make_api(None, None, None, 8765, hosted)

    def application(environ, start_response):
        # Internal health endpoint: no household/account metadata.
        if environ.get('PATH_INFO') == '/healthz':
            try:
                with accounts.db() as db: db.execute('SELECT 1')
                status, payload, extra = 200, {'ok': True}, {}
            except Exception:
                status, payload, extra = 503, {'ok': False}, {}
        else:
            try:
                headers = Message()
                for key, value in environ.items():
                    if key.startswith('HTTP_'): headers[key[5:].replace('_', '-')] = value
                headers['Content-Type'] = environ.get('CONTENT_TYPE', '')
                length = int(environ.get('CONTENT_LENGTH') or 0)
                if not 0 <= length <= 16384: raise ValueError('Request body is too large.')
                headers['Content-Length'] = str(length)
                body = environ['wsgi.input'].read(length)
                if len(body) != length: raise ValueError('Incomplete request body.')
                path = environ.get('PATH_INFO', '/')
                if environ.get('QUERY_STRING'): path += '?' + environ['QUERY_STRING']
                request = API(environ['REQUEST_METHOD'], path, headers, body)
                # Shared deployments use database locks; local compatibility uses its mutex.
                with tenants.lock: status, payload = request.handle()
                extra = request.response_headers
            except ValueError as error:
                status, payload, extra = 400, {'error': str(error)}, {}
            except Exception:
                status, payload, extra = 500, {'error': 'The request could not be completed.'}, {}
        content = json.dumps(payload, allow_nan=False).encode()
        headers = [('Content-Type', 'application/json; charset=utf-8'), ('Content-Length', str(len(content))),
            ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff')]
        headers.extend(extra.items())
        start_response(f'{status} {HTTPStatus(status).phrase}', headers)
        return [content]
    application.accounts = accounts
    application.tenants = tenants
    return application
