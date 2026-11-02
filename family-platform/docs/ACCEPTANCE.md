# First VPS acceptance

Use a disposable or recoverable isolated VPS for the first deployment. The source
package has not itself provisioned a host or opened any public root console.

1. Confirm HTTPS, secure session cookies, expected DNS, and provider-console recovery.
2. Create an admin and member. Verify that the member can save an intention and
   propose a job, but cannot attach a terminal, inspect host details or approve jobs.
3. Open the admin terminal. Complete Codex trust/authentication and permission setup.
   Ask the agent to read `family` MCP memory and create a journal checkpoint.
4. Reconnect the browser and confirm the existing PTY continues. Restart the web
   service and confirm SQLite records persist; resume Codex history as appropriate.
5. Configure external spending controls and a conservative dispatch budget. Propose
   and approve one harmless read-only job, release the interactive console, and
   confirm a result, thread ID, actual token count and journal entry appear.
6. Interrupt a harmless job after a known test side effect. Confirm it becomes
   outcome unknown and the queue remains stopped until reconciliation.
7. Verify `ss` and SSH journal access. Inspect and approve a known-good listener
   baseline. Open a temporary test listener; confirm it is reported, then remove it.
8. Inspect local backups and restore a snapshot to a separate state directory.
   Add encrypted off-host copies and test provider-console access independently.
9. Configure actual recovery CIDRs. In a controlled environment, enable containment
   and verify the allowlist and a 15-minute block from a disposable test source.
   Never use the only administrator connection as the test source.
10. Review model permissions and confirm administrative authority is limited to this
    family's infrastructure and mandate. Give other families separate VPSs.

This base observes a limited set of host signals. Add integration-specific
observations and response capabilities from evidence of actual operational needs.

