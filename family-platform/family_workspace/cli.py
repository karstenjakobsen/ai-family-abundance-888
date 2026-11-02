import argparse
import asyncio
import getpass
import json
from pathlib import Path

from .brain import Brain
from .config import Config


def main():
    parser = argparse.ArgumentParser(description="Family workspace host tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    user = commands.add_parser("add-user")
    user.add_argument("name")
    user.add_argument("--role", choices=["admin", "member"], default="member")
    budget = commands.add_parser("budget")
    budget.add_argument("--funded", type=int)
    budget.add_argument("--reserve", type=int)
    for command in ("serve", "worker", "mcp", "audit", "backup"):
        commands.add_parser(command)
    args = parser.parse_args()
    config = Config()
    config.prepare()
    brain = Brain(config.db)
    if args.command == "init":
        policy = config.state / "workspace" / "AGENTS.md"
        if not policy.exists():
            policy.write_text((Path(__file__).parent / "OPERATOR.md").read_text())
        print("Initialized durable state and workspace instructions at", config.state)
    elif args.command == "add-user":
        password = getpass.getpass("Password (12+ characters): ")
        if password != getpass.getpass("Confirm password: "):
            parser.error("Passwords differ")
        brain.add_user(args.name, password, args.role)
        print("User created:", args.name, args.role)
    elif args.command == "budget":
        for key, value in (("funded_tokens", args.funded), ("reserve_tokens", args.reserve)):
            if value is not None:
                if value < 0:
                    parser.error("Budget values must be nonnegative")
                brain.set_setting(key, value)
        print(json.dumps(brain.budget(), indent=2))
    elif args.command == "serve":
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(config), host="127.0.0.1", port=8765, workers=1,
                    proxy_headers=True, forwarded_allow_ips="127.0.0.1", ws_max_size=16384)
    elif args.command == "worker":
        from .worker import serve
        asyncio.run(serve(config, brain))
    elif args.command == "mcp":
        from .mcp import serve
        serve(config, brain)
    elif args.command == "audit":
        from .guardian import Guardian
        print(json.dumps(Guardian(config, brain).tick(), indent=2))
    elif args.command == "backup":
        import time
        print(brain.backup(config.state / "backups" / ("manual-" + str(time.time_ns()) + ".sqlite3")))


if __name__ == "__main__":
    main()

