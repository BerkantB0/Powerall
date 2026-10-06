"""Hosted accounts. Session/invite tokens are hashed; provider credentials use AEAD."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

COOKIE = '__Host-powerall_session'
ITERATIONS = 600_000
SESSION_SECONDS = 7 * 86400

class AuthError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password, salt=None):
    if not isinstance(password, str) or not 15 <= len(password) <= 256:
        raise AuthError('Use a password or passphrase between 15 and 256 characters.')
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${value}'

def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 256:
        return False
    _, iterations, salt, expected = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    return hmac.compare_digest(actual, expected)

class Accounts:
    def __init__(self, root, key):
        if len(key) != 32:
            raise ValueError('Credential encryption key must contain exactly 32 bytes.')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.path = self.root / 'accounts.sqlite3'
        self.cipher = AESGCM(key)
        self.max_users = None
        self.dummy_hash = password_hash('invalid-account-placeholder')
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL, credentials BLOB, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    csrf TEXT NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(key TEXT NOT NULL, instant INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_lookup ON attempts(key,instant);
            ''')
        os.chmod(self.path, 0o600)

    @staticmethod
    def integrity_errors():
        return (sqlite3.IntegrityError,)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA secure_delete=ON')
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def prune(self):
        now = int(time.time())
        with self.db() as db:
            db.execute('DELETE FROM attempts WHERE instant<?', (now - 900,))
            db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
            db.execute('DELETE FROM invites WHERE expires<=?', (now,))

    def invite(self, hours=48):
        value = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM invites WHERE expires<=?', (int(time.time()),))
            db.execute('INSERT INTO invites VALUES(?,?)', (digest(value), int(time.time()) + hours * 3600))
        return value

    def throttle(self, client, username):
        now = int(time.time())
        keys = [digest('ip:' + client), digest('account:' + username)]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE instant<?', (now - 900,))
            for key, limit in zip(keys, (30, 10)):
                count = db.execute('SELECT COUNT(*) FROM attempts WHERE key=?', (key,)).fetchone()[0]
                if count >= limit:
                    raise AuthError('Too many attempts. Try again in 15 minutes.', 429)
            db.executemany('INSERT INTO attempts VALUES(?,?)', [(key, now) for key in keys])

    def register(self, username, password, invitation, client):
        username = self.username(username)
        self.throttle(client, username)
        encoded = password_hash(password)
        user_id = str(uuid.uuid4())
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            invite = db.execute('SELECT token FROM invites WHERE token=? AND expires>?',
                (digest(str(invitation)), int(time.time()))).fetchone()
            if not invite:
                raise AuthError('Invitation is invalid or expired.')
            if self.max_users is not None and db.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= self.max_users:
                raise AuthError('Registration is currently at capacity.', 409)
            try:
                db.execute('INSERT INTO users VALUES(?,?,?,NULL,?)', (user_id, username, encoded, int(time.time())))
            except self.integrity_errors():
                raise AuthError('Registration could not be completed. Choose another username.') from None
            db.execute('DELETE FROM invites WHERE token=?', (invite['token'],))
        return self.new_session(user_id)

    @staticmethod
    def username(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', value):
            raise AuthError('Use a username of 3–64 letters, numbers, dots, dashes or underscores.')
        return value.lower()

    def login(self, username, password, client):
        # Invalid names still consume an attempt and run the password KDF.
        username = str(username).lower()[:256]
        self.throttle(client, username)
        with self.db() as db:
            suffix=' FOR UPDATE' if hasattr(self,'database') else ''
            user = db.execute('SELECT * FROM users WHERE username=?'+suffix, (username,)).fetchone()
            valid = password_matches(password, user['password'] if user else self.dummy_hash)
            if not user or not valid:
                raise AuthError('Username or password is incorrect.', 401)
            # Keep the PostgreSQL user row locked through session creation. A concurrent
            # password reset then revokes this session instead of missing a late insert.
            return self.new_session(user['id'],db)

    def new_session(self, user_id, existing_db=None):
        token = secrets.token_urlsafe(48)
        csrf = secrets.token_urlsafe(32)
        expires = int(time.time()) + SESSION_SECONDS
        with (nullcontext(existing_db) if existing_db is not None else self.db()) as db:
            db.execute('DELETE FROM sessions WHERE expires<=?', (int(time.time()),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), user_id, csrf, expires))
            # Bound simultaneous sessions per account.
            db.execute('DELETE FROM sessions WHERE user_id=? AND token NOT IN '
                '(SELECT token FROM sessions WHERE user_id=? ORDER BY expires DESC LIMIT 10)', (user_id, user_id))
        return token

    def session(self, token):
        with self.db() as db:
            row = db.execute('SELECT s.*,u.username,u.credentials IS NOT NULL AS connected FROM sessions s '
                'JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',
                (digest(token), int(time.time()))).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (digest(token),))

    def verify_password(self, user_id, password, client):
        self.throttle(client, user_id)
        with self.db() as db:
            row = db.execute('SELECT password FROM users WHERE id=?', (user_id,)).fetchone()
        if not row or not password_matches(password, row['password']):
            raise AuthError('Password is incorrect.', 401)

    def change_password(self, user_id, old, new, client):
        self.verify_password(user_id, old, client)
        encoded = password_hash(new)
        with self.db() as db:
            if hasattr(self,'database'):
                current=db.execute('SELECT password FROM users WHERE id=? FOR UPDATE',(user_id,)).fetchone()
                if not current or not password_matches(old,current['password']): raise AuthError('Password is incorrect.',401)
            db.execute('UPDATE users SET password=? WHERE id=?', (encoded, user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            return self.new_session(user_id,db)

    def set_credentials(self, user_id, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or not username.strip() or not password or len(username) > 320 or len(password) > 1024:
            raise AuthError('Enter your Bright username and password.')
        nonce = os.urandom(12)
        encrypted = nonce + self.cipher.encrypt(nonce, json.dumps({'username': username.strip(), 'password': password}).encode(), user_id.encode())
        with self.db() as db:
            db.execute('UPDATE users SET credentials=? WHERE id=?', (encrypted, user_id))

    def credentials(self, user_id):
        with self.db() as db:
            row = db.execute('SELECT credentials FROM users WHERE id=?', (user_id,)).fetchone()
        if not row or not row['credentials']:
            return None
        encrypted = row['credentials']
        return json.loads(self.cipher.decrypt(encrypted[:12], encrypted[12:], user_id.encode()))

    def disconnect(self, user_id):
        with self.db() as db:
            db.execute('UPDATE users SET credentials=NULL WHERE id=?', (user_id,))

    def delete(self, user_id, record=True):
        if record:
            ledger = self.root / 'erasures.txt'
            fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'w') as file:
                file.write(user_id + '\n'); file.flush(); os.fsync(file.fileno())
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM users WHERE id=?', (user_id,))
