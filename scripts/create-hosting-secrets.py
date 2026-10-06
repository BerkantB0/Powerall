#!/usr/bin/env python3
"""Generate deployment secrets without printing or overwriting them."""
from pathlib import Path
import os
import secrets
root = Path(__file__).resolve().parent.parent / 'deploy' / 'secrets'
root.mkdir(parents=True, exist_ok=True)
os.chmod(root, 0o700)
for name in ('encryption_key', 'internal_token', 'backup_key', 'database_password', 'database_admin_password'):
    path = root / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f'{name}: already exists; preserved.')
        continue
    # File-backed Compose secrets preserve host ownership/mode. Containers run UID 10001.
    with os.fdopen(fd, 'w') as file: file.write(secrets.token_hex(32) + '\n')
    print(f'{name}: created. On Linux, assign ownership to UID 10001 before deploying.')
