"""Install and inspect this project's user services without root privileges."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workspace.server import backup, initialize, set_password  # noqa: E402
import secrets

DATA = ROOT / '.family-data'


def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def snapshot():
    database = DATA / 'family.sqlite3'
    if not database.is_file():
        raise ValueError('Workspace database does not exist.')
    directory = DATA / 'backups'
    directory.mkdir(mode=0o700, exist_ok=True)
    destination = directory / (datetime.now(timezone.utc).strftime('family-%Y%m%dT%H%M%S.%fZ') + '.sqlite3')
    backup(database, destination)
    db = sqlite3.connect(destination)
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Snapshot integrity check failed.')
    finally:
        db.close()
    print(json.dumps({'backup': str(destination), 'integrity': 'ok'}))


def systemctl(*args):
    subprocess.run(['systemctl', '--user', *args], check=True)


def install():
    expected_python = ROOT / '.venv' / 'bin' / 'python'
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        raise ValueError(f'Install with the project interpreter: {expected_python} ops/manage.py install')
    database = initialize(DATA)
    db = sqlite3.connect(database)
    try:
        configured = db.execute("SELECT 1 FROM settings WHERE key='password'").fetchone()
    finally:
        db.close()
    if not configured:
        # No plaintext password is persisted. Access is provisioned separately via one-use links.
        set_password(database, secrets.token_urlsafe(48))
    unit_dir = DATA / 'units'
    unit_dir.mkdir(mode=0o700, exist_ok=True)
    common = '''UMask=0077
NoNewPrivileges=true
MemoryMax=256M
TasksMax=32
'''
    units = {
        'family-desk.service': f'''[Unit]
Description=GRIME private family desk
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart={quote(expected_python)} {quote(ROOT / 'workspace' / 'server.py')}
Restart=on-failure
RestartSec=3
{common}
[Install]
WantedBy=default.target
''',
        'family-desk-backup.service': f'''[Unit]
Description=Snapshot GRIME family desk and shared project memory

[Service]
Type=oneshot
ExecStart={quote(expected_python)} {quote(Path(__file__).resolve())} backup
{common}''',
        'family-desk-backup.timer': '''[Unit]
Description=Daily GRIME workspace recovery snapshot

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=300
Unit=family-desk-backup.service

[Install]
WantedBy=timers.target
''',
    }
    for name, content in units.items():
        path = unit_dir / name
        path.write_text(content)
        path.chmod(0o600)
        systemctl('link', str(path))
    systemctl('daemon-reload')
    systemctl('enable', '--now', 'family-desk.service', 'family-desk-backup.timer')
    systemctl('start', 'family-desk-backup.service')
    systemctl('is-active', 'family-desk.service', 'family-desk-backup.timer')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'backup', 'status', 'stop', 'start'))
    args = parser.parse_args()
    try:
        if args.action == 'install':
            install()
        elif args.action == 'backup':
            snapshot()
        elif args.action == 'status':
            systemctl('status', 'family-desk.service', 'family-desk-backup.timer', '--no-pager')
        elif args.action == 'stop':
            systemctl('stop', 'family-desk.service', 'family-desk-backup.timer')
        elif args.action == 'start':
            systemctl('start', 'family-desk.service', 'family-desk-backup.timer')
    except (ValueError, OSError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
