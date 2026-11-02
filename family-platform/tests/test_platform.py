import asyncio
import io
import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from family_workspace.app import create_app
from family_workspace.brain import Brain
from family_workspace.config import Config
from family_workspace.guardian import Guardian
from family_workspace.mcp import serve as mcp_serve
from family_workspace.worker import execute


@pytest.fixture
def config(tmp_path):
    value = Config(state=tmp_path, origin="http://localhost", secure_cookie=False)
    value.prepare()
    return value


@pytest.fixture
def brain(config):
    return Brain(config.db)


@pytest.fixture
def client(config):
    app = create_app(config, monitor=False)
    app.state.brain.add_user("adult", "a-valid-test-password", "admin")
    app.state.brain.add_user("child", "another-test-password", "member")
    with TestClient(app, base_url="http://localhost", headers={"origin": "http://localhost"}) as value:
        yield value


def login(client, name="adult", password="a-valid-test-password"):
    response = client.post("/api/login", json={"name": name, "password": password})
    assert response.status_code == 200, response.text
    return response


def test_auth_csrf_and_no_default_account(client):
    assert client.get("/api/records").status_code == 401
    assert client.post("/api/login", headers={"origin": "https://evil.example"}, json={"name": "adult", "password": "a-valid-test-password"}).status_code == 403
    response = login(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/api/me").json()["role"] == "admin"
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("configured_host", ["localhost", "127.0.0.1"])
@pytest.mark.parametrize("browser_host", ["localhost", "127.0.0.1"])
def test_local_login_accepts_loopback_aliases(tmp_path, configured_host, browser_host):
    config = Config(state=tmp_path, origin=f"http://{configured_host}:8765", secure_cookie=False)
    app = create_app(config, monitor=False)
    app.state.brain.add_user("adult", "a-valid-test-password", "admin")
    browser_origin = f"http://{browser_host}:8765"
    with TestClient(app, base_url=browser_origin, headers={"origin": browser_origin}) as client:
        login(client)
        assert client.get("/api/me").json()["name"] == "adult"
        assert client.post("/api/logout").status_code == 200


@pytest.mark.parametrize("origin", [None, "null", "http://localhost:8766",
    "http://127.0.0.1:8766", "https://localhost:8765", "http://evil.example:8765",
    "http://localhost.evil.example:8765", "http://192.168.1.2:8765"])
def test_local_login_still_rejects_untrusted_origins(tmp_path, origin):
    config = Config(state=tmp_path, origin="http://localhost:8765", secure_cookie=False)
    app = create_app(config, monitor=False)
    with TestClient(app, base_url=config.origin) as client:
        headers = {} if origin is None else {"origin": origin}
        response = client.post("/api/login", headers=headers, json={"name": "adult", "password": "irrelevant"})
        assert response.status_code == 403
        assert config.origin in response.json()["detail"]


@pytest.mark.parametrize("origin,secure", [("https://family.example.com", True),
    ("http://localhost:8765", True), ("https://localhost:8765", False),
    ("http://family.example.com", False)])
def test_production_and_non_loopback_origins_remain_exact(tmp_path, origin, secure):
    config = Config(state=tmp_path, origin=origin, secure_cookie=secure)
    assert config.allowed_origins == {origin}
    app = create_app(config, monitor=False)
    with TestClient(app, base_url=origin) as client:
        assert client.post("/api/login", headers={"origin": "http://127.0.0.1:8765"}, json={}).status_code == 403
        assert client.post("/api/login", headers={"origin": origin}, json={}).status_code == 422


def test_member_can_propose_but_not_administer_host_or_approve(client):
    login(client, "child", "another-test-password")
    result = client.post("/api/records", json={"kind": "goal", "title": "Build a bird house", "body": "Make room for everyone"})
    assert result.status_code == 200
    assert client.get("/api/records").json()[0]["title"] == "Build a bird house"
    job = client.post("/api/jobs", json={"title": "Make a plan", "prompt": "Draft a materials list", "allowance": 1000}).json()
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 403
    assert client.get("/api/host").status_code == 403
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect("ws://localhost/ws/terminal", headers={"origin": "http://localhost"}):
            pytest.fail("Member obtained root console")
    assert denied.value.code == 1008


def test_login_throttle_and_invalid_metadata(client):
    for _ in range(5):
        assert client.post("/api/login", json={"name": "missing", "password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"name": "adult", "password": "a-valid-test-password"}).status_code == 429


def test_chunked_request_size_bound(client):
    login(client)
    payload = iter([b'{"kind":"note","title":"large","body":"', b'x' * 70000, b'"}'])
    assert client.post("/api/records", content=payload, headers={"content-type": "application/json"}).status_code == 413


def test_baseline_requires_exact_observed_listeners(client, monkeypatch):
    login(client)
    guardian = client.app.state.guardian
    monkeypatch.setattr(guardian, "audit", lambda: {"listeners": ["127.0.0.1:8765"], "findings": []})
    assert client.post("/api/host/baseline", json={"listeners": ["0.0.0.0:9999"]}).status_code == 400
    assert client.post("/api/host/baseline", json={"listeners": ["127.0.0.1:8765"]}).status_code == 200


def test_restart_preserves_intent_and_consistent_backup(config, brain):
    ident = brain.record("parent", "goal", "Family board", "Usable by touch")
    backup = config.state / "backups" / "test.sqlite3"
    brain.backup(backup)
    restored = Brain(backup)
    assert restored.records()[0]["id"] == ident
    assert any(r["action"] == "record.created" for r in restored.journal())
    assert Brain(config.db).records()[0]["body"] == "Usable by touch"


def funded_job(brain, allowance=1000):
    brain.set_setting("funded_tokens", 10000)
    brain.set_setting("reserve_tokens", 1000)
    ident = brain.create_job("parent", "A small task", "Write a verified result", allowance)
    brain.approve("parent", ident)
    return ident


def test_dispatch_is_atomic_and_reserve_is_preserved(brain):
    ident = funded_job(brain)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: brain.claim_job(), range(2)))
    assert sum(r is not None for r in results) == 1
    assert brain.budget()["used_or_reserved"] == 1000
    brain.finish_job(ident, "done", "Verified", actual=500)
    second = brain.create_job("parent", "Too large", "Too large", 9000)
    brain.approve("parent", second)
    assert brain.claim_job() is None
    assert brain.budget()["dispatch_available"] == 8500


def test_unknown_effects_hold_budget_and_all_further_dispatch(brain):
    ident = funded_job(brain)
    assert brain.claim_job()
    brain.recover_jobs()
    second = brain.create_job("parent", "Next", "Next", 1000)
    brain.approve("parent", second)
    assert brain.jobs()[-1]["status"] == "outcome_unknown"
    assert brain.claim_job() is None
    assert brain.budget()["used_or_reserved"] == 1000
    brain.resolve_job("parent", ident, 900)
    assert brain.claim_job()["id"] == second


def test_oversized_pending_job_does_not_deadlock_smaller_work(brain):
    funded_job(brain, allowance=10000)
    small = brain.create_job("parent", "Useful small step", "Complete one small step", 1000)
    brain.approve("parent", small)
    assert brain.claim_job()["id"] == small


def test_defender_standing_authorization_is_opt_in_and_deduplicated(config, brain):
    guardian = Guardian(config, brain)
    evidence = {"ip": "198.51.100.7", "failures": 12}
    assert guardian.escalate("ssh_attack", evidence) is None
    assert brain.jobs() == []
    config.defender_allowance = 1000
    ident = guardian.escalate("ssh_attack", evidence)
    assert ident
    assert guardian.escalate("ssh_attack", evidence) is None
    job = brain.jobs()[0]
    assert job["approved"] == 1 and job["allowance"] == 1000
    assert "propose an exact job for human approval" in job["prompt"]
    assert brain.claim_job() is None  # Authorized scope still needs funded resources.


def test_actual_overrun_stops_future_dispatch(config, brain):
    ident = funded_job(brain)
    job = brain.claim_job()
    config.job_command = [sys.executable, "-c", "import sys,json;sys.stdin.read();print(json.dumps({'type':'thread.started','thread_id':'test-thread'}));print(json.dumps({'type':'turn.completed','usage':{'input_tokens':11000,'output_tokens':2}}))"]
    asyncio.run(execute(config, brain, job))
    row = next(j for j in brain.jobs() if j["id"] == ident)
    assert row["status"] == "done" and row["actual_tokens"] == 11002
    assert brain.budget()["dispatch_available"] == 0


def test_timed_out_process_with_possible_side_effect_is_never_replayed(config, brain):
    ident = funded_job(brain)
    job = brain.claim_job()
    marker = config.state / "effect"
    config.job_timeout = .15
    config.job_command = [sys.executable, "-c", f"import pathlib,time,sys;sys.stdin.read();pathlib.Path({str(marker)!r}).write_text('effect');time.sleep(20)"]
    asyncio.run(execute(config, brain, job))
    assert marker.exists()
    assert brain.jobs()[0]["status"] == "outcome_unknown"
    assert brain.claim_job() is None


def test_missing_agent_releases_reservation_without_claiming_completion(config, brain):
    funded_job(brain)
    job = brain.claim_job()
    config.job_command = ["/nonexistent/family-agent"]
    asyncio.run(execute(config, brain, job))
    assert brain.jobs()[0]["status"] == "failed"
    assert brain.budget()["used_or_reserved"] == 0


def test_mcp_lifecycle_tools_and_durable_effects(config, brain):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_create", "arguments": {"kind": "note", "title": "Remember this"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "host_restart_service", "arguments": {"service": "ssh.service; reboot"}}},
    ]
    output = io.StringIO()
    mcp_serve(config, brain, io.StringIO("\n".join(json.dumps(r) for r in requests)), output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]["result"]["protocolVersion"] == "2025-11-25"
    assert len(replies[1]["result"]["tools"]) >= 10
    assert brain.records()[0]["title"] == "Remember this"
    assert replies[-1]["result"]["isError"] is True


def test_recovery_ranges_invalid_addresses_and_observe_mode_cannot_block(config, brain):
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    config.recovery_networks = ["203.0.113.0/24", "2001:db8::/32"]
    guardian = Guardian(config, brain, runner)
    for address in ("127.0.0.1", "203.0.113.10", "2001:db8::1", "1.2.3.4; reboot", "198.51.100.7"):
        with pytest.raises(ValueError):
            guardian.block(address)
    assert calls == []


def test_containment_only_changes_own_table_and_has_kernel_expiry(config, brain):
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1 if args[:3] == ["nft", "-j", "list"] else 0, "", "")
    config.guard_mode = "contain"
    config.recovery_networks = ["203.0.113.0/24"]
    guardian = Guardian(config, brain, runner)
    guardian.block("198.51.100.17")
    script = calls[1][1]["input"]
    assert "flush" not in script and "policy accept" in script
    assert "family_guard" in script
    assert calls[-1][0][-3:] == ["timeout", "15m", "}"]
    assert any(r["action"] == "security.source_blocked" for r in brain.journal())


def test_ssh_events_are_normalized_and_cursor_prevents_repeat(config, brain):
    event = {"MESSAGE": "Failed password for invalid user x from 198.51.100.8 port 123 ssh2", "__REALTIME_TIMESTAMP": str(int(time.time()*1_000_000)), "__CURSOR": "cursor-10"}
    commands = []
    def runner(args, **kwargs):
        commands.append(args)
        text = "\n".join(json.dumps(event) for _ in range(10)) if "--after-cursor" not in args else ""
        return subprocess.CompletedProcess(args, 0, text, "")
    guardian = Guardian(config, brain, runner)
    guardian.poll_ssh()
    guardian.poll_ssh()
    incidents = [r for r in brain.journal() if r["action"] == "security.ssh_attack"]
    assert len(incidents) == 1
    assert "--after-cursor" in commands[-1]
    assert "MESSAGE" not in incidents[0]["data"]


@pytest.mark.parametrize("browser_origin", ["http://localhost", "http://127.0.0.1"])
def test_actual_pty_survives_browser_reconnect(config, browser_origin):
    config.terminal_command = [sys.executable, "-u", "-c", "import sys;print('PTY_READY',flush=True);\nfor line in sys.stdin: print('RESULT:'+line.strip(),flush=True)"]
    app = create_app(config, monitor=False)
    app.state.brain.add_user("adult", "a-valid-test-password", "admin")
    websocket_url = browser_origin.replace("http://", "ws://") + "/ws/terminal"
    with TestClient(app, base_url=browser_origin, headers={"origin": browser_origin}) as client:
        login(client)
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(websocket_url, headers={"origin": "http://localhost:9999"}):
                pytest.fail("Unexpected terminal origin was accepted")
        assert denied.value.code == 1008
        with client.websocket_connect(websocket_url, headers={"origin": browser_origin}) as ws:
            output = ""
            for _ in range(10):
                message = ws.receive_json()
                output += message.get("data", "")
                if "PTY_READY" in output:
                    break
            assert "PTY_READY" in output
            ws.send_json({"type": "input", "data": "hello-family\n"})
            for _ in range(10):
                message = ws.receive_json()
                output += message.get("data", "")
                if "RESULT:hello-family" in output:
                    break
            assert "RESULT:hello-family" in output
            pid = app.state.terminal.process.pid
        with client.websocket_connect(websocket_url, headers={"origin": browser_origin}) as ws:
            replay = ws.receive_json()
            assert "RESULT:hello-family" in replay["data"]
            assert app.state.terminal.process.pid == pid
        assert not any("hello-family" in r["data"] for r in app.state.brain.journal())
        assert client.post('/api/terminal/stop').status_code == 200
        assert app.state.terminal.process is None and app.state.terminal.slot is None
