"""SQLite persistence; connections are short-lived and never contain credentials."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS meters (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_entity TEXT NOT NULL,
 resource_id TEXT NOT NULL UNIQUE, cost_resource_id TEXT, name TEXT NOT NULL,
 unit TEXT NOT NULL DEFAULT 'kWh', active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS readings (
 meter_id TEXT NOT NULL, start INTEGER NOT NULL, kwh TEXT NOT NULL,
 updated INTEGER NOT NULL, PRIMARY KEY(meter_id,start)
);
CREATE TABLE IF NOT EXISTS tariffs (
 id TEXT PRIMARY KEY, meter_id TEXT NOT NULL, source TEXT NOT NULL,
 label TEXT NOT NULL, effective_from INTEGER NOT NULL, effective_to INTEGER,
 rate TEXT NOT NULL, standing TEXT NOT NULL, windows TEXT NOT NULL,
 clock TEXT NOT NULL, vat TEXT NOT NULL, warning TEXT
);
CREATE INDEX IF NOT EXISTS tariff_lookup ON tariffs(meter_id,effective_from);
CREATE TABLE IF NOT EXISTS sync_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, started INTEGER NOT NULL,
 finished INTEGER, status TEXT NOT NULL, phase TEXT NOT NULL,
 imported INTEGER NOT NULL DEFAULT 0, error TEXT, warnings TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        with self.connection() as db:
            db.executescript(SCHEMA)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connection() as db:
            return [dict(r) for r in db.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.connection() as db:
            return db.execute(sql, args).lastrowid

    def meters(self):
        return self.rows('SELECT id,provider,name,active FROM meters ORDER BY active DESC,name')

    def tariffs(self, meter):
        rows = self.rows('SELECT * FROM tariffs WHERE meter_id=? ORDER BY effective_from DESC', (meter,))
        for row in rows:
            row['windows'] = json.loads(row['windows'])
        return rows

    def upsert_tariff(self, item):
        self.execute('''INSERT INTO tariffs(id,meter_id,source,label,effective_from,effective_to,rate,standing,windows,clock,vat,warning)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            label=excluded.label,effective_from=excluded.effective_from,effective_to=excluded.effective_to,
            rate=excluded.rate,standing=excluded.standing,windows=excluded.windows,clock=excluded.clock,
            vat=excluded.vat,warning=excluded.warning''', tuple(item[k] if k != 'windows' else json.dumps(item[k]) for k in
                ('id','meter_id','source','label','effective_from','effective_to','rate','standing','windows','clock','vat','warning')))

    def sync_state(self):
        latest = self.rows('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1')
        success = self.rows("SELECT finished FROM sync_runs WHERE status IN ('success','partial') ORDER BY id DESC LIMIT 1")
        run = latest[0] if latest else None
        if run:
            run['warnings'] = json.loads(run['warnings'])
        return {'run': run, 'last_success': success[0]['finished'] if success else None}
