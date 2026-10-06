#!/usr/bin/env python3
"""Authenticate and decrypt a backup locally. Restore instructions are in HOSTING.md."""
import argparse
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
parser = argparse.ArgumentParser()
parser.add_argument('backup'); parser.add_argument('key_file'); parser.add_argument('output_archive')
args = parser.parse_args()
key = bytes.fromhex(Path(args.key_file).read_text().strip())
output = Path(args.output_archive)
fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with open(args.backup, 'rb') as source, os.fdopen(fd, 'wb') as target:
        if source.read(18) != b'POWERALL-BACKUP-1\n': raise ValueError('Not a Powerall backup.')
        nonce = source.read(12)
        source.seek(-16, os.SEEK_END); tag = source.read(16)
        encrypted_end = source.tell() - 16
        source.seek(30)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        while source.tell() < encrypted_end:
            block = source.read(min(1024 * 1024, encrypted_end - source.tell()))
            target.write(decryptor.update(block))
        target.write(decryptor.finalize())
except BaseException:
    output.unlink(missing_ok=True)
    raise
print('Backup authenticated and decrypted. Protect and remove the plaintext archive after restoration.')
