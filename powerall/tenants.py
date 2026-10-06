"""Tenant contexts are selected by authenticated session, never request IDs."""
from contextlib import closing
import os
import shutil
import sqlite3
import time
import threading
from .auth import AuthError
from .bright import Bright
from .providers import ProviderError
from .storage import Store
from .sync import SyncService

class EncryptedBright:
    name = 'bright'
    network_slots = threading.BoundedSemaphore(2)
    def __init__(self, accounts, user_id):
        self.accounts, self.user_id = accounts, user_id
        self.adapter = None
        self.fingerprint = None
    def provider(self):
        values = self.accounts.credentials(self.user_id)
        if not values:
            raise ProviderError('Connect your Bright account to import readings.')
        # Refresh the in-memory adapter when credentials change. Nothing is returned to clients.
        if values != self.fingerprint:
            self.adapter = Bright(values['username'], values['password'], os.environ.get('POWERALL_BRIGHT_APPLICATION_ID'))
            self.fingerprint = values
        return self.adapter
    def call(self, method, *args):
        with self.network_slots:
            return getattr(self.provider(), method)(*args)
    def discover(self): return self.call('discover')
    def readings(self, *args): return self.call('readings', *args)
    def tariffs(self, *args): return self.call('tariffs', *args)
    def clear(self): self.adapter = self.fingerprint = None

class Tenants:
    def __init__(self, accounts, initial_days=90, refresh_hours=6, automatic=True):
        self.accounts, self.initial_days, self.refresh_hours = accounts, initial_days, refresh_hours
        self.automatic = automatic
        self.contexts = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.housekeeping = None
    def get(self, user_id):
        # user_id comes only from the accounts database (server-generated UUID).
        with self.lock:
            if user_id not in self.contexts:
                store = Store(self.accounts.root / 'households' / user_id / 'powerall.sqlite3')
                sync = SyncService(store, EncryptedBright(self.accounts, user_id), self.initial_days, self.refresh_hours)
                sync.retention_days = 730
                self.contexts[user_id] = (store, sync)
                if self.automatic and self.accounts.credentials(user_id): sync.schedule()
            return self.contexts[user_id]
    def resume(self):
        with self.accounts.db() as db:
            ids = [r['id'] for r in db.execute('SELECT id FROM users WHERE credentials IS NOT NULL')]
        for user_id in ids: self.get(user_id)
    def disconnect(self, user_id, delete=False):
        with self.lock:
            store, sync = self.get(user_id)
            if not sync._lock.acquire(blocking=False):
                raise AuthError('Wait for the current import to finish, then try again.', 409)
            try:
                sync.stop()
                sync.provider.clear()
                self.accounts.disconnect(user_id)
                if delete:
                    self.accounts.delete(user_id)
                    shutil.rmtree(store.path.parent)
                self.contexts.pop(user_id, None)
            finally:
                sync._lock.release()
    def prune(self):
        self.accounts.prune()
        cutoff = int(time.time()) - 730 * 86400
        for path in (self.accounts.root / 'households').glob('*/powerall.sqlite3'):
            with self.lock:
                if not path.exists(): continue
                with closing(sqlite3.connect(path, timeout=30)) as db:
                    db.execute('PRAGMA secure_delete=ON')
                    db.execute('DELETE FROM readings WHERE start<?', (cutoff,))
                    db.commit()
    def start_housekeeping(self):
        def loop():
            while not self.stop_event.is_set():
                try: self.prune()
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning('Privacy housekeeping deferred; retrying next minute.')
                self.stop_event.wait(60)
        self.housekeeping = threading.Thread(target=loop, daemon=True)
        self.housekeeping.start()
    def stop(self):
        self.stop_event.set()
        with self.lock:
            for _, sync in list(self.contexts.values()): sync.stop()
        if self.housekeeping and self.housekeeping is not threading.current_thread():
            self.housekeeping.join(timeout=5)
