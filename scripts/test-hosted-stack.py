#!/usr/bin/env python3
"""Disposable Docker integration test. Publishes HTTPS only on loopback; no Bright calls."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPSHandler, HTTPCookieProcessor
from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parent.parent

def run(command, **kwargs):
    result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kwargs)
    if result.returncode:
        raise RuntimeError(f'Test command failed ({result.returncode}): {result.stderr.decode()[-8000:]}')
    return result.stdout

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--npm',action='store_true',help='Test HTTPS nginx -> private Caddy, matching NPM forwarding headers')
    mode=parser.parse_args().npm
    with tempfile.TemporaryDirectory(prefix='powerall-docker-test-') as temporary:
        folder = Path(temporary)
        if mode: folder.chmod(0o711)  # Allow the non-root test nginx to traverse its fixture mount.
        command=['docker','compose','--env-file','deploy/hosting.env.example','--profile','tools']
        environment=dict(os.environ)
        if mode:
            command += ['-f','compose.yaml','-f','compose.npm.yaml']
            environment.update(POWERALL_PROXY_NETWORK='powerall-test-proxy',POWERALL_TRUSTED_PROXY_CIDRS='192.0.2.2/32')
        config = json.loads(run([*command,'config','--format','json'],env=environment))
        config['name'] = 'powerall-test-' + secrets.token_hex(4)
        for section in ('networks','volumes'):
            for value in config[section].values(): value.pop('name', None)
        for name in ('encryption_key','internal_token','backup_key','database_password','database_admin_password'):
            path=folder/name;path.write_text(secrets.token_hex(32)+'\n');path.chmod(0o444)
            config['secrets'][name]['file']=str(path)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        origin=f'https://localhost:{port}'
        for name,image in (('api','powerall-api'),('frontend','powerall-frontend'),('worker','powerall-api'),('migrate','powerall-api'),('database','powerall-database')):
            service=config['services'][name];service.pop('build',None);service['image']=image
            if name!='database': service['environment']['POWERALL_PUBLIC_ORIGIN']=origin
        config['services']['maintenance']['environment']['POWERALL_PUBLIC_ORIGIN']=origin
        config['services']['worker']['environment']['POWERALL_BRIGHT_ENABLED']='false'
        config['services']['api']['volumes']=[{'type':'bind','source':str(ROOT/'tests'),'target':'/tests','read_only':True}]
        config['services']['maintenance']['volumes']=[{'type':'bind','source':str(folder),'target':'/fixtures','read_only':True}]
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
            .sign(key,hashes.SHA256()))
        (folder/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (folder/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        gateway=config['services']['gateway']
        if mode:
            config['networks']['edge']={}
            config['networks']['npm']={'internal':True,'ipam':{'config':[{'subnet':'192.0.2.0/29','ip_range':'192.0.2.4/30'}]}}
            gateway['environment']['POWERALL_DOMAIN']='localhost'
            assert not gateway.get('ports')
            assert 'npm' not in config['services']['frontend']['networks']
            assert 'npm' not in config['services']['api']['networks']
            nginx="""pid /tmp/nginx.pid;
                error_log /dev/stderr warn;
                events { worker_connections 256; }
                http {
                    access_log off;
                    client_max_body_size 16k;
                    server {
                        listen 8443 ssl;
                        server_name localhost;
                        ssl_certificate /fixtures/cert.pem;
                        ssl_certificate_key /fixtures/key.pem;
                        add_header X-Test-Remote-Addr $remote_addr always;
                        location / {
                            proxy_set_header Host $host;
                            proxy_set_header X-Forwarded-Proto https;
                            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                            proxy_set_header X-Real-IP $remote_addr;
                            proxy_pass http://powerall-gateway:8080;
                        }
                    }
                }
            """
            (folder/'nginx.conf').write_text(nginx)
            for fixture in ('nginx.conf','cert.pem','key.pem'): (folder/fixture).chmod(0o444)
            config['services']['test-proxy']={
                'image':'nginx:stable-alpine','user':'101:101','entrypoint':['nginx'],
                'command':['-g','daemon off;','-c','/fixtures/nginx.conf'],
                'read_only':True,'tmpfs':['/tmp:mode=1777','/var/cache/nginx:mode=1777'],
                'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
                'ports':[{'target':8443,'published':str(port),'host_ip':'127.0.0.1','protocol':'tcp'}],
                'networks':{'edge':{},'npm':{'ipv4_address':'192.0.2.2'}},
                'volumes':[{'type':'bind','source':str(folder),'target':'/fixtures','read_only':True}],
                'depends_on':{'gateway':{'condition':'service_started'}}}
            config['services']['test-probe']={
                'image':'powerall-api','profiles':['tools'],'networks':['npm'],
                'read_only':True,'tmpfs':['/tmp'],'cap_drop':['ALL']}
        else:
            caddy=Path(ROOT/'deploy/Caddyfile').read_text().replace('email {$POWERALL_ACME_EMAIL}','auto_https off').replace('{$POWERALL_DOMAIN} {','https://localhost:8443 {\n    tls /fixtures/cert.pem /fixtures/key.pem')
            (folder/'Caddyfile').write_text(caddy)
            gateway['ports']=[{'target':8443,'published':str(port),'host_ip':'127.0.0.1','protocol':'tcp'}]
            for volume in gateway['volumes']:
                if volume['type']=='bind' and volume['target']=='/etc/caddy/Caddyfile':volume['source']=str(folder/'Caddyfile')
            gateway['volumes'].append({'type':'bind','source':str(folder),'target':'/fixtures','read_only':True})
        spec=folder/'compose.json';spec.write_text(json.dumps(config))
        compose=['docker','compose','-f',str(spec)]
        try:
            run([*compose,'up','-d','--scale','api=2','--scale','worker=2'])
            result=run([*compose,'exec','-T','-e','POWERALL_TEST_POSTGRES=1','api','python','-m','unittest','discover','-s','/tests','-p','test_postgres.py','-v'])
            print('PostgreSQL integration passed: RLS, shared sessions, concurrent workers, crash replay, retries, deletion and SQLite migration.',flush=True)
            context=ssl.create_default_context(cafile=str(folder/'cert.pem'))
            def client():return build_opener(HTTPSHandler(context=context),HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def call(opener,path,body=None,csrf=None,request_origin=origin,extra_headers=None):
                headers={'Origin':request_origin,'Content-Type':'application/json'}
                if csrf:headers['X-Powerall-Token']=csrf
                if extra_headers:headers.update(extra_headers)
                request=Request(origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
                try:
                    with opener.open(request,timeout=10) as result:
                        raw=result.read();return result.status,json.loads(raw) if 'json' in result.headers.get('Content-Type','') else raw,result.headers
                except HTTPError as error:
                    try:
                        raw=error.read();return error.code,json.loads(raw) if 'json' in error.headers.get('Content-Type','') else raw,error.headers
                    finally:error.close()
            anonymous=client()
            for _ in range(30):
                try:
                    if call(anonymous,'/login')[0]==200:break
                except Exception: pass
                time.sleep(1)
            operations=json.loads(run([*compose,'exec','-T','api','python','-m','powerall.manage','status']))
            assert operations['workers_alive']>=2
            assert call(anonymous,'/api/status')[0]==401
            _,_,headers=call(anonymous,'/login')
            for header in ('Strict-Transport-Security','Content-Security-Policy','X-Frame-Options','Referrer-Policy'):
                assert headers.get(header),header
            if mode:
                probe="from urllib.request import Request,urlopen; from urllib.error import HTTPError; r=Request('http://powerall-gateway:8080/api/config',headers={'Host':'localhost','X-Forwarded-For':'192.0.2.2'});\ntry: urlopen(r); raise AssertionError('Untrusted peer accepted')\nexcept HTTPError as e: assert e.code==403"
                run([*compose,'--profile','tools','run','--rm','-T','test-probe','python','-c',probe])
                # Browser-supplied IPs must never become trusted auth-throttle identities.
                code,_,headers=call(anonymous,'/api/auth/login',{'username':'fixture-spoof-check','password':'fixture-passphrase'},extra_headers={
                    'X-Forwarded-For':'203.0.113.99','X-Powerall-Client-IP':'203.0.113.98','X-Real-IP':'203.0.113.97','X-Forwarded-Host':'evil.example','X-Forwarded-Proto':'http'})
                assert code==401
                peer=headers['X-Test-Remote-Addr']
                check="from powerall.manage import accounts; from powerall.auth import digest; a=accounts(); db=a.db(); c=db.__enter__(); assert c.execute('SELECT COUNT(*) FROM attempts WHERE key=?',(digest('ip:'+"+repr(peer)+"),)).fetchone()[0]==1; assert c.execute('SELECT COUNT(*) FROM attempts WHERE key=?',(digest('ip:203.0.113.98'),)).fetchone()[0]==0; assert c.execute('SELECT COUNT(*) FROM attempts WHERE key=?',(digest('ip:203.0.113.99'),)).fetchone()[0]==0; db.__exit__(None,None,None)"
                run([*compose,'exec','-T','api','python','-c',check])
                container=run([*compose,'ps','-q','gateway']).decode().strip()
                assert not any(json.loads(run(['docker','inspect',container]))[0]['NetworkSettings']['Ports'].values())
                assert call(anonymous,'/api/config',request_origin='https://evil.example')[0]==403
                assert call(anonymous,'/api/auth/login',{'username':'fixture-body-bound','password':'x'*20000})[0]==413
                _,_,page_headers=call(anonymous,'/login')
                assert page_headers.get('Cache-Control')=='no-store'
                print('NPM-compatible nginx chain passed: private HTTP gateway, trusted client IP, spoof rejection, request bounds and preserved security headers.',flush=True)
            people=[]
            for username in ('alice','bob'):
                invitation=run([*compose,'exec','-T','api','python','-m','powerall.manage','invite']).decode().strip()
                invitation=parse_qs(urlsplit(invitation).fragment)['invite'][0]
                opener=client()
                code,_,headers=call(opener,'/api/auth/register',{'username':username,'password':'fixture-docker-passphrase','invite':invitation})
                assert code==200,code
                assert 'Secure' in headers.get('Set-Cookie','') and 'HttpOnly' in headers.get('Set-Cookie','')
                code,status,_=call(opener,'/api/status');assert code==200 and status['username']==username
                people.append((opener,status['csrf']))
            alice,alice_csrf=people[0];bob,bob_csrf=people[1]
            seed="from powerall.manage import accounts; from powerall.postgres import PostgresStore; a=accounts(); db=a.db(); c=db.__enter__(); u=c.execute(\"SELECT id FROM users WHERE username='alice'\").fetchone()[0]; db.__exit__(None,None,None); s=PostgresStore(a.database,u); s.execute(\"INSERT INTO meters(id,provider,provider_entity,resource_id,name) VALUES('fixture-meter','bright','e','r','Fixture home')\")"
            run([*compose,'exec','-T','api','python','-c',seed])
            queued="from powerall.manage import accounts; from powerall.jobs import QueuedSync; a=accounts(); db=a.db(); c=db.__enter__(); u=c.execute(\"SELECT id FROM users WHERE username='alice'\").fetchone()[0]; db.__exit__(None,None,None); assert QueuedSync(a.database,u).trigger(1)"
            run([*compose,'exec','-T','api','python','-c',queued])
            assert call(alice,'/api/status')[1]['sync']['queue']['state']=='queued'
            payload={'meter':'fixture-meter','label':'Fixture tariff','from':'2026-01-01','rate':'30','standing':'50','windows':[]}
            code,saved,_=call(alice,'/api/tariffs',payload,alice_csrf);assert code==201,code
            assert call(alice,'/api/dashboard?meter=fixture-meter&view=day&date=2026-01-02')[0]==200
            assert call(bob,'/api/dashboard?meter=fixture-meter&view=day&date=2026-01-02')[0]==404
            assert call(bob,'/api/tariffs/delete',{'id':saved['id']},bob_csrf)[0]==404
            assert call(alice,'/api/tariffs',payload,None)[0]==403
            assert call(alice,'/api/tariffs',payload,alice_csrf,'https://evil.example')[0]==403
            code,export,_=call(alice,'/api/account/export');assert code==200 and len(export['tariffs'])==1
            assert call(bob,'/api/account/export')[1]['meters']==[]
            assert call(anonymous,'/.env')[0]==404
            # Only gateway publishes a port; API/frontend run non-root and read-only.
            for service in ('api','frontend'):
                identity=run([*compose,'exec','-T',service,'id','-u']).decode().strip();assert identity=='10001'
                container_ids=run([*compose,'ps','-q',service]).decode().split()
                for details in json.loads(run(['docker','inspect',*container_ids])):
                    assert details['HostConfig']['ReadonlyRootfs']
                    assert details['Config']['User']=='10001:10001'
                    assert not any(details['NetworkSettings']['Ports'].values())
            encrypted=run([*compose,'--profile','tools','run','--rm','-T','maintenance'])
            assert encrypted.startswith(b'POWERALL-BACKUP-1\n') and b'fixture-docker-passphrase' not in encrypted
            (folder/'backup.enc').write_bytes(encrypted)
            run([sys.executable,str(ROOT/'scripts/decrypt-backup.py'),str(folder/'backup.enc'),str(folder/'backup_key'),str(folder/'backup.tar.gz')])
            assert (folder/'backup.tar.gz').read_bytes().startswith(b'PGDMP')
            # Deletion after a backup must survive restoring that older backup.
            assert call(alice,'/api/account/delete',{'password':'fixture-docker-passphrase'},alice_csrf)[0]==200
            ledger=run([*compose,'--profile','tools','run','--rm','-T','maintenance','python','-m','powerall.manage','backup-erasures'])
            (folder/'erasures.enc').write_bytes(ledger)
            run([sys.executable,str(ROOT/'scripts/decrypt-backup.py'),str(folder/'erasures.enc'),str(folder/'backup_key'),str(folder/'erasures.dump')])
            assert (folder/'erasures.dump').read_bytes().startswith(b'PGDMP')
            run([*compose,'stop','api','worker'])
            run([*compose,'--profile','tools','run','--rm','-T','maintenance','sh','-c',
                'PGPASSWORD="$(cat /run/secrets/database_admin_password)" pg_restore -h database -U postgres -d powerall --clean --if-exists --exit-on-error /fixtures/backup.tar.gz'])
            run([*compose,'--profile','tools','run','--rm','-T','maintenance','python','-m','powerall.manage','apply-erasures'])
            run([*compose,'--profile','tools','run','--rm','-T','maintenance','python','-m','powerall.manage','secure-restored-accounts'])
            check="from powerall.manage import accounts; a=accounts(); db=a.db(); c=db.__enter__(); assert c.execute(\"SELECT id FROM users WHERE username='alice'\").fetchone() is None; assert c.execute(\"SELECT id FROM users WHERE username='bob'\").fetchone() is not None; assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0; db.__exit__(None,None,None)"
            run([*compose,'--profile','tools','run','--rm','-T','maintenance','python','-c',check])
            print('Docker HTTPS integration passed: registration, secure sessions, tenant isolation, origin/CSRF checks, security headers, private ports, non-root services, and encrypted online backup/restore with erasure protection.')
        except Exception:
            if mode:
                diagnostics=run([*compose,'logs','--tail','20','test-proxy','gateway']).decode()
                sys.stderr.write(diagnostics[-6000:])
            raise
        finally:
            run([*compose,'--profile','tools','down','--volumes','--remove-orphans'])

if __name__=='__main__':main()
