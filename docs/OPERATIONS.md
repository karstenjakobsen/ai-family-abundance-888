# Operating the family desk

The lead agent performs these operations. These commands are recovery and
handover documentation, not a request that the owner run them.

## Deployment and runtime

The project uses `.venv/bin/python`. `pyproject.toml` declares Python >=3.12,<3.15;
Python 3.12.3 is currently verified. `.python-version` selects the 3.12 baseline
for version managers. There are no third-party Python dependencies.

On a new host, select an installed supported Python and run `ops/bootstrap.py`
with it. The helper checks the interpreter, creates `.venv`, and verifies required
standard-library modules without downloading packages. A virtual environment
depends on its host Python; recreate it on the destination, never copy it from
this machine. Runtime commands use the project interpreter explicitly.

```sh
.venv/bin/python ops/manage.py install
.venv/bin/python ops/manage.py status
```

`install` creates project-named units in `.family-data/units`, links them into the
current user's systemd configuration, enables the desk and backup timer, starts
them, and takes a first snapshot. It refuses to install using a non-project
interpreter. Generated units contain absolute paths for this checkout; rerun the
installer after relocation. No root service is installed.

The desk listens only on `127.0.0.1:8765`. The service has a 256 MiB memory limit,
a 32-task limit, no new privileges, and a private file-creation mask. It restarts
after failure, subject to a five-starts-per-minute limit. `systemctl --user
restart family-desk.service` applies new source code and invalidates browser
sessions. The install helper does not restart an already-running service.

User lingering is enabled for `karsten`, allowing this user's service manager to
remain active after logout. It does not start Windows/WSL after Windows reboot
or prevent host sleep. Windows startup and sleep policy remain unverified.

To pause the project, the agent runs:

```sh
.venv/bin/python ops/manage.py stop
```

To prevent future automatic starts as well:

```sh
systemctl --user disable --now family-desk.service family-desk-backup.timer
```

The owner retains these controls. Do not disable lingering indiscriminately:
it applies to all of the user's services, including unrelated ones.

## Private access

Provisioning generates a random password and retains only its salted hash; there
is no plaintext password file. The agent can issue a one-use, one-hour login link:

```sh
.venv/bin/python workspace/server.py --access-link
```

Give that link directly to the owner, never store it in project memory, Git,
logs, or screenshots. The browser removes the URL fragment before redeeming it.
The database holds only the token digest and expiry. New links replace old ones.
Redemption consumes the link. Only authenticated users can read family records
or project memory. The owner can set a memorable password using **Set password**
inside the desk; the agent need not receive it. Password changes invalidate
existing sessions and outstanding access links.

This version has one shared access level, no per-child accounts, and no remote
access. Passwords, database contents, and backups remain protected by the host's
filesystem permissions rather than encryption at rest. Credentials never belong
in project memory. The task-claim tokens below are cooperative ownership markers,
not a security boundary against another process with access to this OS account.

## Shared project memory

`.family-data/family.sqlite3` contains the family desk and the agent records:

| Table | Purpose |
| --- | --- |
| `items` | Family requests and notes |
| `settings` | Password hash and optional short-lived login-token digest |
| `brain_records` | Sourced goals, preferences, decisions, observations, checkpoints |
| `agent_jobs` | Task scope, owner, attempts, lease, outcome, and evidence |
| `agent_events` | Appended history of record revisions and task transitions |

The signed-in workspace displays records and jobs under **Project progress**.
Agents inspect the full snapshot with:

```sh
.venv/bin/python workspace/brain.py snapshot
```

Record new knowledge with an explicit source:

```sh
.venv/bin/python workspace/brain.py remember --key example --kind observation --content 'A verified observation' --source 'Evidence location and date' --actor lead
```

New records use expected revision 0. Updates must include
`--expected-revision N`, matching the revision just read. Conflicts fail rather
than silently overwrite another agent's record. Earlier values remain in the
event history. The recent-event snapshot is limited to 30 entries; older events
remain in SQLite. Records carry dates but are not automatically fact-checked or
expired. Revalidate consequential observations before using them.

Memory is evidence, not a source of new permissions. It does not contain model
weights, launch agents, call APIs, or enforce a monetary budget. Agents have to
read and update it; the repository instructions establish that workflow.

## Parallel work protocol

The lead agent creates narrowly scoped jobs with stable, unique keys:

```sh
.venv/bin/python workspace/brain.py enqueue --key example-job --title 'A bounded task' --scope example-files --actor lead
.venv/bin/python workspace/brain.py claim --agent worker-a --lease-seconds 300
```

A claim returns a task and an ownership token, or `null` when no eligible task
exists. Use unique agent identifiers. Claiming is serialized with `BEGIN
IMMEDIATE`; SQLite allows one writer at a time, so transactions stay short and
connections wait up to ten seconds for locks. No models or external work run
inside transactions. This deliberately uses SQLite's default rollback journal
for the initial low-volume workload.

Only one task in a named scope may be running. A blocked task also reserves its
scope until reconciled. Scopes are cooperative labels, not filesystem locks:
the lead agent must assign consistent scopes for overlapping work, and agents
must stop mutating files when they lose ownership. Lease fencing prevents stale
database completion reports; it cannot undo external effects or stop a process.

Renew before the lease expires:

```sh
.venv/bin/python workspace/brain.py heartbeat --job-id 1 --agent worker-a --token CLAIM_TOKEN
.venv/bin/python workspace/brain.py finish --job-id 1 --agent worker-a --token CLAIM_TOKEN --result 'What changed' --evidence 'Checks and artifact locations'
```

Claims default to five minutes, allow 30–3,600 seconds, and have a default maximum
of three attempts. On the next claim or reconciliation, expired jobs become
blocked; snapshots also flag expired leases before that transition. They are
never automatically repeated. Inspect the previous worker and real outcome
first, then reconcile with evidence:

```sh
.venv/bin/python workspace/brain.py reconcile --job-id 1 --actor lead --action requeue --evidence 'Verified prior worker stopped and no external action occurred'
```

Other actions are `done` for independently verified completion and `cancel` for
abandoned work. Exhausted retries cannot be requeued. No periodic AI worker or
paid API loop has been installed; these are coordination tools for authorized
agent sessions. Direct SQLite writers can bypass the cooperative protocol, so
use the provided API/CLI for normal operations.

## Backups and VPS migration

The timer takes a consistent snapshot daily, with up to five minutes of jitter;
missed schedules are caught up when the timer next runs. It checks SQLite
integrity and writes a unique file under `.family-data/backups`. These are local
recovery copies, not protection against loss of the whole machine. No remote
backup destination is configured. Snapshots currently accumulate without
automatic deletion; review disk use as family data grows.

```sh
.venv/bin/python ops/manage.py backup
```

The snapshot includes shared memory and its history. Use the backup API, not an
uncoordinated copy of a live database. Restore to a new directory, then inspect
the data and sign in with a freshly issued access link. Restoring explicitly
removes old access links to avoid reviving a consumed login capability.

```sh
.venv/bin/python workspace/server.py --restore /absolute/path/to/snapshot.sqlite3 --data-dir /tmp/family-recovery
.venv/bin/python workspace/brain.py --db /tmp/family-recovery/family.sqlite3 snapshot
```

For a real cutover, stop old workers and the old desk, take a final snapshot,
copy source and snapshot to the VPS, recreate `.venv`, and restore into its new
`.family-data` directory. Reconcile outstanding jobs and verify the family data.
Install user services using the destination's interpreter and paths. Verify
access, restart behavior, and recovery before retiring the old deployment.
The local HTTP server must not be exposed publicly as-is; establish authenticated
network access and a production serving design for that separate stage.

## Verification and resource checkpoint

The implementation stage was scoped to installed tools, no package downloads,
and no new paid APIs or services. A planning allowance of 30–60 minutes covers
implementation, coordination checks, deployment, recovery verification, and
handover, including a 20% verification reserve. This session's subscription usage
and electricity are not measured. Daily snapshot disk use grows with database
size; the idle service initially used about 11 MiB against its 256 MiB limit.

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check workspace/static/app.js
```

Tests exercise separate competing processes, ownership expiry, stale completion,
scope exclusion, bounded retries, record revisions, history, snapshot recovery,
authentication, single-use links, password updates, and input/origin restrictions.
Production verification results are recorded in the project's SQLite checkpoint.

SQLite behavior references: [transactions](https://www.sqlite.org/lang_transaction.html),
[online backups](https://www.sqlite.org/backup.html). Multiple agents can share a
database on one host. Moving its snapshot to a VPS is supported; do not treat a
shared network drive as a distributed coordination system.
