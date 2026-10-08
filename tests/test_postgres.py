"""Real PostgreSQL integration tests, run only in a disposable Docker project."""
import os
import secrets
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

@unittest.skipUnless(os.environ.get('POWERALL_TEST_POSTGRES')=='1','Needs disposable PostgreSQL test stack')
class PostgresTests(unittest.TestCase):
    def setUp(self):
        from powerall.postgres import Database,PostgresAccounts,PostgresStore
        from powerall.hosted import read_secret
        self.database=Database()
        self.accounts=PostgresAccounts(self.database,'/tmp',bytes.fromhex(read_secret('POWERALL_ENCRYPTION_KEY',64).decode()))
        self.ids=[str(uuid.uuid4()),str(uuid.uuid4())]
        with self.accounts.db() as db:
            for user_id in self.ids:
                db.execute('INSERT INTO users VALUES(?,?,?,NULL,?)',(user_id,'fixture-'+user_id,self.accounts.dummy_hash,int(time.time())))
        self.stores=[PostgresStore(self.database,u) for u in self.ids]
    def tearDown(self):
        for user_id in self.ids:self.accounts.delete(user_id,record=False)
        self.database.close()
    def seed(self,store):
        store.execute("INSERT INTO meters(id,provider,provider_entity,resource_id,name) VALUES('same-meter','bright','e','same-resource','Fixture')")
    def test_database_isolation_and_pool_context_reset(self):
        import psycopg
        alice,bob=self.stores
        self.seed(alice);self.seed(bob)
        alice.execute("INSERT INTO readings(meter_id,start,kwh,updated) VALUES('same-meter',EXTRACT(EPOCH FROM NOW())::bigint,'2',1)")
        self.assertEqual(len(alice.rows('SELECT * FROM readings')),1)
        self.assertEqual(bob.rows('SELECT * FROM readings'),[])
        bob.execute("UPDATE readings SET kwh='999'")
        self.assertEqual(alice.rows('SELECT kwh FROM readings')[0]['kwh'],'2')
        with self.database.connection() as db:self.assertEqual(db.execute('SELECT * FROM readings').fetchall(),[])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with bob.connection() as db:
                db.execute('INSERT INTO readings(tenant_id,meter_id,start,kwh,updated) VALUES(?,?,?,?,?)',(self.ids[0],'same-meter',2,'5',1))
        self.assertEqual(bob.rows('SELECT * FROM readings'),[])
    def test_shared_sessions_revocation_and_optional_capacity(self):
        from powerall.postgres import Database,PostgresAccounts
        replica=Database();other=PostgresAccounts(replica,'/tmp',bytes.fromhex(__import__('powerall.hosted',fromlist=['read_secret']).read_secret('POWERALL_ENCRYPTION_KEY',64).decode()))
        try:
            token=self.accounts.new_session(self.ids[0]);self.assertEqual(other.session(token)['user_id'],self.ids[0])
            other.logout(token);self.assertIsNone(self.accounts.session(token))
            self.assertIsNone(other.max_users)
            for _ in range(26):
                user_id=str(uuid.uuid4());self.ids.append(user_id)
                with self.accounts.db() as db: db.execute('INSERT INTO users VALUES(?,?,?,NULL,?)',(user_id,'fixture-'+user_id,self.accounts.dummy_hash,int(time.time())))
            invite=self.accounts.invite()
            value=self.accounts.register('fixture-'+secrets.token_hex(8),'fixture-long-passphrase',invite,'fixture-client')
            self.ids.append(self.accounts.session(value)['user_id'])
        finally:replica.close()
    def test_worker_dedup_crash_replay_and_retry(self):
        from powerall.jobs import Worker,QueuedSync,HouseholdLock
        from powerall.providers import Meter,ProviderError
        user=self.ids[0];self.accounts.set_credentials(user,'fixture-bright','fixture-password')
        queue=QueuedSync(self.database,user)
        self.assertTrue(queue.trigger(1));self.assertFalse(queue.trigger(1))
        lock=HouseholdLock(self.database,user);self.assertTrue(lock.acquire())
        try:self.assertFalse(Worker(self.accounts).once())
        finally:lock.release()
        with self.accounts.db() as db:db.execute("UPDATE import_jobs SET state='running' WHERE user_id=?",(user,))
        class Provider:
            name='bright'
            def __init__(self,*args):pass
            def discover(self):return [Meter('e','r',None,'Fixture')]
            def tariffs(self,*args):return [],[]
            def readings(self,meter,start,end):return [(start,'1.5')]
        with patch('powerall.jobs.LimitedBright',Provider):
            self.assertTrue(Worker(self.accounts).once())
            first=self.stores[0].rows('SELECT * FROM readings')
            self.assertTrue(queue.trigger(1));self.assertTrue(Worker(self.accounts).once())
            self.assertEqual(len(self.stores[0].rows('SELECT * FROM readings')),len(first))
        class Failed(Provider):
            def discover(self):raise ProviderError('Fixture failure')
        with patch('powerall.jobs.LimitedBright',Failed):
            self.assertTrue(queue.trigger(1));self.assertTrue(Worker(self.accounts).once())
        with self.accounts.db() as db:
            job=db.execute('SELECT * FROM import_jobs WHERE user_id=?',(user,)).fetchone()
        self.assertEqual(job['state'],'queued');self.assertGreater(job['available_at'],time.time());self.assertEqual(job['days'],1)
        self.assertEqual(self.stores[0].sync_state()['run']['status'],'failed')
        self.assertTrue(self.stores[0].sync_state()['queue']['retry'])
        self.assertLessEqual(job['available_at']-time.time(),60)
        # A manual refresh brings an already-deferred historical retry forward.
        with self.accounts.db() as db:
            db.execute('UPDATE import_jobs SET available_at=? WHERE user_id=?',(int(time.time())+86400,user))
        Worker(self.accounts).schedule()
        with self.accounts.db() as db:
            repaired=db.execute('SELECT * FROM import_jobs WHERE user_id=?',(user,)).fetchone()
        self.assertLessEqual(repaired['available_at']-time.time(),900)
        self.assertEqual(repaired['days'],1)
        self.assertTrue(queue.trigger())
        self.assertFalse(queue.trigger())
        with self.accounts.db() as db:
            retry=db.execute('SELECT * FROM import_jobs WHERE user_id=?',(user,)).fetchone()
        self.assertEqual(retry['days'],1);self.assertEqual(retry['attempts'],0)
        self.assertLessEqual(retry['available_at'],time.time())
        self.assertFalse(self.stores[0].sync_state()['queue']['retry'])
        with patch('powerall.jobs.LimitedBright',Provider):
            self.assertTrue(Worker(self.accounts).once())
        with self.accounts.db() as db:
            completed=db.execute('SELECT * FROM import_jobs WHERE user_id=?',(user,)).fetchone()
        self.assertEqual(completed['state'],'idle');self.assertEqual(completed['attempts'],0)
        # Old releases accumulated attempts across successes: that alone is not
        # evidence that a newly scheduled refresh is a failed retry.
        with self.accounts.db() as db:
            db.execute("UPDATE import_jobs SET state='queued',attempts=4 WHERE user_id=?",(user,))
        self.assertFalse(self.stores[0].sync_state()['queue']['retry'])
        # Running imports must never lose their ownership token to a manual retry.
        with self.accounts.db() as db:
            db.execute("UPDATE import_jobs SET state='running',attempts=3,owner='fixture-owner' WHERE user_id=?",(user,))
        self.assertFalse(queue.trigger(30))
        with self.accounts.db() as db:
            self.assertEqual(db.execute('SELECT owner FROM import_jobs WHERE user_id=?',(user,)).fetchone()['owner'],'fixture-owner')
    def test_disconnect_delete_lock_and_erasure_cascade(self):
        from powerall.jobs import SharedTenants,HouseholdLock,QueuedSync
        from powerall.auth import AuthError
        self.seed(self.stores[0]);self.seed(self.stores[1])
        tenants=SharedTenants(self.accounts);lock=HouseholdLock(self.database,self.ids[0]);self.assertTrue(lock.acquire())
        try:
            with self.assertRaises(AuthError):tenants.disconnect(self.ids[0],delete=True)
        finally:lock.release()
        QueuedSync(self.database,self.ids[0]).trigger()
        tenants.disconnect(self.ids[0],delete=True)
        self.assertEqual(self.stores[0].meters(),[]);self.assertEqual(len(self.stores[1].meters()),1)
        with self.accounts.db() as db:
            self.assertIsNotNone(db.execute('SELECT * FROM erasures WHERE user_id=?',(self.ids[0],)).fetchone())
            self.assertIsNone(db.execute('SELECT * FROM import_jobs WHERE user_id=?',(self.ids[0],)).fetchone())
    def test_local_sqlite_migration_is_transactional_and_preserves_source(self):
        from powerall.storage import Store
        from powerall.migration import migrate_sqlite
        with tempfile.TemporaryDirectory() as folder:
            source=Store(Path(folder)/'powerall.sqlite3');self.seed(source)
            source.execute("INSERT INTO readings VALUES('same-meter',?,'4.5',1)",(int(time.time()),))
            source.execute("INSERT INTO sync_runs(started,status,phase) VALUES(1,'success','Done')")
            with self.accounts.db() as db:username=db.execute('SELECT username FROM users WHERE id=?',(self.ids[0],)).fetchone()['username']
            migrate_sqlite(self.accounts,Path(folder),username)
            self.assertEqual(self.stores[0].rows('SELECT kwh FROM readings')[0]['kwh'],'4.5')
            self.assertTrue(source.path.exists())
            with self.assertRaises(ValueError):migrate_sqlite(self.accounts,Path(folder),username)
            self.assertEqual(len(self.stores[0].meters()),1)
    def test_actual_concurrent_workers_claim_distinct_households(self):
        from powerall.jobs import Worker,QueuedSync
        from powerall.providers import Meter
        barrier=threading.Barrier(2);seen=[]
        for user in self.ids:self.accounts.set_credentials(user,'fixture','fixture');QueuedSync(self.database,user).trigger(1)
        class Provider:
            name='bright'
            def __init__(self,accounts,user):self.user=user
            def discover(self):
                seen.append(self.user);barrier.wait(timeout=10);return [Meter('e','r',None,'Fixture')]
            def tariffs(self,*args):return [],[]
            def readings(self,*args):return []
        with patch('powerall.jobs.LimitedBright',Provider):
            workers=[threading.Thread(target=Worker(self.accounts).once) for _ in range(2)]
            for worker in workers:worker.start()
            for worker in workers:worker.join(timeout=15);self.assertFalse(worker.is_alive())
        self.assertCountEqual(seen,self.ids)
    def test_global_http_throttle_and_cooldown(self):
        from powerall.jobs import LimitedBright
        user=self.ids[0];self.accounts.set_credentials(user,'fixture','fixture')
        first=LimitedBright(self.accounts,user);second=LimitedBright(self.accounts,user)
        with self.accounts.db() as db:db.execute("UPDATE provider_limits SET next_at=0,blocked_until=0 WHERE provider='bright'")
        self.assertIsNotNone(first.provider().before_request)
        first.reserve();before=time.time();second.reserve()
        self.assertGreaterEqual(time.time()-before,0.8)
        first.cooldown(1);before=time.time();second.reserve()
        self.assertGreaterEqual(time.time()-before,0.8)
    def test_credentials_unique_invites_and_throttle_work_on_postgres(self):
        from powerall.auth import AuthError
        self.accounts.set_credentials(self.ids[0],'fixture-provider','fixture-provider-password')
        self.assertEqual(self.accounts.credentials(self.ids[0])['username'],'fixture-provider')
        self.assertIsNone(self.accounts.credentials(self.ids[1]))
        with self.accounts.db() as db:username=db.execute('SELECT username FROM users WHERE id=?',(self.ids[0],)).fetchone()['username']
        invite=self.accounts.invite()
        with self.assertRaises(AuthError):self.accounts.register(username,'fixture-long-passphrase',invite,'fixture-unique-client')
        # Failed duplicate registration must roll back invite consumption.
        name='fixture-'+secrets.token_hex(8)
        token=self.accounts.register(name,'fixture-long-passphrase',invite,'fixture-unique-client')
        self.ids.append(self.accounts.session(token)['user_id'])
        with self.assertRaises(AuthError):self.accounts.register('fixture-'+secrets.token_hex(8),'fixture-long-passphrase',invite,'fixture-unique-client')
        for _ in range(10):self.accounts.throttle('fixture-throttle-client',self.ids[0])
        with self.assertRaises(AuthError):self.accounts.throttle('fixture-throttle-client',self.ids[0])
    def test_replaced_worker_cannot_write_with_stale_ownership(self):
        from powerall.postgres import PostgresStore
        from powerall.jobs import QueuedSync
        user=self.ids[0];QueuedSync(self.database,user).trigger()
        with self.accounts.db() as db:db.execute("UPDATE import_jobs SET state='running',owner='old-owner' WHERE user_id=?",(user,))
        stale=PostgresStore(self.database,user,'old-owner');self.seed(stale)
        with self.accounts.db() as db:db.execute("UPDATE import_jobs SET owner='new-owner' WHERE user_id=?",(user,))
        with self.assertRaises(RuntimeError):stale.execute("INSERT INTO readings(meter_id,start,kwh,updated) VALUES('same-meter',EXTRACT(EPOCH FROM NOW())::bigint,'99',1)")
        current=PostgresStore(self.database,user,'new-owner')
        current.execute("INSERT INTO readings(meter_id,start,kwh,updated) VALUES('same-meter',EXTRACT(EPOCH FROM NOW())::bigint,'2',1)")
        self.assertEqual(self.stores[0].rows('SELECT kwh FROM readings')[0]['kwh'],'2')
    def test_housekeeping_prunes_every_household_and_expired_sessions(self):
        from powerall.jobs import Worker
        for store in self.stores:
            store.execute("INSERT INTO readings(meter_id,start,kwh,updated) VALUES('fixture-old',1,'2',1)")
            store.execute("INSERT INTO readings(meter_id,start,kwh,updated) VALUES('fixture-new',?,'3',1)",(int(time.time()),))
        token=self.accounts.new_session(self.ids[0])
        with self.accounts.db() as db:db.execute('UPDATE sessions SET expires=1 WHERE user_id=?',(self.ids[0],))
        Worker(self.accounts).prune()
        for store in self.stores:self.assertEqual(store.rows('SELECT meter_id FROM readings'),[{'meter_id':'fixture-new'}])
        self.assertIsNone(self.accounts.session(token))
    def test_concurrent_password_reset_revokes_late_login_session(self):
        from powerall.auth import password_hash,password_matches
        user=self.ids[0]
        with self.accounts.db() as db:username=db.execute('SELECT username FROM users WHERE id=?',(user,)).fetchone()['username']
        entered=threading.Event();release=threading.Event();reset_started=threading.Event();tokens=[];errors=[]
        replacement=password_hash('fixture-replacement-passphrase')
        def matching(password,encoded):
            entered.set();release.wait(timeout=10);return password_matches(password,encoded)
        def login():
            try:tokens.append(self.accounts.login(username,'invalid-account-placeholder','fixture-login-race'))
            except Exception as error:errors.append(error)
        def reset():
            try:
                reset_started.set()
                with self.accounts.db() as db:
                    db.execute('UPDATE users SET password=? WHERE id=?',(replacement,user))
                    db.execute('DELETE FROM sessions WHERE user_id=?',(user,))
            except Exception as error:errors.append(error)
        with patch('powerall.auth.password_matches',matching):
            login_thread=threading.Thread(target=login);login_thread.start();self.assertTrue(entered.wait(timeout=10))
            reset_thread=threading.Thread(target=reset);reset_thread.start();self.assertTrue(reset_started.wait(timeout=10));release.set()
            login_thread.join(timeout=10);reset_thread.join(timeout=10)
        self.assertFalse(login_thread.is_alive());self.assertFalse(reset_thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(len(tokens),1);self.assertIsNone(self.accounts.session(tokens[0]))
