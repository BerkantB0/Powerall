"""Consistent online PostgreSQL dumps streamed through authenticated encryption."""
import os
import secrets
import subprocess
import sys
from .hosted import read_secret
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def backup(ledger_only=False):
    key=bytes.fromhex(read_secret('POWERALL_BACKUP_KEY',64).decode())
    nonce=secrets.token_bytes(12)
    encryptor=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor()
    env=dict(os.environ,PGPASSWORD=read_secret('POWERALL_DATABASE_ADMIN_PASSWORD').decode(),PGSSLMODE=os.environ.get('POWERALL_DATABASE_SSLMODE','prefer'))
    if os.environ.get('POWERALL_DATABASE_SSLROOTCERT'):env['PGSSLROOTCERT']=os.environ['POWERALL_DATABASE_SSLROOTCERT']
    command=['pg_dump','-h',os.environ.get('POWERALL_DATABASE_HOST','database'),'-U','postgres',
        '-d',os.environ.get('POWERALL_DATABASE_NAME','powerall'),'--format=custom','--no-acl']
    command += ['--table=erasures','--data-only'] if ledger_only else ['--exclude-table=erasures']
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
    output=sys.stdout.buffer
    try:
        output.write(b'POWERALL-BACKUP-1\n'+nonce)
        while chunk:=process.stdout.read(65536): output.write(encryptor.update(chunk))
        if process.wait()!=0: raise RuntimeError('PostgreSQL backup failed; discard this output.')
        output.write(encryptor.finalize());output.write(encryptor.tag);output.flush()
    finally:
        process.stdout.close()
        if process.poll() is None: process.kill();process.wait()
