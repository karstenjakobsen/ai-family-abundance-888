"""Typed capability registry shared by the stdio MCP transport and local callers."""
import subprocess
import time


def schema(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


STRING = {"type": "string"}
TOOLS = [
    {"name": "memory_search", "description": "Find shared family requests, goals, events, notes and finance intentions. No arbitrary SQL.", "inputSchema": schema({"query": STRING})},
    {"name": "memory_create", "description": "Save durable family intent. metadata is data, not executable instructions.", "inputSchema": schema({"kind": {"type": "string", "enum": ["request", "goal", "event", "job", "finance", "note"]}, "title": STRING, "body": STRING, "metadata": {"type": "object"}}, ["kind", "title"])},
    {"name": "memory_status", "description": "Record progress on an existing family item.", "inputSchema": schema({"id": STRING, "status": {"type": "string", "enum": ["open", "active", "done", "blocked"]}}, ["id", "status"])},
    {"name": "journal_read", "description": "Read the latest durable actions, decisions, incidents and checkpoints.", "inputSchema": schema()},
    {"name": "journal_note", "description": "Persist a decision, evidence or resumable checkpoint. Never include secrets.", "inputSchema": schema({"text": STRING, "subject": STRING}, ["text"])},
    {"name": "jobs_list", "description": "List pending, completed and outcome-unknown jobs. Unknown effects require reconciliation before retry.", "inputSchema": schema()},
    {"name": "jobs_propose", "description": "Create an immutable job proposal; a human administrator approves it in the browser.", "inputSchema": schema({"title": STRING, "prompt": STRING, "allowance": {"type": "integer", "minimum": 100, "maximum": 1000000}}, ["title", "prompt", "allowance"])},
    {"name": "budget_read", "description": "Read funded token units and the dispatch reserve. This is NOT a provider-enforced spending cap.", "inputSchema": schema()},
    {"name": "host_audit", "description": "Observe disk space and TCP listeners, and compare the approved listener baseline. No mutation.", "inputSchema": schema()},
    {"name": "host_backup", "description": "Create a transaction-consistent local SQLite backup. Off-host recovery remains separate.", "inputSchema": schema()},
    {"name": "host_contain_source", "description": "Temporarily block one attack source for 15 minutes. Requires enabled containment; recovery addresses are rejected.", "inputSchema": schema({"ip": STRING}, ["ip"])},
    {"name": "host_restart_service", "description": "Restart only family-workspace.service or family-worker.service. May interrupt this conversation.", "inputSchema": schema({"service": {"type": "string", "enum": ["family-workspace.service", "family-worker.service"]}}, ["service"])},
]


def call(name, args, config, brain, guardian):
    actor = "agent"
    definition = next((t for t in TOOLS if t["name"] == name), None)
    if not definition or not isinstance(args, dict):
        raise ValueError("Unknown tool or invalid arguments")
    spec = definition["inputSchema"]
    if set(args) - set(spec["properties"]) or set(spec["required"]) - set(args):
        raise ValueError("Unexpected or missing tool arguments")
    for key, value in args.items():
        kind = spec["properties"][key].get("type")
        if kind == "string" and (not isinstance(value, str) or len(value) > 20000):
            raise ValueError("Expected bounded string: " + key)
        if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("Expected integer: " + key)
        if kind == "object" and not isinstance(value, dict):
            raise ValueError("Expected object: " + key)
    if name == "memory_search":
        return brain.records(args.get("query", ""))
    if name == "memory_create":
        return {"id": brain.record(actor, **args)}
    if name == "memory_status":
        brain.set_status(actor, args["id"], args["status"])
    elif name == "journal_read":
        return brain.journal()
    elif name == "journal_note":
        brain.append(actor, "agent.note", {"text": args["text"]}, args.get("subject"))
    elif name == "jobs_list":
        return brain.jobs()
    elif name == "jobs_propose":
        return {"id": brain.create_job(actor, **args)}
    elif name == "budget_read":
        return brain.budget()
    elif name == "host_audit":
        return guardian.audit()
    elif name == "host_backup":
        path = config.state / "backups" / ("manual-" + str(time.time_ns()) + ".sqlite3")
        result = brain.backup(path)
        brain.append(actor, "backup.created", {"filename": path.name})
        return {"path": result}
    elif name == "host_contain_source":
        guardian.block(args["ip"], actor)
    elif name == "host_restart_service":
        service = args["service"]
        if service not in ("family-workspace.service", "family-worker.service"):
            raise ValueError("Service is outside this tool's allowlist")
        brain.append(actor, "service.restart_requested", {"service": service})
        result = subprocess.run(["systemctl", "restart", service], capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError("Service restart failed; inspect systemd status")
        brain.append(actor, "service.restarted", {"service": service})
    return {"ok": True}

