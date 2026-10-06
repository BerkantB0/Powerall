"""Read-only Glowmarkt integration. JWT is cached in memory, never persisted."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal, InvalidOperation
from .providers import Meter, ProviderError
from .tariffs import number, validate_windows, TariffError
from .timeutils import UTC, HALF_HOUR, parse_instant

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials or JWT headers to a redirect destination.
        return None

def safe_urlopen(request, timeout):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)

class Bright:
    name = 'bright'
    base = 'https://api.glowmarkt.com/api/v0-1'
    application_id = 'b0f1b774-a586-4f72-9edd-27ead8aa7a8d'

    def __init__(self, username, password, application_id=None):
        if application_id: self.application_id = application_id
        self._username, self._password = username, password
        self._token, self._expires = None, 0
        self.before_request = None
        self.rate_limited = None

    def _request(self, path, payload=None, authenticated=True, retried_auth=False):
        if authenticated and (not self._token or time.time() > self._expires - 60):
            self._authenticate()
        headers = {'Content-Type': 'application/json', 'applicationId': self.application_id}
        if authenticated:
            headers['token'] = self._token
        data = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(3):
            if self.before_request: self.before_request()
            req = urllib.request.Request(self.base + path, data=data, headers=headers)
            try:
                with safe_urlopen(req, timeout=30) as response:
                    content = response.read(2 * 1024 * 1024 + 1)
                    if len(content) > 2 * 1024 * 1024:
                        raise ProviderError('Bright returned an oversized response. The import was stopped safely.')
                    result = json.loads(content)
                return result
            except urllib.error.HTTPError as error:
                code = error.code
                if code == 429 and self.rate_limited:
                    try: delay = min(3600,max(1,float(error.headers.get('Retry-After','60'))))
                    except (ValueError,TypeError): delay = 60
                    self.rate_limited(delay)
                error.close()
                if code == 401 and authenticated and not retried_auth:
                    self._token = None
                    return self._request(path, payload, True, True)
                if (code == 429 or code >= 500) and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                if code in (401, 403):
                    raise ProviderError('Bright access was refused. Check credentials and meter consent in the Bright app.') from None
                raise ProviderError(f'Bright returned HTTP {code}. Try refreshing again later.') from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise ProviderError('Cannot reach Bright. Cached readings remain available; try again later.') from None
            except (ValueError, TypeError):
                raise ProviderError('Bright returned an unexpected response. Try again later.') from None

    def _authenticate(self):
        if not self._username or not self._password:
            raise ProviderError('Add BRIGHT_USERNAME and BRIGHT_PASSWORD to the local .env file, then restart.')
        response = self._request('/auth', {'username': self._username, 'password': self._password}, False)
        if not isinstance(response, dict) or response.get('valid') is False or not response.get('token'):
            raise ProviderError('Bright sign-in failed. Check the credentials in your local .env file.')
        self._token = response['token']
        self._expires = int(response.get('exp', time.time() + 7 * 86400))

    def discover(self):
        entities = self._request('/virtualentity')
        if not isinstance(entities, list):
            raise ProviderError('Bright returned an unexpected meter list.')
        result = []
        for entity in entities:
            entity_id = entity.get('veId')
            if not entity_id:
                continue
            detail = self._request('/virtualentity/' + urllib.parse.quote(entity_id, safe='') + '/resources')
            resources = detail.get('resources', [])
            cost = next((r['resourceId'] for r in resources if r.get('classifier') == 'electricity.consumption.cost' and r.get('active', True)), None)
            for resource in resources:
                if resource.get('classifier') == 'electricity.consumption' and resource.get('active', True):
                    unit = resource.get('baseUnit', 'kWh')
                    if unit not in ('kWh', 'Wh'):
                        raise ProviderError('Bright electricity units are unsupported; no readings were imported for this stream.')
                    result.append(Meter(entity_id, resource['resourceId'], cost, str(entity.get('name', 'Home')), unit))
        if not result:
            raise ProviderError('No active electricity meter found. Complete verification and check meter consent in Bright.')
        return result

    def readings(self, meter, start, end):
        # Glowmarkt limits PT30M queries to ten days; the sync service chunks them.
        fmt = lambda stamp: datetime.fromtimestamp(stamp, UTC).strftime('%Y-%m-%dT%H:%M:%S')
        query = urllib.parse.urlencode({'from': fmt(start), 'to': fmt(end - 1), 'period': 'PT30M', 'offset': 0, 'function': 'sum'})
        response = self._request('/resource/' + urllib.parse.quote(meter.resource, safe='') + '/readings?' + query)
        if not isinstance(response, dict) or response.get('status') != 'OK' or not isinstance(response.get('data'), list):
            raise ProviderError('Bright did not return a valid readings response.')
        result = []
        for row in response['data']:
            try:
                instant, value = row
                if isinstance(instant, bool) or not isinstance(instant, (int, float)) or instant != int(instant):
                    continue
                instant = int(instant)
                value = Decimal(str(value))
                if not value.is_finite() or value < 0 or instant % HALF_HOUR or not start <= instant < end:
                    continue
                if meter.unit == 'Wh':
                    value /= 1000
                result.append((instant, str(value)))
            except (ValueError, TypeError, InvalidOperation):
                continue
        return result

    def tariffs(self, meter):
        warnings, raw = [], []
        # New 'structure' carries time-of-use windows; legacy 'plan' may omit them.
        current = self._request('/resource/' + urllib.parse.quote(meter.resource, safe='') + '/tariff')
        if not isinstance(current, dict) or current.get('status') != 'OK' or not isinstance(current.get('data'), list):
            raise ProviderError('Bright did not return a valid current tariff.')
        raw.extend(current['data'])
        if meter.cost_resource:
            try:
                history = self._request('/resource/' + urllib.parse.quote(meter.cost_resource, safe='') + '/tariff-list')
                if not isinstance(history, dict) or history.get('status') != 'OK' or not isinstance(history.get('data'), list):
                    raise ProviderError('Tariff history is unavailable.')
                raw = history['data'] + raw
            except ProviderError:
                warnings.append('Bright tariff history is unavailable. The current tariff was retained; add earlier rates manually.')
        parsed = {}
        for item in raw:
            try:
                if item.get('type') not in (None, 'tou', 'flat', 'fixed'):
                    raise TariffError('Unsupported tariff type.')
                effective = parse_instant(item.get('effectiveDate') or item['from'])
                plans = item.get('structure') or item.get('plan') or []
                if not isinstance(plans, list) or len(plans) != 1:
                    raise TariffError('Multiple tariff schedules require manual entry.')
                details = plans[0].get('planDetail', [])
                if any('dynamic' in r or 'block' in r for r in details):
                    raise TariffError('Dynamic or block tariff requires another pricing source.')
                if sum('rate' in r for r in details) > 1:
                    raise TariffError('Multiple base rates require manual entry.')
                if item.get('type') == 'tou' and not any('tourate' in r for r in details):
                    raise TariffError('Time-of-use details are incomplete.')
                rate = next((r['rate'] for r in details if 'rate' in r), item.get('currentRates', {}).get('rate'))
                standing = next((r['standing'] for r in details if 'standing' in r), item.get('currentRates', {}).get('standingCharge'))
                windows = []
                for entry in details:
                    if 'tourate' in entry:
                        start, end = entry['time'].split('-')
                        windows.append({'start': start, 'end': end, 'rate': entry['tourate']})
                parsed[effective] = {'source': 'bright', 'label': item.get('displayName') or item.get('name') or 'Bright meter tariff',
                    'effective_from': effective, 'effective_to': None, 'rate': number(rate, 'Bright rate'),
                    'standing': number(standing, 'Bright standing charge'), 'windows': validate_windows(windows),
                    'clock': 'Europe/London', 'vat': 'unconfirmed',
                    'warning': 'Bright tariff clock and VAT are unconfirmed. Estimates assume UK local time and use rates as reported. Add a manual tariff to confirm your bill rates.'}
            except (KeyError, ValueError, TypeError, TariffError):
                warnings.append('A Bright tariff could not be interpreted safely. Enter its rates manually for that period.')
        return list(parsed.values()), sorted(set(warnings))
