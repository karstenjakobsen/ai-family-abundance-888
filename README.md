# GRiMe Family AI Partnership

The [manifesto](MANIFEST.md) defines our purpose and the lead agent's operational
responsibility. The agent builds, starts, verifies, and maintains authorized
project services; the family sets direction and retains control.

## Family desk

The desk is installed and running as a user service on the home machine at
**http://127.0.0.1:8765**. Closing a terminal does not stop it. It provides private
requests, task progress, notes, and a view of shared project decisions and work.
It uses the project `.venv` and makes no model API calls.

The agent provides a one-use sign-in link, valid for one hour. Once inside, the
owner can use **Set password** for future sign-ins. There is no startup command
for the owner to run. This first version has one shared access level; family
phone access and individual accounts are future work.

## Shared memory

`.family-data/family.sqlite3` holds both family data and project memory:

- Goals, decisions, preferences, observations, and checkpoints with sources.
- Revision checks that prevent silent overwrites between agents.
- Atomic task claims, work scopes, renewable leases, and bounded attempts.
- Completion evidence and an appended history of changes.

Parallel processes were tested against the same database. Expired claims hold
work for reconciliation rather than silently retrying it. This is persistent
project state and cooperative coordination, not model weights or an unattended
AI worker. Agent sessions must read and update it.

The database is private to this OS account and excluded from Git. Secrets do not
belong in memory. The desk retains password hashes and short-lived login-token
digests separately from agent records.

## Current verification — 2026-11-01

- Python 3.12.3 in `.venv`, SQLite 3.45.1; no third-party Python dependencies.
- Managed desk and daily snapshot timer active; user lingering enabled.
- Live HTTP 200 from both WSL and Windows localhost.
- Unauthenticated project-data requests denied; one-use login verified.
- Service restart and session invalidation verified.
- Concurrent-agent and workspace integration tests run in `.venv`.
- SQLite snapshots include family data, shared memory, and task history.

The exact verification checkpoint is maintained in SQLite and shown in the desk.
Interactive visual browser QA is not available in this session.

## Operation and portability

[Operations and recovery](docs/OPERATIONS.md) contains the agent runbook: service
controls, access provisioning, task claims, environment recreation, backups,
restore verification, and future VPS migration.

The live service and backup timer explicitly use `.venv/bin/python`.
`pyproject.toml` declares the supported Python range; `.python-version` records
3.12 as the tested baseline. `ops/bootstrap.py` checks an installed interpreter
and recreates the environment on a new host without downloading packages.
Virtual environments are host-specific; migrate a tested database snapshot and
source, then rebuild `.venv` on the destination.

The daily snapshots currently remain on this machine. An off-machine backup
location, Windows reboot startup, sleep behavior, and secure access from other
family devices remain unresolved. The service is local-only. Windows sleep or
shutdown pauses it; a cloud move follows demonstrated need and an approved budget.

## Resources and next work

The environment reports Ubuntu 24.04 on WSL2, 24 logical CPUs, about 45 GiB RAM,
and ample Linux filesystem space. The idle desk initially used about 11 MiB of
its 256 MiB service limit. These are observations, not a complete host audit.

The owner reported a $100 OpenAI account. Its type and an explicit spending
allowance remain unconfirmed. No paid API loop, new subscription, or cloud
purchase is configured. The current session's account usage and electricity are
not measured here.

The next useful infrastructure steps are an off-machine recovery copy and secure
access from the family's chosen devices. Keep the current home deployment
working while developing those increments. Future agents should read
`AGENTS.md`, the manifesto, this README, and the shared-memory snapshot before
acting.
