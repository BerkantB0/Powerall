"""Hosted security regressions. All accounts/credentials are disposable fixtures."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.exceptions import InvalidTag
from powerall.auth import Accounts, AuthError, COOKIE, digest
from powerall.hosted import create_app

class HostedTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.key = os.urandom(32)
        self.accounts = Accounts(Path(self.folder.name)/'data', self.key)
        self.app = create_app(self.accounts, 'https://energy.example.com', 'internal-' + 'x'*40, automatic=False)
        self.token = self.accounts.register('alice', 'fixture-password-long', self.accounts.invite(), 'alice-ip')
        self.other = self.accounts.register('bob', 'fixture-password-long', self.accounts.invite(), 'bob-ip')
        self.alice = self.accounts.session(self.token)
        self.bob = self.accounts.session(self.other)
    def tearDown(self):
        self.app.tenants.stop(); self.folder.cleanup()
    def call(self, path, body=None, token=None, csrf=True, origin='https://energy.example.com', internal=True, extra=None):
        raw = json.dumps(body).encode() if body is not None else b''
        route, _, query = path.partition('?')
        env = {'REQUEST_METHOD': 'POST' if body is not None else 'GET', 'PATH_INFO':route,
            'QUERY_STRING':query, 'wsgi.input':io.BytesIO(raw), 'CONTENT_LENGTH':str(len(raw)),
            'CONTENT_TYPE':'application/json', 'HTTP_ORIGIN':origin, 'HTTP_X_POWERALL_CLIENT_IP':'test-ip'}
        if internal: env['HTTP_X_POWERALL_INTERNAL']='internal-'+'x'*40
        if token:
            env['HTTP_COOKIE']=f'{COOKIE}={token}'
            session=self.accounts.session(token)
            if csrf and session: env['HTTP_X_POWERALL_TOKEN']=session['csrf']
        if extra: env.update(extra)
        result={}
        def start(status, headers): result.update(code=int(status.split()[0]), headers=dict(headers))
        content=b''.join(self.app(env,start))
        return result['code'],json.loads(content),result['headers']
    def test_every_data_route_requires_session_and_internal_auth(self):
        for path in ('/api/status','/api/dashboard?meter=m','/api/tariffs?meter=m','/api/account/export'):
            self.assertEqual(self.call(path)[0],401)
            self.assertEqual(self.call(path,token=self.token,internal=False)[0],403)
        self.assertEqual(self.call('/api/config')[0],200)
        self.assertEqual(self.call('/api/status',token=self.token)[1]['username'],'alice')
    def test_origin_csrf_and_fetch_site_guards(self):
        self.assertEqual(self.call('/api/auth/login',{},origin='https://evil.example')[0],403)
        self.assertEqual(self.call('/api/auth/login',{},origin='')[0],403)
        self.assertEqual(self.call('/api/sync',{},token=self.token,csrf=False)[0],403)
        self.assertEqual(self.call('/api/sync',{},token=self.token,extra={'HTTP_X_POWERALL_TOKEN':self.bob['csrf']})[0],403)
        self.assertEqual(self.call('/api/sync',{},token=self.token,extra={'HTTP_SEC_FETCH_SITE':'cross-site'})[0],403)
        self.assertEqual(self.call('/api/sync',{},token=self.token,origin='https://evil.example')[0],403)
    def test_cross_tenant_ids_cannot_read_edit_delete_or_export_data(self):
        store,_=self.app.tenants.get(self.alice['user_id'])
        store.execute("INSERT INTO meters(id,provider,provider_entity,resource_id,name) VALUES('private-meter','bright','private-entity','private-resource','Alice home')")
        payload={'meter':'private-meter','label':'Private tariff','from':'2026-01-01','rate':'30','standing':'50','windows':[]}
        code,saved,_=self.call('/api/tariffs',payload,token=self.token);self.assertEqual(code,201)
        self.assertEqual(self.call('/api/dashboard?meter=private-meter&view=day&date=2026-01-02',token=self.other)[0],404)
        self.assertEqual(self.call('/api/tariffs?meter=private-meter',token=self.other)[1]['tariffs'],[])
        self.assertEqual(self.call('/api/tariffs',{**payload,'id':saved['id']},token=self.other)[0],400)
        self.assertEqual(self.call('/api/tariffs/delete',{'id':saved['id']},token=self.other)[0],404)
        export=self.call('/api/account/export',token=self.other)[1]
        self.assertNotIn('Private tariff',json.dumps(export));self.assertEqual(export['meters'],[])
        self.assertEqual(len(store.tariffs('private-meter')),1)
    def test_registration_is_invite_only_single_use_and_cookie_secure(self):
        payload={'username':'charlie','password':'fixture-password-long','invite':'invalid'}
        self.assertEqual(self.call('/api/auth/register',payload)[0],400)
        invite=self.accounts.invite();payload['invite']=invite
        code,_,headers=self.call('/api/auth/register',payload);self.assertEqual(code,200)
        cookie=headers['Set-Cookie']
        for attribute in ('__Host-powerall_session=','Secure','HttpOnly','SameSite=Lax','Path=/'):self.assertIn(attribute,cookie)
        self.assertEqual(self.call('/api/auth/register',{**payload,'username':'dora'})[0],400)
        with self.accounts.db() as db:
            self.assertIsNone(db.execute('SELECT token FROM invites WHERE token=?',(digest(invite),)).fetchone())
    def test_passwords_and_sessions_not_stored_plaintext_and_logout_expires(self):
        with self.accounts.db() as db:
            row=db.execute('SELECT password FROM users WHERE username=?',('alice',)).fetchone()
            self.assertNotIn('fixture-password-long',row['password'])
            self.assertIsNone(db.execute('SELECT token FROM sessions WHERE token=?',(self.token,)).fetchone())
        self.assertEqual(self.call('/api/auth/logout',{},token=self.token)[0],200)
        self.assertEqual(self.call('/api/status',token=self.token)[0],401)
    def test_expired_session_refused_and_password_change_revokes_other_sessions(self):
        second=self.accounts.new_session(self.alice['user_id'])
        with self.accounts.db() as db: db.execute('UPDATE sessions SET expires=0 WHERE token=?',(digest(second),))
        self.assertEqual(self.call('/api/status',token=second)[0],401)
        another=self.accounts.new_session(self.alice['user_id'])
        code,_,headers=self.call('/api/auth/password',{'password':'fixture-password-long','new_password':'different-fixture-passphrase'},token=self.token)
        self.assertEqual(code,200);self.assertIsNone(self.accounts.session(another));self.assertIsNone(self.accounts.session(self.token))
        self.assertIn('Set-Cookie',headers)
    def test_credentials_encrypted_bound_to_user_and_never_exported(self):
        with patch('powerall.sync.SyncService.trigger',return_value=True):
            code,_,_=self.call('/api/connection',{'username':'fixture@example.test','password':'fixture-bright-secret'},token=self.token)
        self.assertEqual(code,202)
        self.assertEqual(self.accounts.credentials(self.alice['user_id'])['password'],'fixture-bright-secret')
        with self.accounts.db() as db:
            value=db.execute('SELECT credentials FROM users WHERE id=?',(self.alice['user_id'],)).fetchone()[0]
            self.assertNotIn(b'fixture-bright-secret',value)
            db.execute('UPDATE users SET credentials=? WHERE id=?',(value,self.bob['user_id']))
        with self.assertRaises(InvalidTag): self.accounts.credentials(self.bob['user_id'])
        export=json.dumps(self.call('/api/account/export',token=self.token)[1])
        self.assertNotIn('fixture-bright-secret',export);self.assertNotIn('credentials',export)
        with self.accounts.db() as db: db.execute('UPDATE users SET credentials=NULL WHERE id=?',(self.bob['user_id'],))
        self.assertEqual(self.call('/api/connection',{'disconnect':True},token=self.token)[0],200)
        self.assertIsNone(self.accounts.credentials(self.alice['user_id']))
    def test_account_deletion_requires_password_and_removes_only_owner(self):
        store,sync=self.app.tenants.get(self.alice['user_id'])
        self.assertEqual(self.call('/api/account/delete',{'password':'wrong'},token=self.token)[0],401)
        self.assertTrue(store.path.exists())
        self.assertEqual(self.call('/api/account/delete',{'password':'fixture-password-long'},token=self.token)[0],200)
        self.assertFalse(store.path.parent.exists());self.assertIsNone(self.accounts.session(self.token))
        self.assertIsNotNone(self.accounts.session(self.other));self.assertFalse(sync.trigger())
    def test_running_import_prevents_disconnect_or_delete(self):
        _,sync=self.app.tenants.get(self.alice['user_id']);sync._lock.acquire()
        try:
            self.assertEqual(self.call('/api/connection',{'disconnect':True},token=self.token)[0],409)
            self.assertEqual(self.call('/api/account/delete',{'password':'fixture-password-long'},token=self.token)[0],409)
        finally:sync._lock.release()
    def test_login_errors_do_not_enumerate_accounts_and_attempts_are_bounded(self):
        for name in ('alice','nonexistent'):
            code,payload,_=self.call('/api/auth/login',{'username':name,'password':'wrong'})
            self.assertEqual(code,401);self.assertEqual(payload['error'],'Username or password is incorrect.')
        for _ in range(9):self.call('/api/auth/login',{'username':'alice','password':'wrong'})
        self.assertEqual(self.call('/api/auth/login',{'username':'alice','password':'fixture-password-long'})[0],429)
    def test_encrypted_backup_tamper_rejection_and_restored_account_reset(self):
        key_file=Path(self.folder.name)/'encryption_key';key_file.write_text(self.key.hex())
        backup_key=Path(self.folder.name)/'backup_key';backup_key.write_text(os.urandom(32).hex())
        env={**os.environ,'POWERALL_DATA_DIR':str(self.accounts.root),'POWERALL_ENCRYPTION_KEY_FILE':str(key_file),'POWERALL_BACKUP_KEY_FILE':str(backup_key)}
        self.accounts.set_credentials(self.alice['user_id'],'fixture@example.test','fixture-bright-secret')
        self.app.tenants.get(self.alice['user_id'])
        command=[sys.executable,'-m','powerall.manage']
        encrypted=subprocess.run([*command,'backup'],env=env,check=True,capture_output=True).stdout
        self.assertNotIn(b'fixture-bright-secret',encrypted)
        archive=Path(self.folder.name)/'backup.enc';archive.write_bytes(encrypted)
        output=Path(self.folder.name)/'recovery.tar.gz'
        subprocess.run([sys.executable,'scripts/decrypt-backup.py',str(archive),str(backup_key),str(output)],check=True,capture_output=True)
        import tarfile
        with tarfile.open(output,'r:gz') as recovered:
            self.assertIn('accounts.sqlite3',recovered.getnames())
            self.assertNotIn('erasures.txt',recovered.getnames())
        output.unlink()
        altered=bytearray(encrypted);altered[len(altered)//2]^=1;archive.write_bytes(altered)
        result=subprocess.run([sys.executable,'scripts/decrypt-backup.py',str(archive),str(backup_key),str(output)],capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertFalse(output.exists())
        subprocess.run([*command,'secure-restored-accounts'],env=env,check=True,capture_output=True)
        self.assertIsNone(self.accounts.session(self.token));self.assertIsNone(self.accounts.credentials(self.alice['user_id']))
        with self.assertRaises(AuthError):self.accounts.login('alice','fixture-password-long','restore-ip')

    def test_housekeeping_prunes_inactive_household_and_expired_auth_records(self):
        store,_=self.app.tenants.get(self.alice['user_id'])
        store.execute("INSERT INTO readings VALUES('meter',1,'1',1)")
        with self.accounts.db() as db:
            db.execute("INSERT INTO attempts VALUES('expired',1)")
            db.execute("INSERT INTO invites VALUES('expired',1)")
            db.execute('UPDATE sessions SET expires=1 WHERE token=?',(digest(self.other),))
        self.app.tenants.prune()
        self.assertEqual(store.rows('SELECT * FROM readings'),[])
        with self.accounts.db() as db:
            self.assertIsNone(db.execute("SELECT * FROM attempts WHERE key='expired'").fetchone())
            self.assertIsNone(db.execute("SELECT * FROM invites WHERE token='expired'").fetchone())
        self.assertIsNone(self.accounts.session(self.other))
        self.assertIsNotNone(self.accounts.session(self.token))

    def test_expired_invitation_refused(self):
        invite=self.accounts.invite(hours=-1)
        self.assertEqual(self.call('/api/auth/register',{'username':'expired','password':'fixture-password-long','invite':invite})[0],400)

    def test_operator_can_pause_bright_access(self):
        environment={'POWERALL_OPERATOR_NAME':'Fixture operator','POWERALL_PRIVACY_CONTACT':'fixture@example.test',
            'POWERALL_HOSTING_LOCATION':'Test host','POWERALL_PRIVACY_LEGAL_BASIS':'Fixture test only', 'POWERALL_BRIGHT_ENABLED':'false'}
        with patch.dict(os.environ,environment):
            app=create_app(self.accounts,'https://energy.example.com','internal-'+'x'*40,automatic=True)
        self.assertFalse(app.tenants.automatic)
        app.tenants.stop()
        with patch.dict(os.environ,{'POWERALL_IMPORT_DAYS':'731'}):
            with self.assertRaises(ValueError):create_app(self.accounts,'https://energy.example.com','internal-'+'x'*40,automatic=False)

    def test_fail_closed_config_body_bounds_and_registration_limit(self):
        for origin in ('http://energy.example.com','https://energy.example.com/','https://energy.example.com/path',''):
            with self.assertRaises(ValueError):create_app(self.accounts,origin,'internal-'+'x'*40,automatic=False)
        self.accounts.max_users=2
        with self.assertRaises(AuthError):self.accounts.register('extra','fixture-password-long',self.accounts.invite(),'extra-ip')
        self.assertEqual(self.call('/api/auth/login',{},extra={'CONTENT_LENGTH':'20000'})[0],400)

if __name__=='__main__': unittest.main()
