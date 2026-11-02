import asyncio
import json
import os
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .brain import Brain
from .config import Config
from .guardian import Guardian
from .terminal import Terminal


class Login(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class Record(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    metadata: dict = Field(default_factory=dict)


class Job(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20000)
    allowance: int = Field(default=10000, ge=100, le=1_000_000)
    due: datetime | None = None


class Status(BaseModel):
    status: str


class Resolution(BaseModel):
    actual_tokens: int = Field(ge=0)


class Baseline(BaseModel):
    listeners: list[str] = Field(max_length=200)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        consumed = 0
        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > 65536:
                    raise HTTPException(413, "Request too large")
            return message
        await self.app(scope, limited_receive, send)


def create_app(config=None, monitor=True):
    config = config or Config()
    config.prepare()
    brain = Brain(config.db)
    guardian = Guardian(config, brain)
    terminal = Terminal(config, brain)
    cookie = "__Host-family_session" if config.secure_cookie else "family_session"
    attempts = OrderedDict()
    allowed_origins = config.allowed_origins

    async def observe():
        while True:
            try:
                await asyncio.to_thread(guardian.tick)
                # SQLite online backups are transaction-consistent even in WAL mode.
                today = datetime.now().strftime("%Y-%m-%d")
                backup = config.state / "backups" / (today + ".sqlite3")
                if not backup.exists():
                    await asyncio.to_thread(brain.backup, backup)
                    brain.append("guardian", "backup.created", {"filename": backup.name})
                    for old in sorted((config.state / "backups").glob("*.sqlite3"))[:-7]:
                        old.unlink()
            except Exception as error:
                brain.append("guardian", "monitor.error", {"type": type(error).__name__, "detail": str(error)[:200]})
            await asyncio.sleep(config.guard_interval)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(observe()) if monitor else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        terminal.close()

    app = FastAPI(title="Family workspace", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.brain, app.state.guardian, app.state.terminal = brain, guardian, terminal
    host = urlsplit(config.origin).hostname
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list({host, "localhost", "127.0.0.1"}))
    app.add_middleware(BodyLimit)

    @app.middleware("http")
    async def boundaries(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("origin") not in allowed_origins:
            return JSONResponse({"detail": f"Origin rejected. Open the workspace at {config.origin}."}, 403)
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 65536:
            return JSONResponse({"detail": "Request too large"}, 413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, 400)

    def current(request: Request):
        user = brain.user_for(request.cookies.get(cookie))
        if not user:
            raise HTTPException(401, "Please sign in")
        return user

    def admin(user=Depends(current)):
        if user["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        return user

    @app.post("/api/login")
    def login(payload: Login, request: Request, response: Response):
        peer = request.client.host if request.client else "unknown"
        now = time.monotonic()
        history = attempts.setdefault(peer, deque())
        attempts.move_to_end(peer)
        if len(attempts) > 1000:
            attempts.popitem(last=False)
        while history and history[0] < now - 60:
            history.popleft()
        if len(history) >= 5:
            raise HTTPException(429, "Too many attempts; retry in a minute")
        history.append(now)
        token = brain.authenticate(payload.name, payload.password, config.session_seconds)
        if not token:
            brain.append("web", "security.web_login_failure", {"peer": peer})
            raise HTTPException(401, "Sign-in failed")
        response.set_cookie(cookie, token, max_age=config.session_seconds, httponly=True,
                            secure=config.secure_cookie, samesite="strict", path="/")
        return brain.user_for(token)

    @app.post("/api/logout")
    def logout(request: Request, response: Response, user=Depends(current)):
        brain.logout(request.cookies[cookie])
        response.delete_cookie(cookie, secure=config.secure_cookie, httponly=True, samesite="strict", path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(user=Depends(current)):
        return user

    @app.get("/api/records")
    def records(q: str = "", user=Depends(current)):
        return brain.records(q[:200])

    @app.post("/api/records")
    def record(payload: Record, user=Depends(current)):
        return {"id": brain.record(user["id"], **payload.model_dump())}

    @app.post("/api/records/{ident}/status")
    def record_status(ident: str, payload: Status, user=Depends(current)):
        brain.set_status(user["id"], ident, payload.status)
        return {"ok": True}

    @app.get("/api/journal")
    def journal(user=Depends(current)):
        # Members see shared family activity, not admin/security operational metadata.
        rows = brain.journal(200)
        return rows if user["role"] == "admin" else [r for r in rows if r["action"].startswith(("record.", "job."))]

    @app.get("/api/jobs")
    def jobs(user=Depends(current)):
        rows = brain.jobs()
        return rows if user["role"] == "admin" else [r for r in rows if r["actor"] == user["id"]]

    @app.post("/api/jobs")
    def job(payload: Job, user=Depends(current)):
        if payload.due and payload.due.tzinfo is None:
            raise ValueError("A scheduled job must include a time zone")
        return {"id": brain.create_job(user["id"], payload.title, payload.prompt, payload.allowance,
                                      payload.due.timestamp() if payload.due else None)}

    @app.post("/api/jobs/{ident}/approve")
    def approve(ident: str, user=Depends(admin)):
        brain.approve(user["id"], ident)
        return {"ok": True}

    @app.post("/api/jobs/{ident}/resolve")
    def resolve(ident: str, payload: Resolution, user=Depends(admin)):
        brain.resolve_job(user["id"], ident, payload.actual_tokens)
        return {"ok": True}

    @app.get("/api/status")
    def status(user=Depends(current)):
        return {"budget": brain.budget() if user["role"] == "admin" else None,
                "guard_mode": config.guard_mode, "process_uid": os.geteuid() if user["role"] == "admin" else None,
                "agent_running": bool(terminal.process and terminal.process.poll() is None)}

    @app.get("/api/host")
    def host_status(user=Depends(admin)):
        return brain.setting("guard_report", {"mode": config.guard_mode, "findings": [{"code": "awaiting_first_audit"}]})

    @app.post("/api/host/audit")
    def audit(user=Depends(admin)):
        report = guardian.tick()
        brain.append(user["id"], "host.audit_requested")
        return report

    @app.post("/api/host/baseline")
    def baseline(payload: Baseline, user=Depends(admin)):
        report = guardian.audit()
        if any(f["code"] == "listeners_unavailable" for f in report["findings"]):
            raise ValueError("Cannot approve an unavailable listener observation")
        if sorted(payload.listeners) != report["listeners"]:
            raise ValueError("Listeners changed since observation. Run another audit before approval.")
        brain.set_setting("listener_baseline", report["listeners"], user["id"])
        return {"ok": True}

    @app.post("/api/terminal/stop")
    async def release_terminal(user=Depends(admin)):
        brain.append(user["id"], "terminal.release_requested")
        terminal.close()
        for queue in tuple(terminal.clients):
            if not queue.full():
                queue.put_nowait({"type": "ended"})
        return {"ok": True}

    @app.websocket("/ws/terminal")
    async def terminal_socket(ws: WebSocket):
        token = ws.cookies.get(cookie)
        user = brain.user_for(token)
        if ws.headers.get("origin") not in allowed_origins or not user or user["role"] != "admin":
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=128)
        terminal.clients.add(queue)
        brain.append(user["id"], "terminal.attached")
        try:
            terminal.start()
            await ws.send_json({"type": "output", "data": terminal.replay})

            async def send_output():
                while True:
                    if not brain.user_for(token):
                        await ws.close(code=1008)
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), 10)
                    except TimeoutError:
                        await ws.send_json({"type": "heartbeat"})
                        continue
                    await ws.send_json(event)
                    if event["type"] == "slow":
                        await ws.close(code=1013)
                        return

            async def receive_input():
                while True:
                    raw = await ws.receive_text()
                    if len(raw) > 16384 or not brain.user_for(token):
                        await ws.close(code=1008)
                        return
                    event = json.loads(raw)
                    if event.get("type") == "input":
                        terminal.write(event.get("data", ""))
                    elif event.get("type") == "resize":
                        terminal.resize(event.get("cols", 80), event.get("rows", 24))

            tasks = [asyncio.create_task(send_output()), asyncio.create_task(receive_input())]
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (WebSocketDisconnect, OSError, ValueError, RuntimeError):
            with suppress(RuntimeError):
                await ws.close(code=1011)
        finally:
            terminal.clients.discard(queue)
            brain.append(user["id"], "terminal.detached")

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/healthz")
    def health():
        return {"ok": True}

    return app
