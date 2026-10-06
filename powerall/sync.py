"""One worker serializes imports, reconnects and historical refreshes."""
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from .providers import ProviderError
from .timeutils import UTC, HALF_HOUR

class SyncService:
    def __init__(self, store, provider, initial_days=90, refresh_hours=6, recover=True):
        self.store, self.provider = store, provider
        self.initial_days, self.refresh_hours = initial_days, refresh_hours
        self._scheduled = False
        self._lock, self._stop = threading.Lock(), threading.Event()
        if recover: self.store.execute("UPDATE sync_runs SET status='failed',finished=?,error='Import interrupted by app restart.',phase='Interrupted' WHERE status='running'", (int(time.time()),))

    def trigger(self, days=None):
        if self._stop.is_set() or not self._lock.acquire(blocking=False):
            return False
        worker = threading.Thread(target=self._run, args=(days,), daemon=True)
        worker.start()
        return True

    def _run(self, days):
        now = int(time.time())
        run_id = self.store.execute("INSERT INTO sync_runs(started,status,phase) VALUES(?,'running','Connecting to Bright')", (now,))
        imported, warnings = 0, []
        try:
            meters = self.provider.discover()
            for meter in meters:
                existing = self.store.rows('SELECT id FROM meters WHERE resource_id=?', (meter.resource,))
                meter_id = existing[0]['id'] if existing else str(uuid.uuid4())
                self.store.execute('''INSERT INTO meters(id,provider,provider_entity,resource_id,cost_resource_id,name,unit) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(resource_id) DO UPDATE SET name=excluded.name,cost_resource_id=excluded.cost_resource_id,active=1''',
                    (meter_id, self.provider.name, meter.entity, meter.resource, meter.cost_resource, meter.name, 'kWh'))
                self.store.execute("UPDATE sync_runs SET phase='Retrieving tariff history' WHERE id=?", (run_id,))
                try:
                    tariffs, tariff_warnings = self.provider.tariffs(meter)
                    warnings.extend(tariff_warnings)
                    for tariff in tariffs:
                        tariff['id'] = hashlib.sha256(f'{meter_id}:{tariff["effective_from"]}:{self.provider.name}'.encode()).hexdigest()[:32]
                        tariff['meter_id'] = meter_id
                        self.store.upsert_tariff(tariff)
                    if not tariffs:
                        warnings.append('Bright returned no usable tariff. Add a manual tariff to calculate costs.')
                except ProviderError as error:
                    warnings.append(str(error))
                count = self.store.rows('SELECT COUNT(*) AS count FROM readings WHERE meter_id=?', (meter_id,))[0]['count']
                lookback = days or (self.initial_days if count == 0 else 14)
                end = int(time.time()) // HALF_HOUR * HALF_HOUR
                start = int(datetime.fromtimestamp(end, UTC).replace(hour=0, minute=0, second=0).timestamp()) - lookback * 86400
                total_chunks = (end - start + 10 * 86400 - 1) // (10 * 86400)
                chunk = 0
                while start < end:
                    chunk += 1
                    chunk_end = min(start + 10 * 86400, end)
                    self.store.execute('UPDATE sync_runs SET phase=? WHERE id=?', (f'Importing half-hours · batch {chunk}/{total_chunks}', run_id))
                    readings = self.provider.readings(meter, start, chunk_end)
                    with self.store.connection() as db:
                        db.executemany('''INSERT INTO readings(meter_id,start,kwh,updated) VALUES(?,?,?,?)
                            ON CONFLICT(meter_id,start) DO UPDATE SET kwh=excluded.kwh,updated=excluded.updated''',
                            [(meter_id, instant, value, int(time.time())) for instant, value in readings])
                        retention = getattr(self, 'retention_days', None)
                        if retention:
                            db.execute('DELETE FROM readings WHERE start<?', (int(time.time()) - retention * 86400,))
                    imported += len(readings)
                    self.store.execute('UPDATE sync_runs SET imported=? WHERE id=?', (imported, run_id))
                    start = chunk_end
                # Mark streams no longer found as inactive while retaining history.
            identifiers = {m.resource for m in meters}
            for record in self.store.rows('SELECT resource_id FROM meters WHERE provider=?', (self.provider.name,)):
                if record['resource_id'] not in identifiers:
                    self.store.execute('UPDATE meters SET active=0 WHERE resource_id=?', (record['resource_id'],))
            self.store.execute('UPDATE sync_runs SET status=?,finished=?,phase=?,warnings=? WHERE id=?',
                ('partial' if warnings else 'success', int(time.time()), 'Import complete', json.dumps(sorted(set(warnings))), run_id))
        except ProviderError as error:
            self.store.execute("UPDATE sync_runs SET status='failed',finished=?,phase='Refresh failed',error=?,warnings=? WHERE id=?",
                (int(time.time()), str(error), json.dumps(sorted(set(warnings))), run_id))
        except Exception:
            self.store.execute("UPDATE sync_runs SET status='failed',finished=?,phase='Refresh failed',error='Import failed unexpectedly. Cached readings remain available.',warnings=? WHERE id=?",
                (int(time.time()), json.dumps(sorted(set(warnings))), run_id))
        finally:
            self._lock.release()

    def schedule(self):
        if self._scheduled: return
        self._scheduled = True
        def loop():
            while not self._stop.is_set():
                state = self.store.sync_state()
                last = state['last_success']
                run = state['run']
                # Failed jobs back off for an hour; users can always retry manually.
                attempt = run['started'] if run else 0
                failed_recently = run and run['status'] == 'failed' and time.time() - attempt < 3600
                running = run and run['status'] == 'running'
                if not failed_recently and not running and (last is None or time.time() - last >= self.refresh_hours * 3600):
                    self.trigger()
                self._stop.wait(60)
        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self._stop.set()
