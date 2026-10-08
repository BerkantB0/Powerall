"""Durable PostgreSQL queue. Workers and API replicas share locks and rate limits."""
import hashlib
import os
import threading
import time
from contextlib import nullcontext
from .auth import AuthError
from .postgres import PostgresStore
from .tenants import EncryptedBright
from .sync import SyncService

def retry_delay(previous_failures):
    return min(900, 60 * 2 ** min(previous_failures, 4))

class HouseholdLock:
    def __init__(self, database, user_id):
        self.database = database
        self.key = int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:8], 'big', signed=True)
        self.context = self.connection = None
    def acquire(self, blocking=False):
        self.context = self.database.pool.connection()
        self.connection = self.context.__enter__()
        try:
            acquired = self.connection.execute('SELECT pg_try_advisory_lock(%s) AS locked', (self.key,)).fetchone()['locked']
            self.connection.commit()
        except BaseException:
            self.connection.close()
            self.context.__exit__(None,None,None); self.context = self.connection = None
            raise
        if not acquired:
            self.context.__exit__(None,None,None); self.context = self.connection = None
        return acquired
    def release(self):
        if self.connection:
            try:
                self.connection.execute('SELECT pg_advisory_unlock(%s)', (self.key,))
                self.connection.commit()
            except Exception:
                self.connection.close()  # Discard a lost connection; never return a held lock to the pool.
            finally:
                self.context.__exit__(None,None,None); self.context = self.connection = None

class QueuedSync:
    def __init__(self, database, user_id):
        self.database, self.user_id = database, user_id
        self._lock = HouseholdLock(database, user_id)
        self.provider = type('NoCachedCredentials', (), {'clear':lambda self:None})()
    def trigger(self, days=None):
        with self.database.connection() as db:
            result = db.execute('''INSERT INTO import_jobs(user_id,days,state,available_at) VALUES(?,?,'queued',?)
                ON CONFLICT(user_id) DO UPDATE SET
                    days=CASE WHEN import_jobs.state='queued' THEN COALESCE(excluded.days,import_jobs.days) ELSE excluded.days END,
                    state='queued',available_at=excluded.available_at,attempts=0,owner=NULL
                WHERE import_jobs.state='idle' OR (import_jobs.state='queued' AND import_jobs.attempts>0)
                RETURNING user_id''', (self.user_id,days,int(time.time())))
            return result.fetchone() is not None
    def schedule(self): pass  # Scheduling is done exclusively by worker processes.

class SharedTenants:
    def __init__(self, accounts, automatic=True):
        self.accounts, self.database, self.automatic = accounts, accounts.database, automatic
        self.lock = nullcontext()  # No process-wide serialization of dashboard requests.
    def get(self, user_id):
        return PostgresStore(self.database,user_id), QueuedSync(self.database,user_id)
    def disconnect(self, user_id, delete=False):
        lock = HouseholdLock(self.database,user_id)
        if not lock.acquire(): raise AuthError('Wait for the current import to finish, then try again.',409)
        try:
            with self.database.connection() as db:
                db.execute('DELETE FROM import_jobs WHERE user_id=?',(user_id,))
            if delete: self.accounts.delete(user_id)
            else: self.accounts.disconnect(user_id)
        finally: lock.release()
    def stop(self): self.database.close()

class LimitedBright(EncryptedBright):
    def provider(self):
        provider=super().provider()
        provider.before_request=self.reserve
        provider.rate_limited=self.cooldown
        return provider
    def cooldown(self, delay):
        with self.accounts.database.connection() as db:
            db.execute("UPDATE provider_limits SET blocked_until=GREATEST(blocked_until,EXTRACT(EPOCH FROM clock_timestamp())+?) WHERE provider='bright'",(delay,))
    def reserve(self):
        # Reserve global call slots in the database; replicas cannot each multiply the rate.
        spacing = float(os.environ.get('POWERALL_PROVIDER_INTERVAL_SECONDS','1'))
        if spacing < 0.1: raise ValueError('Provider interval must be at least 0.1 seconds.')
        while True:
            with self.accounts.database.connection() as db:
                slot = db.execute("""UPDATE provider_limits SET next_at=GREATEST(next_at,blocked_until,EXTRACT(EPOCH FROM clock_timestamp()))+?
                    WHERE provider='bright' RETURNING next_at-? AS instant""",(spacing,spacing)).fetchone()['instant']
            time.sleep(max(0,slot-time.time()))
            with self.accounts.database.connection() as db:
                blocked=db.execute("SELECT blocked_until FROM provider_limits WHERE provider='bright'").fetchone()['blocked_until']
            if blocked<=time.time(): return


class Worker:
    def __init__(self, accounts, initial_days=90, refresh_hours=6):
        self.accounts, self.database = accounts, accounts.database
        self.initial_days, self.refresh_hours = initial_days, refresh_hours
        self.stop_event = threading.Event()
        import math
        interval=float(os.environ.get('POWERALL_PROVIDER_INTERVAL_SECONDS','1'))
        if not math.isfinite(interval) or interval<0.1: raise ValueError('Invalid provider request interval.')
        if not 1<=initial_days<=730 or not 0<refresh_hours<=168: raise ValueError('Invalid import settings.')
    def schedule(self):
        now=int(time.time())
        with self.database.connection() as db:
            # Repair deferred retries written by older workers without touching
            # active ownership, historical ranges or successful refresh schedules.
            db.execute("UPDATE import_jobs SET available_at=? WHERE state='queued' AND attempts>0 AND available_at>?",
                       (now+900,now+900))
            db.execute('''INSERT INTO import_jobs(user_id,available_at)
                SELECT id,? FROM users WHERE credentials IS NOT NULL
                ON CONFLICT(user_id) DO UPDATE SET state='queued'
                WHERE import_jobs.state='idle' AND import_jobs.available_at<=?''',(now,now))
    def once(self):
        with self.database.connection() as db:
            candidates=db.execute("SELECT user_id FROM import_jobs WHERE state<>'idle' AND available_at<=? ORDER BY available_at LIMIT 100",(int(time.time()),)).fetchall()
        for candidate in candidates:
            user_id=candidate['user_id']; lock=HouseholdLock(self.database,user_id)
            if not lock.acquire(): continue
            try:
                with self.database.connection() as db:
                    job=db.execute("SELECT * FROM import_jobs WHERE user_id=? AND state<>'idle' AND available_at<=? FOR UPDATE",(user_id,int(time.time()))).fetchone()
                    if not job: continue
                    import uuid
                    owner=str(uuid.uuid4())
                    db.execute("UPDATE import_jobs SET state='running',attempts=attempts+1,owner=? WHERE user_id=?",(owner,user_id))
                store=PostgresStore(self.database,user_id,owner)
                if not self.accounts.credentials(user_id):
                    with self.database.connection() as db: db.execute('DELETE FROM import_jobs WHERE user_id=?',(user_id,))
                    continue
                # Advisory ownership survives between transactions; crash releases it automatically.
                # Replaying imports is safe: readings/tariffs are upserts.
                sync=SyncService(store,LimitedBright(self.accounts,user_id),self.initial_days,self.refresh_hours,recover=True)
                sync.retention_days=730
                sync._lock.acquire()
                sync._run(job['days'])
                failed=store.sync_state()['run']['status']=='failed'
                delay=retry_delay(job['attempts']) if failed else int(self.refresh_hours*3600)
                with self.database.connection() as db:
                    db.execute('UPDATE import_jobs SET state=?,available_at=?,days=?,attempts=?,owner=NULL WHERE user_id=? AND owner=?',
                        ('queued' if failed else 'idle',int(time.time())+delay,(job['days'] or self.initial_days) if failed else None,
                         job['attempts']+1 if failed else 0,user_id,owner))
                return True
            finally: lock.release()
        return False
    def prune(self):
        self.accounts.prune()
        # Bound retention transactions to one household at a time.
        with self.database.connection() as db: users=db.execute('SELECT id FROM users').fetchall()
        for user in users:
            store=PostgresStore(self.database,user['id'])
            store.execute('DELETE FROM readings WHERE start<?',(int(time.time())-730*86400,))
            store.execute('DELETE FROM sync_runs WHERE started<?',(int(time.time())-30*86400,))
    def housekeeping(self):
        lock=HouseholdLock(self.database,'powerall-housekeeping')
        if not lock.acquire(): return
        try:
            with self.database.connection() as db:
                claimed=db.execute("UPDATE maintenance_schedule SET next_at=? WHERE name='housekeeping' AND next_at<=? RETURNING name",(int(time.time())+60,int(time.time()))).fetchone()
            if claimed:
                self.prune()
                if os.environ.get('POWERALL_BRIGHT_ENABLED','true')=='true': self.schedule()
        finally: lock.release()
    def heartbeat(self):
        import uuid
        worker_id=str(uuid.uuid4())
        while not self.stop_event.is_set():
            try:
                with self.database.connection() as db:
                    db.execute('INSERT INTO worker_heartbeats VALUES(?,?) ON CONFLICT(worker_id) DO UPDATE SET seen_at=excluded.seen_at',(worker_id,int(time.time())))
                    db.execute('DELETE FROM worker_heartbeats WHERE seen_at<?',(int(time.time())-86400,))
            except Exception: pass
            self.stop_event.wait(15)
    def run(self):
        heartbeat=threading.Thread(target=self.heartbeat,daemon=True); heartbeat.start()
        last=0
        while not self.stop_event.is_set():
            try:
                if time.time()-last>=60:
                    self.housekeeping()
                    last=time.time()
                if os.environ.get('POWERALL_BRIGHT_ENABLED','true')=='true' and self.once(): continue
            except Exception:
                import logging
                logging.getLogger(__name__).warning('Worker operation deferred; retrying shortly.')
                self.stop_event.wait(5)
            self.stop_event.wait(2)

def main():
    import signal
    from .manage import accounts
    account_store=accounts()
    worker=Worker(account_store,int(os.environ.get('POWERALL_IMPORT_DAYS','90')),float(os.environ.get('POWERALL_REFRESH_HOURS','6')))
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,lambda *_:worker.stop_event.set())
    try: worker.run()
    finally: account_store.database.close()

if __name__=='__main__': main()
