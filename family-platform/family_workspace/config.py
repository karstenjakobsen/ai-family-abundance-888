import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Config:
    state: Path = field(default_factory=lambda: Path(os.getenv("FAMILY_STATE", "/var/lib/family-workspace")))
    origin: str = field(default_factory=lambda: os.getenv("FAMILY_ORIGIN", "https://family.example.com").rstrip("/"))
    secure_cookie: bool = field(default_factory=lambda: os.getenv("FAMILY_DEV_HTTP", "0") != "1")
    terminal_command: list = field(default_factory=lambda: json.loads(os.getenv("FAMILY_TERMINAL_COMMAND", '["codex"]')))
    job_command: list = field(default_factory=lambda: json.loads(os.getenv("FAMILY_JOB_COMMAND", '["codex","exec","--json","--skip-git-repo-check","-"]')))
    guard_mode: str = field(default_factory=lambda: os.getenv("FAMILY_GUARD_MODE", "observe"))
    recovery_networks: list = field(default_factory=lambda: [x.strip() for x in os.getenv("FAMILY_RECOVERY_NETWORKS", "").split(",") if x.strip()])
    defender_allowance: int = field(default_factory=lambda: int(os.getenv("FAMILY_DEFENDER_ALLOWANCE", "0")))
    guard_interval: int = 30
    session_seconds: int = 43200
    job_timeout: int = 900

    @property
    def allowed_origins(self):
        origins = {self.origin}
        configured = urlsplit(self.origin)
        # Local development may be opened via either loopback name. Keep the
        # scheme and port fixed; production never receives additional origins.
        if (not self.secure_cookie and configured.scheme == "http"
                and configured.hostname in {"localhost", "127.0.0.1"}):
            port = f":{configured.port}" if configured.port is not None else ""
            origins.update(f"http://{host}{port}" for host in ("localhost", "127.0.0.1"))
        return origins

    def prepare(self):
        os.umask(0o077)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("workspace", "backups"):
            (self.state / name).mkdir(exist_ok=True, mode=0o700)
        if self.guard_mode not in ("observe", "contain"):
            raise ValueError("FAMILY_GUARD_MODE must be observe or contain")
        if self.defender_allowance != 0 and not 100 <= self.defender_allowance <= 1_000_000:
            raise ValueError("Defender allowance must be 0 or from 100 to 1,000,000 tokens")
        if not self.terminal_command or not all(isinstance(x, str) for x in self.terminal_command):
            raise ValueError("Terminal command must be a nonempty JSON string array")

    @property
    def db(self):
        return self.state / "brain.sqlite3"
