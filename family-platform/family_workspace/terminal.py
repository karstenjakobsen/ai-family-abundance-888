import asyncio
import codecs
import fcntl
import os
import pty
import signal
import struct
import subprocess
import sys
import termios


class Terminal:
    """One shared administrator PTY; browser disconnects do not terminate it."""
    def __init__(self, config, brain):
        self.config, self.brain = config, brain
        self.process = None
        self.master = None
        self.clients = set()
        self.replay = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.slot = None

    def start(self):
        if self.process and self.process.poll() is None:
            return
        self.close()
        self.slot = (self.config.state / "agent.lock").open("a+")
        try:
            fcntl.flock(self.slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.slot.close()
            self.slot = None
            raise ValueError("An unattended job owns the agent slot. Reconnect after it finishes.")
        master, slave = pty.openpty()
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor", FAMILY_STATE=str(self.config.state))
        try:
            self.process = subprocess.Popen([sys.executable, "-m", "family_workspace.pty_child", *self.config.terminal_command],
                                            stdin=slave, stdout=slave, stderr=slave,
                                            cwd=self.config.state / "workspace", env=env, start_new_session=True)
        except Exception:
            os.close(master)
            self.slot.close()
            self.slot = None
            raise
        finally:
            os.close(slave)
        self.master = master
        os.set_blocking(master, False)
        self.decoder.reset()
        self.replay = ""
        asyncio.get_running_loop().add_reader(master, self.read)
        self.brain.append("terminal", "terminal.started", {"pid": self.process.pid})

    def read(self):
        try:
            data = os.read(self.master, 16384)
            if not data:
                raise OSError("PTY closed")
        except BlockingIOError:
            return
        except OSError:
            self.close()
            self.brain.append("terminal", "terminal.ended")
            for queue in tuple(self.clients):
                if not queue.full():
                    queue.put_nowait({"type": "ended"})
            return
        text = self.decoder.decode(data)
        self.replay = (self.replay + text)[-131072:]
        for queue in tuple(self.clients):
            if queue.full():
                self.clients.discard(queue)
                # A slow browser is disconnected instead of blocking the agent.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "slow"})
            else:
                queue.put_nowait({"type": "output", "data": text})

    def write(self, text):
        if not isinstance(text, str) or len(text.encode()) > 8192:
            raise ValueError("Terminal input too large")
        if self.master is None:
            raise ValueError("Terminal is not running")
        os.write(self.master, text.encode())

    def resize(self, cols, rows):
        if self.master is not None:
            cols, rows = max(20, min(300, int(cols))), max(5, min(100, int(rows)))
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def detach_reader(self):
        if self.master is not None:
            try:
                asyncio.get_running_loop().remove_reader(self.master)
            except RuntimeError:
                pass

    def close(self):
        self.detach_reader()
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
            except ProcessLookupError:
                pass
        if self.master is not None:
            os.close(self.master)
        self.master = None
        self.process = None
        if self.slot is not None:
            self.slot.close()
            self.slot = None

