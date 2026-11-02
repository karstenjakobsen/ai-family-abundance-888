# Family workspace base

A self-hosted Linux toolbox for a family-operated agent: browser terminal, durable
SQLite memory, local MCP capabilities, approved background jobs, and a host guardian.

The repository contains the reusable tools and bootstrap interface. Family data,
agent histories, generated applications, credentials, and operational state live
outside Git. Generated features may change independently of the durable intent
that explains how they should behave.

## What is implemented

- A responsive browser workspace with a real xterm/PTY connection to Codex CLI.
  Browser reconnects preserve the existing process while the web service runs.
- Password-based household accounts, server-side sessions, administrator/member
  roles, same-origin request checks, WebSocket authentication, login throttling,
  and bounded HTTP/WebSocket inputs. No default account or password.
- Shared family requests, goals, events, finance intentions, and notes in SQLite.
  Finance records are intentions/notes, not a banking integration or accounting system.
- A transactional application journal and online SQLite backups with seven local
  daily snapshots. Root can change the journal; it is not tamper-evident evidence.
- A local stdio MCP server exposing memory, journal, jobs, budget, audit, backup,
  source containment, and limited service restart operations.
- Exact, immutable job proposals with adult approval, due times, one worker,
  dispatch reservations, and token usage reconciliation from Codex JSON events.
- A shared agent-slot lock: the interactive console and the background worker
  cannot independently operate the host at the same time. Use **Release console
  for jobs** to allow background dispatch. Agent-internal tool/subagent execution
  remains within that session's mandate.
- Recovery semantics: a worker restart, timeout, missing completion event, or
  possible partial execution becomes `outcome_unknown`. Further dispatch pauses
  until an administrator reconciles effects. It never automatically retries.
- A guardian which checks disk space, compares TCP listener observations against
  an explicitly approved baseline, and watches systemd SSH failure events.
- Optional 15-minute nftables source-IP containment after 10 SSH failures within
  five minutes. It uses its own table, leaves existing firewall policy intact,
  and refuses configured recovery addresses. Monitor errors are visible.
- Optional agent investigations under a human-configured standing mandate. They
  receive normalized incidents, use the shared funded job queue, and propose
  broader remediation for approval. Duplicate active investigations are suppressed.
- systemd process supervision and a Caddy HTTPS reverse-proxy configuration.

This is a runnable base, with two real Codex launch paths. A live Codex account,
provider billing policy, external integrations, and a VPS are supplied by the
operator. It does not simulate model responses or claim to detect every attack.

## Architecture

| Durable part | Responsibility |
| --- | --- |
| Toolbox repository | Capability contracts, integration code, browser bridge, operator instructions, and tests |
| `/var/lib/family-workspace/brain.sqlite3` | Family intent, decisions, progress, sessions, jobs, incidents, and dispatch accounting |
| `/var/lib/family-workspace/workspace` | Codex working directory and replaceable/generated family applications |
| Root's Codex configuration | Provider authentication, Codex permissions, local history, and MCP registration |
| Root-owned environment file | Workspace origin, state location, process commands, and containment mode |
| External recovery and billing controls | Recovery/evidence and spending boundaries beyond this root agent's authority |

SQLite is accessed through short local transactions and typed capabilities. The
model reasons about records; SQLite provides durable storage. A deterministic
guardian continues without model calls, including when the token budget is empty.

## Local development

Use Linux and Python 3.11 or later. The tested lockfile was produced on Linux with
Python 3.12. Browser assets are already vendored with their MIT licenses.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
export FAMILY_STATE="$PWD/state"
export FAMILY_ORIGIN=http://localhost:8765
export FAMILY_DEV_HTTP=1
.venv/bin/family-workspace init
.venv/bin/family-workspace add-user karstenjakobsen --role admin
.venv/bin/family-workspace serve
```

Open `http://localhost:8765`. Install and authenticate Codex first to use the actual
agent terminal. For a deliberate local PTY smoke test, set
`FAMILY_TERMINAL_COMMAND='["/bin/sh"]'` before starting the service. That is a shell,
not an AI substitute. The development HTTP setting must not be used for an
Internet-facing deployment.

When `FAMILY_DEV_HTTP=1` and `FAMILY_ORIGIN` is a local HTTP address, both
`localhost` and `127.0.0.1` work on that configured port, including the terminal.
Other ports and origins remain blocked. Production accepts only `FAMILY_ORIGIN`.
An `Origin rejected` response means the browser address does not match the
configured origins; the response includes the configured workspace URL.

The terminal runs with the service user's OS rights. The supplied production
units deliberately run as root for this family's isolated VPS. Interactive Codex
permissions remain governed by its configured approval/sandbox policies. Select
the desired host-administration permissions during trusted setup.

## Deploy to an isolated Ubuntu or Debian VPS

1. Extract the package to `/opt/family-platform` on a VPS dedicated to one family.
2. Set DNS for your chosen domain and allow inbound HTTPS/HTTP using your provider
   firewall. Preserve SSH/provider-console recovery. The installer does not rewrite
   your existing base firewall or SSH configuration.
3. Install nftables according to your host firewall policy. Review its packaged
   service/config before enabling it; sample rulesets can flush existing rules.
   Install and authenticate Codex CLI as the service account (root for the supplied
   units), following the official instructions: https://developers.openai.com/codex/cli/.
   Confirm `codex` is available in the systemd PATH from the service files.
4. From `/opt/family-platform`, run:

```bash
sudo bash tools/install-systemd.sh family.your-domain.tld
```

The installer installs Python runtime dependencies, Caddy and iproute2;
creates a root-only configuration; asks for an administrator password; registers
the MCP toolbox; installs the two systemd services; and validates the proxy config.
The new-host installer refuses an existing `/etc/caddy/Caddyfile`. On an existing
proxy, merge the supplied configuration and service templates manually.

The web process binds only `127.0.0.1:8765`. Caddy terminates TLS and forwards browser
HTTP/WebSocket traffic. Only the loopback proxy is trusted for forwarded addresses.
Use one web-service process; its in-memory PTY is deliberately owned by that process.

The worker starts but has zero funded tokens, so it cannot dispatch a job. Its
configured Codex command grants host-wide access for explicitly approved jobs on
this isolated VPS. Family and agent-proposed jobs need approval in the browser.
The optional defender may use a separately configured standing authorization for
its fixed investigation scope. Members cannot access the root terminal, approve jobs, or inspect
administrative host metadata. Family intent records are shared across the household.

### Fund the dispatch ledger and add family members

Run these commands in a root shell with the same environment as the services:

```bash
set -a
source /etc/family-workspace.env
set +a
/opt/family-platform/.venv/bin/family-workspace add-user family-member
/opt/family-platform/.venv/bin/family-workspace budget --funded 100000 --reserve 10000
```

`--funded` sets the cumulative funded token-unit total, not an increment and not
a currency amount. The ledger counts total input plus output tokens, including
cached input within the reported input count. It does not model different prices.

The agent learns about durable family intentions through the `family` MCP server.
Start with: "Read our family memory and journal. Propose one useful small task."
The initializer places operator instructions in the runtime workspace's `AGENTS.md`.
The worker and terminal both use that directory.

To authorize automatic bounded incident investigations, set
`FAMILY_DEFENDER_ALLOWANCE=5000` (or another chosen allowance) in the root-owned
environment file and restart both services. This is explicit standing approval
for the fixed investigation prompt in `guardian.py`, not permission to approve
arbitrary remediation. It uses the same funded budget and one-agent slot. With
zero funding or an occupied console, deterministic monitoring/containment continues
while the investigation waits. The default allowance of 0 makes no model calls.

### Budget semantics

The worker selects the oldest due, approved job that fits available resources;
an oversized pending task does not block smaller useful work. It reserves that
job's estimate before launch, excluding the configured
dispatch reserve. It reconciles actual tokens after reported Codex completions.
An overrun can exceed the estimate and stop subsequent dispatches. A missing usage
report keeps the reservation attached to an outcome-unknown job.

This is a **dispatch admission policy, not a hard inference or billing cap**.
The PTY is deliberately an administrative console and is not token-metered by this
application. An agent with root can modify local configuration and records. Use
provider/account controls outside the VPS's administrative authority for a hard
spending boundary. Budgets are authorized by humans; the agent cannot approve or
fund itself through the MCP API.

### Enable source containment

Start in `observe` mode and inspect actual listener and SSH-monitor findings. Use
the browser to approve the exact listener observation you inspected. A changed
observation is rejected until reviewed again.

Before enabling containment, verify provider-console access and a stable operator
or bastion address. In `/etc/family-workspace.env`, set real recovery CIDRs, then:

```text
FAMILY_RECOVERY_NETWORKS=YOUR_STABLE_OPERATOR_IPV4/32,YOUR_STABLE_OPERATOR_IPV6/128
FAMILY_GUARD_MODE=contain
```

Restart the workspace service to load changes. Automatic source blocks expire in
the kernel after 15 minutes. No LLM is in the packet-filtering path. Only SSH
authentication failure bursts currently trigger automatic containment; changed
listeners and low disk space are findings for investigation. Web login attempts
are throttled and recorded, with no automatic web-source blocking in this base.

The guardian needs local systemd SSH journal access and `ss`. If your SSH service
has another unit name or logs only to files, adapt the typed observation tool for
that host; a missing journal is reported as degraded, never as a clean audit.

## Recovery and maintenance

- `systemctl status family-workspace family-worker caddy` checks supervision.
- `journalctl -u family-workspace -u family-worker` checks runtime failures.
- `family-workspace backup` creates a consistent SQLite snapshot. Do not copy a
  live SQLite database without its WAL or a proper backup operation.
- Copy encrypted backups off-host and test restoring to a separate machine. Local
  snapshots cannot survive loss or compromise of this VPS.
- If locked out, use the provider console; switch to observe mode and inspect the
  dedicated table with `nft list table inet family_guard`. Removing only that table
  (`nft delete table inet family_guard`) removes this guardian's blocks while
  preserving other firewall tables. Never flush the whole host ruleset.
- A service restart stops the interactive PTY. Codex's own local history remains
  on disk; reconnect and use Codex's resume functionality where appropriate.
- Investigate an outcome-unknown job before using the browser reconciliation
  action. If exact token usage cannot be recovered, use at least the reserved
  allowance conservatively. A retry must be a new, explicitly approved proposal.
- Routine OS/application updates are agent/admin operations, supported by full
  OS access. This base does not autonomously rewrite SSH, run distro upgrades, or
  claim to prove host integrity from inside a potentially compromised root account.

For toolbox updates, keep `/etc/family-workspace.env`, `/var/lib/family-workspace`
and root's Codex configuration unchanged; stop the services, replace reviewed
toolbox code, install locked dependencies and the package, test, then restart.
The package does not impose a release workflow on generated family applications.

## Extend through capabilities

Add a named, validated operation to `family_workspace/toolbox.py`; expose its
input schema and document its effects, permissions, idempotency and recovery.
Keep authentication material out of schemas, Git, journal entries and prompts.
External systems remain authoritative for their own records; store the IDs and
decisions needed to reconnect and reconcile.

For each generated feature, persist its intention, accepted behavior, necessary
configuration, and external references. Cache working generated artifacts under
the runtime workspace while useful. The agent can rebuild them without losing
the family's commitments or repeating external actions.

Each additional family gets a separate VPS, state directory and provider identity.
Share the reusable toolbox, never another household's brain or credentials.

## Verification

```bash
.venv/bin/python -m pytest -q
node --check family_workspace/static/app.js
bash -n tools/install-systemd.sh
```

Tests cover permission boundaries, durable recovery and backups, job admission,
uncertain effects, usage overruns, MCP transport, actual PTY reconnects, and mocked
source containment. They never change the test machine's firewall or call a paid
model. Live Codex/authentication, provider limits, Caddy certificates, systemd
installation and real nftables behavior require a deployment acceptance check.

The npm lockfile and `tools/vendor.py` reproduce the bundled xterm assets. Runtime
pages load local assets only. npm is not needed by the running web application.

## Primary references

- Codex CLI: https://developers.openai.com/codex/cli/
- Codex background execution and usage events: https://developers.openai.com/codex/noninteractive/
- MCP lifecycle: https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- nftables element expiry: https://wiki.nftables.org/wiki-nftables/index.php/Element_timeouts
