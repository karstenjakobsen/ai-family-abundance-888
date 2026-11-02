# Package verification

Automated checks run in the build environment:

- 18 Python tests: authentication and origin checks; member/admin boundaries;
  chunked body bounds; exact listener-baseline approval; durable records and online
  backups; atomic budget reservations; outcome-unknown recovery; useful work behind
  oversized pending jobs; opt-in defender investigation and deduplication; usage
  overruns; timeout recovery after a possible side effect; missing executable;
  stdio MCP lifecycle; validated source containment; normalized SSH evidence; and
  real PTY reconnects and explicit release.
- JavaScript syntax check with Node.
- Installer syntax check with Bash.
- Python wheel build and packaged resource inspection.

No paid model was called and no test changed the machine's firewall. Codex CLI is
not installed/authenticated in the build environment; subprocess integration was
tested with deterministic local executables. nftables behavior was checked using
recorded command arguments and mocked command results, not a live ruleset.

Visual browser QA remains unverified. The browser and execution environment have
different loopback networks; the supported managed preview runner could not launch
the Python virtual environment. No production security checks were weakened to
work around that limitation.

The live deployment checks in ACCEPTANCE.md cover actual Codex authentication,
model/provider spending controls, HTTPS and cookies, systemd supervision, browser
behavior, journal access, kernel containment expiry, and off-host restoration.
