#!/usr/bin/env python3
"""Start or stop WorkBuddy2API-Hub as a detached background process."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / "run"
PID_FILE = RUN_DIR / "wb-proxy.json"
OUT_LOG = RUN_DIR / "wb-proxy.out.log"
ERR_LOG = RUN_DIR / "wb-proxy.err.log"

IS_WINDOWS = os.name == "nt"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
WAIT_TIMEOUT = 0x102


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_ulong),
        ("dwHighDateTime", ctypes.c_ulong),
    ]


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _open_process(pid: int, access: int):
    from ctypes import wintypes
    kernel32 = _kernel32()
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(access, False, int(pid))
    return handle or None


def _close_handle(handle) -> None:
    try:
        _kernel32().CloseHandle(handle)
    except Exception:
        pass


def _process_token(pid: int):
    """Return the process creation token, or None when the PID is not alive."""
    if not IS_WINDOWS:
        try:
            os.kill(int(pid), 0)
            return "posix"
        except OSError:
            return None

    from ctypes import wintypes
    access = PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    handle = _open_process(pid, access)
    if not handle:
        return None
    try:
        kernel32 = _kernel32()
        created = _FILETIME()
        exited = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value != STILL_ACTIVE:
            return None
        return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    finally:
        _close_handle(handle)


def _wait_for_exit(pid: int, expected_token=None, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        token = _process_token(pid)
        if token is None:
            return True
        if expected_token is not None and str(token) != str(expected_token):
            return True
        time.sleep(0.15)
    return _process_token(pid) is None


def _terminate_pid(pid: int, expected_token=None) -> bool:
    """Terminate one verified PID. Returns False only on a real failure."""
    pid = int(pid)
    token = _process_token(pid)
    if token is None:
        return True
    if expected_token is not None and str(token) != str(expected_token):
        return True

    if not IS_WINDOWS:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return _wait_for_exit(pid, token, 5.0)

    from ctypes import wintypes
    access = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE
    handle = _open_process(pid, access)
    if not handle:
        return False
    try:
        kernel32 = _kernel32()
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        if not kernel32.TerminateProcess(handle, 0):
            return False
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        result = kernel32.WaitForSingleObject(handle, 5000)
        return result != WAIT_TIMEOUT
    finally:
        _close_handle(handle)


def _read_record():
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("pid"):
            return data
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass
    return None


def _write_record(data: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    temp = PID_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, PID_FILE)


def _remove_record() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _record_alive(record: dict) -> bool:
    pid = int(record.get("pid") or 0)
    token = _process_token(pid)
    if token is None:
        return False
    expected = record.get("created")
    return expected is None or str(token) == str(expected)


def _health(port: int, timeout: float = 0.6):
    url = "http://127.0.0.1:%s/health" % int(port)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if int(getattr(response, "status", 0)) != 200:
                return None
            raw = response.read(65536)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        if (
            isinstance(data, dict)
            and data.get("ok") is True
            and "accounts" in data
            and "api_key_required" in data
        ):
            return data
    except Exception:
        return None
    return None


def _tail_log():
    for path in (ERR_LOG, OUT_LOG):
        try:
            data = path.read_bytes()[-5000:]
        except OSError:
            continue
        text = data.decode("utf-8", errors="replace").strip()
        if text:
            return path, text
    return None


def _print_log_tail() -> None:
    item = _tail_log()
    if not item:
        return
    path, text = item
    print()
    print("Last log output (%s):" % path)
    print(text[-3500:])


def _listener_pids(port: int):
    if not IS_WINDOWS:
        return []
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    wanted = str(int(port))
    pids = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if "LISTENING" not in parts[3].upper():
            continue
        local = parts[1]
        if ":" not in local:
            continue
        if local.rsplit(":", 1)[1] != wanted:
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _command_line(pid: int) -> str:
    if not IS_WINDOWS:
        try:
            return Path("/proc/%s/cmdline" % int(pid)).read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except OSError:
            return ""

    commands = [
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-CimInstance Win32_Process -Filter 'ProcessId=%s').CommandLine" % int(pid),
        ],
        ["wmic", "process", "where", "processid=%s" % int(pid), "get", "commandline", "/value"],
    ]
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            continue
        text = (result.stdout or "").strip()
        if not text:
            continue
        if "CommandLine=" in text:
            text = text.split("CommandLine=", 1)[1]
        text = text.strip().strip('"')
        if "wb_proxy.py" in text.lower():
            return text
    return ""


def _is_wb_proxy_for_port(pid: int, port: int) -> bool:
    command = _command_line(pid).lower().replace("/", "\\")
    if "wb_proxy.py" not in command:
        return False
    return ("--port %s" % int(port)) in command or ("--port=%s" % int(port)) in command


def _print_running(record=None) -> None:
    if record:
        port = record.get("port", "?")
        print("WorkBuddy2API-Hub is already running in the background.")
        print("PID       : %s" % record.get("pid"))
        print("Dashboard : http://127.0.0.1:%s/" % port)
        print("Stop      : run stop.bat")
    print("Output log: %s" % OUT_LOG)
    print("Error log : %s" % ERR_LOG)


def cmd_start(args) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    existing = _read_record()
    if existing:
        if _record_alive(existing):
            _print_running(existing)
            return 0
        _remove_record()

    health = _health(args.port)
    if health:
        print("A WorkBuddy proxy is already responding on port %s." % args.port)
        print("It was not started by this background launcher.")
        print("Close its original window once; future starts will be managed by stop.bat.")
        return 0

    script = ROOT / "wb_proxy.py"
    if not script.is_file():
        print("[ERROR] wb_proxy.py not found: %s" % script)
        return 1

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with OUT_LOG.open("ab", buffering=0) as stdout_log:
        stdout_log.write(("\n===== %s start port %s =====\n" % (stamp, args.port)).encode("utf-8"))
    with ERR_LOG.open("ab", buffering=0) as stderr_log:
        stderr_log.write(("\n===== %s start port %s =====\n" % (stamp, args.port)).encode("utf-8"))

    shutdown_token = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["WB_SHUTDOWN_TOKEN"] = shutdown_token

    command = [sys.executable, str(script), "--port", str(args.port)]
    creationflags = 0
    start_new_session = False
    if IS_WINDOWS:
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        start_new_session = True

    try:
        with OUT_LOG.open("ab", buffering=0) as stdout_log, ERR_LOG.open("ab", buffering=0) as stderr_log:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_log,
                stderr=stderr_log,
                creationflags=creationflags,
                start_new_session=start_new_session,
                close_fds=True,
            )
    except Exception as exc:
        print("[ERROR] Could not start wb_proxy.py: %s" % exc)
        return 1

    token = _process_token(process.pid)
    if token is None:
        time.sleep(0.15)
        token = _process_token(process.pid)

    record = {
        "pid": int(process.pid),
        "created": token,
        "port": int(args.port),
        "script": str(script),
        "python": sys.executable,
        "started": stamp,
        "shutdown_token": shutdown_token,
    }
    _write_record(record)

    deadline = time.monotonic() + 10.0
    ready = False
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            _remove_record()
            print("[ERROR] The proxy exited during startup (code %s)." % code)
            _print_log_tail()
            return 1
        if _health(args.port, timeout=0.45):
            ready = True
            break
        time.sleep(0.25)

    print("WorkBuddy2API-Hub started in the background.")
    print("PID       : %s" % process.pid)
    print("API       : http://127.0.0.1:%s/v1" % args.port)
    print("Dashboard : http://127.0.0.1:%s/" % args.port)
    print("Stop      : run stop.bat")
    print("Output log: %s" % OUT_LOG)
    print("Error log : %s" % ERR_LOG)
    if not ready:
        print("Status    : process is alive but still starting; check the logs if it does not respond.")
    return 0


def _request_shutdown(port: int, token: str, timeout: float = 1.0) -> bool:
    if not token:
        return False
    url = "http://127.0.0.1:%s/internal/shutdown" % int(port)
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-WB-Shutdown-Token": str(token),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 0)) == 200
    except Exception:
        return False


def _graceful_stop(port: int, pid: int, token: str, expected_token=None) -> bool:
    if not _request_shutdown(port, token):
        return False
    return _wait_for_exit(pid, expected_token, 8.0)


def cmd_stop(args) -> int:
    record = _read_record()
    if record:
        if _record_alive(record):
            pid = int(record["pid"])
            if _graceful_stop(args.port, pid, record.get("shutdown_token"), record.get("created")):
                _remove_record()
                print("Stopped WorkBuddy2API-Hub background service (PID %s)." % pid)
                return 0
            if _terminate_pid(pid, record.get("created")):
                _remove_record()
                print("Stopped WorkBuddy2API-Hub background service (PID %s)." % pid)
                return 0
            print("[ERROR] Found PID %s but could not stop it (permission denied?)." % pid)
            return 1
        _remove_record()
        print("Removed a stale background PID record.")

    health = _health(args.port)
    if not health:
        print("WorkBuddy2API-Hub is not running on port %s." % args.port)
        return 0

    print("The service on port %s was not started by the new background launcher." % args.port)
    print("Checking its process command line before stopping it...")
    stopped = []
    for pid in _listener_pids(args.port):
        if not _is_wb_proxy_for_port(pid, args.port):
            continue
        token = _process_token(pid)
        if _terminate_pid(pid, token):
            stopped.append(pid)
    if stopped:
        print("Stopped WorkBuddy2API-Hub (PID %s)." % ", ".join(str(pid) for pid in stopped))
        return 0
    print("[ERROR] The service is running, but stop.bat could not verify its process.")
    print("Close the original launcher window, then run start-cn.bat again.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBuddy2API-Hub background launcher")
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("start", help="start the proxy in the background")
    start.add_argument("--port", type=int, default=8788)
    stop = sub.add_parser("stop", help="stop the background proxy")
    stop.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    if args.command == "start":
        return cmd_start(args)
    if args.command == "stop":
        return cmd_stop(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())