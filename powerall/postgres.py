"""Shared PostgreSQL persistence with transaction-scoped tenant row security."""
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
import psycopg
from psycopg_pool import ConnectionPool
from .auth import Accounts, password_hash
from .storage import Store
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class Row(dict):
    # Keep the existing SQLite storage interface for both persistence adapters.
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)

def row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))

class DB:
    def __init__(self, connection, tenant=False):
        self.raw, self.tenant = connection, tenant
    def query(self, sql):
        if sql == 'BEGIN IMMEDIATE':
            # Serialize invite consumption/capacity and throttling across replicas.
            return 'SELECT pg_advisory_xact_lock(41001)'
        sql = sql.replace('?', '%s')
        if self.tenant:
            sql = re.sub(r'ON CONFLICT\(([^)]+)\)', r'ON CONFLICT(tenant_id,\1)', sql, flags=re.I)
        return sql
    def execute(self, sql, args=()):
        sql = self.query(sql)
        returning = self.tenant and bool(re.match(r'\s*INSERT INTO sync_runs', sql, re.I))
        if returning: sql += ' RETURNING id'
        result = self.raw.execute(sql, args)
        if returning:
            return type('Inserted', (), {'lastrowid':result.fetchone()['id']})()
        return result
    def executemany(self, sql, args):
        return self.raw.cursor().executemany(self.query(sql), args)

class Database:
    def __init__(self, conninfo=None):
        if conninfo is None:
            password = Path(os.environ['POWERALL_DATABASE_PASSWORD_FILE']).read_text().strip()
            conninfo = psycopg.conninfo.make_conninfo(host=os.environ.get('POWERALL_DATABASE_HOST', 'database'),
                dbname=os.environ.get('POWERALL_DATABASE_NAME', 'powerall'), user=os.environ.get('POWERALL_DATABASE_USER','powerall'), password=password,
                sslmode=os.environ.get('POWERALL_DATABASE_SSLMODE','prefer'),
                **({'sslrootcert':os.environ['POWERALL_DATABASE_SSLROOTCERT']} if os.environ.get('POWERALL_DATABASE_SSLROOTCERT') else {}),
                connect_timeout=10, application_name='powerall')
        maximum=int(os.environ.get('POWERALL_DB_POOL_SIZE','8'))
        if maximum<3: raise ValueError('Database pool needs at least three connections for imports and heartbeat.')
        self.pool = ConnectionPool(conninfo, min_size=1, max_size=maximum,
            timeout=10, max_waiting=64, kwargs={'row_factory': row_factory}, open=True)
        self.pool.wait(timeout=30)
    @contextmanager
    def connection(self, tenant=None):
        with self.pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant or '',))
                yield DB(connection, tenant is not None)
    def close(self): self.pool.close()

class PostgresStore(Store):
    def __init__(self, database, tenant, owner=None):
        self.database, self.tenant, self.owner = database, tenant, owner
    @contextmanager
    def connection(self):
        with self.database.connection(self.tenant) as db:
            if self.owner is not None:
                # Hold the ownership row through each transaction. A replaced worker
                # cannot write after losing its advisory-lock connection.
                owned=db.execute("SELECT user_id FROM import_jobs WHERE user_id=? AND owner=? AND state='running' FOR SHARE",(self.tenant,self.owner)).fetchone()
                if not owned: raise RuntimeError('Import ownership lost.')
            yield db
    def execute(self, sql, args=()):
        with self.connection() as db:
            return getattr(db.execute(sql, args), 'lastrowid', None)
    def rows(self, sql, args=()):
        return [{k:v for k,v in r.items() if k != 'tenant_id'} for r in super().rows(sql, args)]
    def sync_state(self):
        state=super().sync_state()
        with self.database.connection() as db:
            job=db.execute("SELECT state,available_at FROM import_jobs WHERE user_id=? AND state<>'idle'",(self.tenant,)).fetchone()
        state['queue']=dict(job) if job else None
        return state


class PostgresAccounts(Accounts):
    def __init__(self, database, root, key):
        self.database, self.root = database, Path(root)
        if len(key) != 32: raise ValueError('Invalid encryption key.')
        self.cipher, self.max_users = AESGCM(key), None
        self.dummy_hash = password_hash('invalid-account-placeholder')
    @staticmethod
    def integrity_errors(): return (psycopg.IntegrityError,)
    def db(self): return self.database.connection()
    def delete(self, user_id, record=True):
        with self.db() as db:
            if record: db.execute('INSERT INTO erasures VALUES(?,?) ON CONFLICT DO NOTHING', (user_id, int(time.time())))
            db.execute('DELETE FROM users WHERE id=?', (user_id,))


def migrate(database):
    """Versioned, serialized DDL runs in the deployment's one-shot migration job."""
    with database.connection() as db:
        db.execute('SELECT pg_advisory_xact_lock(41000)')
        db.raw.execute(Path(__file__).with_name('schema.sql').read_text())
