#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" != 0 ]]; then
  echo 'Run as root on your own isolated Ubuntu/Debian VPS.' >&2
  exit 1
fi
if [[ "$(pwd -P)" != /opt/family-platform ]]; then
  echo 'Extract the project at /opt/family-platform, then run this script there.' >&2
  exit 1
fi
domain="${1:-}"
if [[ ! "$domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]] || [[ "$domain" != *.* ]]; then
  echo 'Usage: bash tools/install-systemd.sh family.your-domain.tld' >&2
  exit 1
fi
if ! command -v codex >/dev/null; then
  echo 'Install and authenticate the Codex CLI as root using the official instructions first.' >&2
  exit 1
fi
if ! command -v nft >/dev/null; then
  echo 'Install nftables using your host firewall policy first. Review its service/config before enabling it; sample rulesets may flush existing rules.' >&2
  exit 1
fi
if [[ -e /etc/caddy/Caddyfile ]]; then
  echo 'Existing Caddy configuration detected. Merge deploy/Caddyfile.example manually instead of overwriting it with this new-host installer.' >&2
  exit 1
fi
if [[ -e /etc/family-workspace.env ]]; then
  echo 'Existing installation detected. Use the documented update procedure to preserve configuration.' >&2
  exit 1
fi
apt-get update
apt-get install -y python3-venv caddy iproute2
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps .
install -m 600 deploy/environment.example /etc/family-workspace.env
python3 - "$domain" <<'PY'
import pathlib, sys
domain = sys.argv[1]
path = pathlib.Path('/etc/family-workspace.env')
path.write_text(path.read_text().replace('family.example.com', domain))
pathlib.Path('/etc/caddy/Caddyfile').write_text(pathlib.Path('deploy/Caddyfile.example').read_text().replace('family.example.com', domain))
PY
set -a
source /etc/family-workspace.env
set +a
.venv/bin/family-workspace init
.venv/bin/family-workspace add-user admin --role admin
codex mcp add family -- /opt/family-platform/.venv/bin/family-workspace mcp
install -m 644 deploy/family-workspace.service deploy/family-worker.service /etc/systemd/system/
caddy validate --config /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now family-workspace.service family-worker.service
systemctl enable --now caddy
systemctl reload caddy
echo "Workspace configured at https://$domain. Jobs remain unfunded until you set a budget."
