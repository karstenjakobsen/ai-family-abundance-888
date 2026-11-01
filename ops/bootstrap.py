"""Recreate the project's isolated interpreter on a new host; no downloads."""
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent


def main():
    if not (3, 12) <= sys.version_info[:2] < (3, 15):
        raise SystemExit('Use an installed Python 3.12–3.14 interpreter; 3.12 is the verified baseline.')
    environment = ROOT / '.venv'
    if not environment.exists():
        venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / 'bin' / 'python'
    if not python.is_file():
        raise SystemExit('Existing .venv is incomplete. Inspect it before replacing it.')
    subprocess.run([str(python), '-c',
        'import hashlib, sqlite3, ssl, sys; '
        'assert (3,12) <= sys.version_info[:2] < (3,15); '
        'assert sys.prefix != sys.base_prefix; '
        'hashlib.scrypt(b"check", salt=b"check", n=16384, r=8, p=1); '
        'print("Project Python:", sys.executable); '
        'print("Version:", sys.version.split()[0]); '
        'print("SQLite:", sqlite3.sqlite_version)'], check=True)


if __name__ == '__main__':
    main()
