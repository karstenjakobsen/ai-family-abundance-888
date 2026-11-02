"""Acquire the PTY in a fresh session, then replace this process with Codex."""
import fcntl
import os
import sys
import termios

if __name__ == "__main__":
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    try:
        os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
    except OSError as error:
        print(f"Could not start {sys.argv[1]}: {error}. Install/authenticate Codex on this VPS.", flush=True)
        sys.exit(127)

