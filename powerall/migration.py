"""Explicit, transactional imports. Never discovers local credentials or data automatically."""
import json
import sqlite3
from contextlib import closing
from .postgres import PostgresStore

TABLES={'meters':('id','provider','provider_entity','resource_id','cost_resource_id','name','unit','active'),
    'readings':('meter_id','start','kwh','updated'),
    'tariffs':('id','meter_id','source','label','effective_from','effective_to','rate','standing','windows','clock','vat','warning'),
    'sync_runs':('id','started','finished','status','phase','imported','error','warnings'),
    'settings':('key','value')}

def migrate_sqlite(accounts,source,username=None):
    if not source.is_dir(): raise ValueError('Source must be a mounted read-only data directory.')
    with accounts.database.connection() as db:
        db.execute('SELECT pg_advisory_xact_lock(41001)')
        if username:
            user=db.execute('SELECT id FROM users WHERE username=?',(accounts.username(username),)).fetchone()
            if not user: raise ValueError('Create the destination account first.')
            users=[{'id':user['id']}]
        else:
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]: raise ValueError('Hosted migration needs an empty destination.')
            with closing(sqlite3.connect(f'file:{source / "accounts.sqlite3"}?mode=ro',uri=True)) as sqlite:
                sqlite.row_factory=sqlite3.Row
                users=[dict(r) for r in sqlite.execute('SELECT * FROM users')]
            for user in users:
                if user['credentials']:
                    # Verify that the configured encryption key belongs to the old deployment.
                    value=user['credentials']; accounts.cipher.decrypt(value[:12],value[12:],user['id'].encode())
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)',tuple(user[k] for k in ('id','username','password','credentials','created')))
            ledger=source/'erasures.txt'
            if ledger.exists():
                import uuid
                for line in ledger.read_text().splitlines():
                    user_id=str(uuid.UUID(line))
                    db.execute('INSERT INTO erasures VALUES(?,0) ON CONFLICT DO NOTHING',(user_id,))
        maximum=0
        for user in users:
            db.raw.execute("SELECT set_config('app.tenant_id',%s,true)",(user['id'],))
            if db.execute('SELECT COUNT(*) FROM meters').fetchone()[0]: raise ValueError('Destination household is not empty.')
            path=source/'powerall.sqlite3' if username else source/'households'/user['id']/'powerall.sqlite3'
            if not path.exists(): continue
            with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True)) as sqlite:
                for table,columns in TABLES.items():
                    cursor=sqlite.execute(f'SELECT {",".join(columns)} FROM {table}')
                    while rows:=cursor.fetchmany(1000):
                        db.executemany(f'INSERT INTO {table}({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',rows)
                        if table=='sync_runs': maximum=max(maximum,max(r[0] for r in rows))
            db.execute("UPDATE sync_runs SET status='failed',phase='Interrupted',error='Import interrupted by migration.' WHERE status='running'")
        # setval is monotonic: a migration must never reuse a previously issued identity.
        if maximum: db.execute("SELECT setval('sync_runs_id_seq',GREATEST(?,(SELECT last_value FROM sync_runs_id_seq)))",(maximum,))
        db.raw.execute("SELECT set_config('app.tenant_id','',true)")
        db.execute('DELETE FROM users WHERE id IN (SELECT user_id FROM erasures)')
