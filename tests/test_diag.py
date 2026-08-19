#!/usr/bin/env python3
"""Diagnose: does setChildProcessModifier + killpg actually kill children?"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QProcess, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication([])


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# 1. API availability
p = QProcess()
print("has setChildProcessModifier:", hasattr(p, "setChildProcessModifier"))

# 2. does the callback run, does setpgid stick?
mod_called = []

def modifier(pid):
    mod_called.append(pid)
    try:
        os.setpgid(0, 0)
    except Exception as e:
        mod_called.append(("ERR", repr(e)))

p.setProcessChannelMode(QProcess.MergedChannels)
p.setChildProcessModifier(modifier)
p.start("/bin/sh", ["-c", "echo HI; sleep 0.2; echo BYE"])
wait(1000)
print("modifier called:", mod_called)
print("output:", bytes(p.readAllStandardOutput()).decode().strip())

# 3. group-kill test with a shell that spawns children
p2 = QProcess()
p2.setProcessChannelMode(QProcess.MergedChannels)
p2.setChildProcessModifier(lambda pid: os.setpgid(0, 0))
p2.start("/bin/sh", ["-c", "sleep 300 & sleep 300"])
wait(1000)
shell_pid = p2.processId()
print("shell pid:", shell_pid)
try:
    print("shell pgid:", os.getpgid(shell_pid), "== pid?", os.getpgid(shell_pid) == shell_pid)
except Exception as e:
    print("getpgid err:", e)

children = []
for pid in os.listdir("/proc"):
    if pid.isdigit():
        try:
            with open(f"/proc/{pid}/stat") as f:
                st = f.read()
            parts = st[st.rfind(")") + 2:].split()
            ppid = int(parts[1])
            comm = st[st.find("(") + 1:st.rfind(")")]
            if ppid == shell_pid:
                children.append((int(pid), comm))
        except Exception:
            pass
print("children of shell:", children)

os.killpg(shell_pid, 15)
wait(1500)
alive = [comm for pid, comm in children if os.path.exists(f"/proc/{pid}")]
print("children STILL ALIVE after killpg(SIGTERM):", alive)
print("p2 state:", p2.state())
