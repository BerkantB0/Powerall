#!/usr/bin/env python3
"""Persistent, loopback-only hosted preview; separate keys/accounts/data from deployment."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
from datetime import datetime,timedelta,timezone

ROOT=Path(__file__).resolve().parent.parent
STATE=ROOT/'data/hosted-local'
COMPOSE=['docker','compose','--env-file','deploy/hosting.env.example','-f','compose.yaml','-f','compose.local.yaml']

def private_file(path,value,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'wb') as file:file.write(value)

def prepare():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    for folder in (STATE,STATE/'secrets',STATE/'tls'):
        folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
    for name in ('encryption_key','internal_token','backup_key','database_password','database_admin_password'):
        path=STATE/'secrets'/name
        if not path.exists():
            # The 0700 parent protects files on the host. Compose mounts each file
            # separately; readable mounts support non-root containers on Docker Desktop.
            # Public deployment uses its own 0600 secrets and UID/ACL setup.
            private_file(path,(secrets.token_hex(32)+'\n').encode(),0o444)
    cert_path=STATE/'tls/cert.pem';key_path=STATE/'tls/key.pem'
    if cert_path.exists() and key_path.exists():return
    if cert_path.exists() or key_path.exists():raise ValueError('Incomplete local TLS pair; preserve keys and repair manually.')
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=5))
        .not_valid_after(datetime.now(timezone.utc)+timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
        .sign(key,hashes.SHA256()))
    private_file(key_path,key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    private_file(cert_path,cert.public_bytes(serialization.Encoding.PEM))

def call(*args,capture=False):
    return subprocess.run([*COMPOSE,*args],cwd=ROOT,check=True,text=True,stdout=subprocess.PIPE if capture else None).stdout

def invitation():
    link=call('exec','-T','api','python','-m','powerall.manage','invite',capture=True).strip()
    path=STATE/'invitation.txt'
    if path.exists():path.unlink()
    private_file(path,(link+'\n').encode())
    print(link)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['start','stop','status','invite'],nargs='?',default='start')
    args=parser.parse_args()
    if args.command=='start':
        prepare();call('up','-d','--build');print('Hosted Powerall: https://localhost:8443/login',flush=True)
        invitation()
    elif args.command=='stop':call('stop')
    elif args.command=='status':call('ps')
    else:invitation()

if __name__=='__main__':main()
