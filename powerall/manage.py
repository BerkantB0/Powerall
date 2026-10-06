"""Operator actions run inside the private API container; no browser admin API."""
import argparse
import getpass
import os
import secrets
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path
from .auth import Accounts, password_hash
from .hosted import read_secret

def accounts():
    key=bytes.fromhex(read_secret('POWERALL_ENCRYPTION_KEY',64).decode())
    if os.environ.get('POWERALL_DATABASE_PASSWORD_FILE'):
        from .postgres import Database, PostgresAccounts
        return PostgresAccounts(Database(),os.environ.get('POWERALL_DATA_DIR','/data'),key)
    return Accounts(os.environ.get('POWERALL_DATA_DIR', '/data'),key)

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    invite = sub.add_parser('invite'); invite.add_argument('--hours', type=int, default=48)
    reset = sub.add_parser('reset-password'); reset.add_argument('username')
    sub.add_parser('backup')
    sub.add_parser('backup-erasures')
    sub.add_parser('migrate')
    sub.add_parser('status')
    migration=sub.add_parser('migrate-sqlite'); migration.add_argument('--source',required=True); migration.add_argument('--username')
    sub.add_parser('apply-erasures')
    sub.add_parser('secure-restored-accounts')
    args = parser.parse_args()
    store = accounts()
    try:
        if args.command == 'migrate':
            from .postgres import migrate
            migrate(store.database)
            print('PostgreSQL schema ready.')
        elif args.command == 'migrate-sqlite':
            from .migration import migrate_sqlite
            migrate_sqlite(store,Path(args.source),args.username)
            print('SQLite data migrated; source preserved and old sessions excluded.')
        elif args.command == 'status':
            import json,time
            with store.db() as db:
                jobs=[dict(r) for r in db.execute('SELECT state,COUNT(*) AS count,MIN(available_at) AS oldest FROM import_jobs GROUP BY state')]
                workers=db.execute('SELECT COUNT(*) FROM worker_heartbeats WHERE seen_at>?',(int(time.time())-90,)).fetchone()[0]
            print(json.dumps({'workers_alive':workers,'queue':jobs}))
        elif args.command == 'invite':
            if not 1 <= args.hours <= 168: parser.error('Invitation expiry must be between 1 and 168 hours.')
            value = store.invite(args.hours)
            print(f"{os.environ['POWERALL_PUBLIC_ORIGIN']}/login#invite={value}")
        elif args.command == 'reset-password':
            username = store.username(args.username)
            first = getpass.getpass('New password (never passed in command arguments): ')
            second = getpass.getpass('Confirm new password: ')
            if first != second: parser.error('Passwords do not match.')
            encoded = password_hash(first)
            with store.db() as db:
                user = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
                if not user: parser.error('Account not found.')
                db.execute('UPDATE users SET password=? WHERE id=?', (encoded, user['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            print('Password reset; all sessions revoked.')
        elif args.command == 'apply-erasures':
            # Keep the current erasure ledger outside restored archives. Run BEFORE restarting API.
            import shutil
            if hasattr(store,'database'):
                with store.db() as db: ids=[r['user_id'] for r in db.execute('SELECT user_id FROM erasures')]
                for user_id in ids: store.delete(user_id,record=False)
                print('Erased accounts removed from restored data.'); return
            ledger = store.root / 'erasures.txt'
            for user_id in ledger.read_text().splitlines() if ledger.exists() else []:
                import uuid
                if str(uuid.UUID(user_id)) != user_id: raise ValueError('Invalid erasure ledger.')
                store.delete(user_id, record=False)
                folder = store.root / 'households' / user_id
                if folder.exists(): shutil.rmtree(folder)
            print('Erased accounts removed from restored data.')
        elif args.command == 'secure-restored-accounts':
            # A restore must not resurrect old passwords, sessions or disconnected provider access.
            with store.db() as db:
                ids = [r['id'] for r in db.execute('SELECT id FROM users')]
                for user_id in ids:
                    db.execute('UPDATE users SET password=?,credentials=NULL WHERE id=?', (password_hash(secrets.token_urlsafe(48)), user_id))
                db.execute('DELETE FROM sessions')
                if hasattr(store,'database'):
                    db.execute('DELETE FROM import_jobs')
                    db.execute('DELETE FROM worker_heartbeats')
                    db.execute('UPDATE provider_limits SET next_at=0,blocked_until=0')
                    db.execute('UPDATE maintenance_schedule SET next_at=0')
            print('Restored passwords invalidated, sessions revoked and Bright connections removed. Verify account owners before resetting passwords.')
        elif args.command == 'backup-erasures':
            from .backup import backup
            backup(ledger_only=True)
        elif args.command == 'backup':
            if hasattr(store,'database'):
                from .backup import backup
                backup(); return
            # Stop the serving API first for consistency across separate household databases.
            # Stream encrypted output: no plaintext database or archive leaves the container.
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            key = bytes.fromhex(read_secret('POWERALL_BACKUP_KEY', 64).decode())
            nonce = secrets.token_bytes(12)
            encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
            output = sys.stdout.buffer
            output.write(b'POWERALL-BACKUP-1\n' + nonce)
            class Sink:
                def write(self, value): output.write(encryptor.update(value)); return len(value)
            with tempfile.TemporaryDirectory(prefix='powerall-backup-') as folder:
                with tarfile.open(fileobj=Sink(), mode='w|gz') as archive:
                    paths = [store.path, *sorted((store.root / 'households').glob('*/powerall.sqlite3'))]
                    for index, path in enumerate(paths):
                        snapshot = Path(folder) / f'{index}.sqlite3'
                        source = sqlite3.connect(path)
                        target = sqlite3.connect(snapshot)
                        try: source.backup(target)
                        finally: target.close(); source.close()
                        archive.add(snapshot, arcname=str(path.relative_to(store.root)))
                        snapshot.unlink()
            output.write(encryptor.finalize()); output.write(encryptor.tag); output.flush()
    finally:
        if hasattr(store, 'database'): store.database.close()

if __name__ == '__main__': main()
