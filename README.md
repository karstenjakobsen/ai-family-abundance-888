# GRIME Family AI Partnership

Our purpose and engineering principles are in [MANIFEST.md](MANIFEST.md).

We are starting on the family's home machine with a private family desk for
requests, task progress, and shared notes. Cloud hosting follows a demonstrated
need and an approved operating budget.

## Use the family desk

From this repository in an interactive Ubuntu/WSL terminal:

```sh
python3 workspace/server.py
```

On first launch, choose and repeat a password of at least 12 characters. The
terminal hides your input. Open **http://127.0.0.1:8765** in a browser on the same
machine and sign in. Press **Ctrl+C** in the terminal to stop the server.

Create a request, add a name and details, and use **Edit** to move it from Inbox
to Up next, In progress, or Done. Keep shared ideas in notes. **Lock workspace**
signs out that browser. This version uses one shared password, with equal access
for everyone who has it; individual accounts and child-specific access are not
implemented. Requests are records for people to review, not automatic AI jobs.

The app requires Python 3.12 or later and no third-party packages. It calls no
external services and requires no API key. Data and the salted password hash
live in `.family-data/family.sqlite3`, excluded from Git. Data is not encrypted
at rest; protect the host account and backup files. A local machine administrator
can access the database. Browser sessions expire after 12 hours or server restart.

The server binds only to `127.0.0.1`. Phone/LAN access, public hosting, HTTPS,
automatic startup, and background AI work are not configured. Do not put this
local HTTP server behind a public tunnel; a network deployment needs a separate
production serving and access design. Windows-to-WSL browser access has not been
verified from this session.

To change a forgotten password, stop the server, run the following, then restart:

```sh
python3 workspace/server.py --set-password
```

## Backup and restore

The backup command takes a consistent SQLite snapshot even while the desk is
running. Use a new filename each time; existing files are never overwritten.

```sh
mkdir -p backups
python3 workspace/server.py --backup backups/family-2026-11-01.sqlite3
```

Copy the backup to a private destination outside this machine. It contains family
records and the password hash. An off-machine destination and automatic backups
have not yet been configured.

Restore into a new directory, then open the recovered workspace on another port:

```sh
python3 workspace/server.py --restore backups/family-2026-11-01.sqlite3 --data-dir .family-data/recovery
python3 workspace/server.py --data-dir .family-data/recovery --port 8766
```

Visit `http://127.0.0.1:8766`, sign in with the password from the backup, and verify
your records. The original data remains untouched. Stop the recovery server with
Ctrl+C. To use recovered data going forward, keep specifying that `--data-dir`.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check workspace/static/app.js
```

Four integration tests passed on 2026-11-01, covering authentication, logout,
session expiry, login throttling, Host/Origin restrictions, invalid input,
record creation and updates, and backup recovery into a fresh server. Tests use
temporary data and localhost sockets; they do not contact external services.
JavaScript syntax validation passed. Interactive browser rendering and the
Windows browser connection have not been tested. Node is needed only for the
optional JavaScript syntax check, not to run the desk.

## Current checkpoint — 2026-11-01

The repository began with only `AGENTS.md` and `MANIFEST.md`. The first local
workspace implementation and recovery tools are now present. No persistent
server, background worker, cloud deployment, or scheduled backup has been
installed. No real family records or password have been created during testing.

Read-only checks from the current execution environment reported:

| Item | Observation |
| --- | --- |
| Platform | Ubuntu 24.04.3 LTS on a WSL2 kernel |
| CPU | 24 logical CPUs visible |
| Memory | Approximately 45 GiB visible; 42 GiB available during inspection |
| Project filesystem | Approximately 929 GiB available on the Linux filesystem |
| Development tools | Python 3.12.3, Git 2.43.0, Node 24.18.0 |
| Containers | Docker CLI 29.3.1 installed; engine access denied from this session |
| Services | Service-manager inspection denied from this session |

These observations describe the accessible environment, not a complete host
audit. WSL disk availability does not establish free space on the Windows
physical disk. Access denials do not establish that Docker or host services are
broken. Host uptime, Windows sleep/restart behavior, backups, and external
reachability remain unverified.

## First decisions

The owner has authorized taking initiative on this project using the home machine
and intends to move to a cloud VPS when practical.

Until a spending limit is specified, the planning allowance for new purchases,
subscriptions, and paid API jobs is zero. Existing session costs and electricity
are not measured here; this is not a claim that operation is free.

The owner chose a private family workspace and reported having a $100 OpenAI
account. Whether this means a subscription or prepaid API credit is unresolved;
it is not recorded as a verified API balance or a new monthly spending allowance.
Official [OpenAI pricing documentation](https://learn.chatgpt.com/docs/pricing)
distinguishes subscription usage from API pricing. No account billing was read.
Whether Windows normally stays on is also unresolved.

## Delivery plan

1. **Define one job.** Write down its user, input, expected output, and success
   measure. For family admin, measure time saved. For an income experiment,
   first establish demand and a deliverable that can be produced reliably.
2. **Deliver it locally.** Use the existing tools and the fewest dependencies
   that solve the job. Start with manual invocation and review. Document the run
   command and verify the result on representative input.
3. **Make it repeatable.** Add persistent execution only when the job benefits
   from it. Establish host service access, Windows/WSL startup behavior, a stop
   procedure, bounded retries, logs, and enforceable spending limits before
   unattended paid work.
4. **Prove recovery.** Identify application data, choose a backup destination
   outside this machine, and restore into a temporary location. Record the
   outcome. A Git repository alone is not a backup of application data.
5. **Move when justified.** Migrate when uptime, customer access, or local
   resource constraints justify the recurring expense. Compare providers and
   current prices at that point, within the approved budget.

The initial discovery estimate was 15–30 minutes using installed tools, including
verification and handover, with no new paid services. The delivered workspace
uses local CPU, memory, and disk with no paid runtime dependencies. Electricity,
this coding session's usage, and future maintenance time are not measured here.
Before the next implementation stage, estimate build, test, deployment, retries,
handover, ongoing costs, and uncertainty; reserve at least 20% of its approved
work budget for verification and a recoverable stop. No monetary or token
enforcement mechanism has been implemented yet.

## Operating approach

Keep routine project edits and local checks moving without repeated approval.
Make spending, external commitments, and access to additional systems explicit
before acting. Do not infer authority to contact customers or use family records
from the broad project mandate.

Keep credentials outside version control and request only the access needed for
the chosen job. Each deployed job needs a documented start command, stop command,
data location, backup/restore procedure, and a record of its costs and results.

This repository currently provides a checkpoint for future work; it does not
provide an always-running agent. Unattended execution will need a separately
configured runtime and a verified lifecycle.

## Cloud portability

For the first application, keep configuration outside source code, separate
persistent data from application files, pin dependencies, and document how to run
and restore it on a clean Linux host. Introduce containers if they solve an
actual deployment need; their presence on this machine is not a reason to depend
on them now.

A migration is complete only after the chosen job works on the new host, its
data has been restored and checked, ongoing costs fit the budget, and the owner
has access and a rollback path. Retain the working home deployment until those
checks pass.

## Resume here

Read `MANIFEST.md` and this checkpoint. First have the owner start the desk and
choose a private password locally, then record one real family request. Clarify
which family devices need access, Windows uptime, and the OpenAI account type.
The next infrastructure milestone is secure access from those devices plus an
off-machine recovery copy; choose the mechanism after those requirements are
known. Do not interpret current Docker/service inspection denials as permission
to alter host access.
