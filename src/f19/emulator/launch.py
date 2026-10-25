"""Start the DOSBox-X build that has the automation socket, by launching the binary with arguments (no AppleScript).

  launch()                                    # F-19's config (Settings.dosbox_conf): what scripts/launch_f19.sh runs
  launch(conf=..., port=6123, headless=True)  # e.g. a bare DOS prompt for testing the socket
  wait_ready(port=6123)                       # until the automation port accepts a connection

The automation port comes from the config's `[sdl] automation port`; `port=` overrides it on the command line
(`-set "sdl automation port=N"`). headless=True sets SDL_VIDEODRIVER/SDL_AUDIODRIVER=dummy (no window, no sound).
Nothing here sends keys: starting F-19 is up to the config's [autoexec].
"""
import os
import socket
import subprocess
import time
from pathlib import Path

from f19.core.settings import settings as _settings


def command(conf=None, port=None, savedir=None, noautoexec=False, extra=(), binary=None):
    """The argv list for DOSBox-X."""
    argv = [str(binary or _settings.dosbox_bin), "-conf", str(conf or _settings.dosbox_conf)]
    if port is not None: argv += ["-set", f"sdl automation port={int(port)}"]
    if savedir is not None: argv += ["-savedir", str(savedir)]
    if noautoexec: argv.append("-noautoexec")
    return argv + [str(a) for a in extra]


def launch(conf=None, port=None, savedir=None, noautoexec=False, extra=(), headless=False, log=None, binary=None):
    """Start DOSBox-X; returns the Popen. log: a path for stdout+stderr (default: inherit)."""
    env = dict(os.environ)
    if headless: env.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    out = open(log, "wb") if log else None
    try:
        return subprocess.Popen(command(conf, port, savedir, noautoexec, extra, binary), env=env,
                                stdout=out, stderr=subprocess.STDOUT if out else None, start_new_session=True)
    finally:
        if out: out.close()


def port_open(host=None, port=None, timeout=0.5):
    try:
        with socket.create_connection((host or _settings.host, int(port or _settings.port)), timeout=timeout):
            return True
    except OSError:
        return False


def wait_ready(host=None, port=None, timeout=30.0, proc=None):
    """Poll until the automation port listens. Raises TimeoutError, or RuntimeError if `proc` exits first.
    NOTE: the server serves one client at a time, and this probe connects (and disconnects) once."""
    end = time.time() + timeout
    while time.time() < end:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"dosbox-x exited with {proc.returncode} before the automation port opened")
        if port_open(host, port): return
        time.sleep(0.25)
    raise TimeoutError(f"automation port {port or _settings.port} not open after {timeout:.0f} s")


def stop(proc, timeout=5.0):
    """SIGTERM, then SIGKILL after `timeout` (DOSBox-X itself; key-sending scripts are stopped with SIGINT instead)."""
    if proc.poll() is not None: return proc.returncode
    proc.terminate()
    try: return proc.wait(timeout)
    except subprocess.TimeoutExpired:
        proc.kill(); return proc.wait(timeout)


BARE_PROMPT_CONF = """[sdl]
automation port = {port}
[dosbox]
memsize = 16
[autoexec]
"""


def write_bare_conf(path, port):
    """A config with only the automation port and an empty [autoexec]: a bare Z:\\> prompt, no game."""
    Path(path).write_text(BARE_PROMPT_CONF.format(port=int(port)))
    return Path(path)


if __name__ == "__main__":
    import sys
    os.execv(str(_settings.dosbox_bin), command(extra=sys.argv[1:]))
