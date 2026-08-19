#!/usr/bin/env python3
"""Offscreen smoke tests for bg_runner (no display needed)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

import bg_runner

app = QApplication([])
win = bg_runner.MainWindow()
win.show()


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# --- 1. needs_shell detection ---
assert bg_runner.needs_shell("ls | wc -l"), "pipe should need shell"
assert bg_runner.needs_shell("a && b"), "&& should need shell"
assert bg_runner.needs_shell("echo x > /tmp/f"), "redirect should need shell"
assert bg_runner.needs_shell("ls *.py"), "glob should need shell"
assert not bg_runner.needs_shell("ssh -N -L 8080:localhost:8080 user@host"), \
    "plain ssh should NOT need shell"
assert not bg_runner.needs_shell('echo "a | b"'), "piped string in quotes is fine"
print("[ok] needs_shell")

# --- 2. runnable_command ---
assert bg_runner.runnable_command("/tmp/x.py") == "python3 /tmp/x.py"
assert bg_runner.runnable_command("/tmp/x.sh") == "bash /tmp/x.sh"
assert bg_runner.runnable_command("/tmp/noext") is None
print("[ok] runnable_command")

# --- 3. direct exec task ---
t1 = win.add_task("echo hello-from-task")
assert t1 is not None and t1.shell is False
wait(1500)
assert t1.state == bg_runner.Task.FINISHED, f"t1 state={t1.state_text} exit={t1.exit_code}"
assert "hello-from-task" in t1.full_output, repr(t1.full_output)
print("[ok] direct exec task ->", t1.state_text, "exit", t1.exit_code)

# --- 4. shell pipeline task ---
t2 = win.add_task("printf 'a\\nb\\nc\\n' | wc -l")
assert t2.shell is True
wait(1500)
assert t2.state == bg_runner.Task.FINISHED, f"t2 state={t2.state_text} exit={t2.exit_code}"
assert t2.full_output.strip() == "3", repr(t2.full_output)
print("[ok] shell pipeline task ->", t2.full_output.strip())

# --- 5. failing task ---
t5 = win.add_task('python3 -c "import sys; sys.exit(3)"')
wait(1500)
assert t5.state == bg_runner.Task.FAILED and t5.exit_code == 3, f"t5={t5.state_text} exit={t5.exit_code}"
print("[ok] failing task ->", t5.state_text, "exit", t5.exit_code)

# --- 6. stop a running task (process-group kill) ---
t3 = win.add_task("sleep 60")
wait(800)
assert t3.state == bg_runner.Task.RUNNING, f"t3 should be running, got {t3.state_text}"
t3.stop()
wait(4500)  # allow SIGTERM + 3s force-kill window
assert t3.state != bg_runner.Task.RUNNING, f"t3 still running: {t3.state_text}"
print("[ok] stop running task ->", t3.state_text, "exit", t3.exit_code)

# --- 7. output window shows buffered + live output ---
win.open_output(win.tasks[t1])
win2 = win.output_windows[t1]
assert "hello-from-task" in win2.text.toPlainText()
t4 = win.add_task("python3 -u -c \"import time; print('line-1'); time.sleep(1); print('line-2')\"")
win.open_output(win.tasks[t4])
win4 = win.output_windows[t4]
wait(300)
assert "line-1" in win4.text.toPlainText()
wait(2000)
assert "line-2" in win4.text.toPlainText()
assert t4.state == bg_runner.Task.FINISHED
print("[ok] output window live updates")

# --- 8. status bar counters ---
running = sum(1 for t in win.tasks if t.state == bg_runner.Task.RUNNING)
assert running == 0, f"all tasks should be done, running={running}"
print("[ok] status:", win.statusBar().currentMessage())

# --- 9. clear finished ---
before = len(win.tasks)
win.clear_finished()
assert len(win.tasks) == 0, f"clear_finished left {len(win.tasks)}"
print(f"[ok] clear_finished ({before} -> 0)")

# ================= regression tests for reported bugs =================

import subprocess

# --- BUG 1: stop must kill the whole process tree (shell children too) ---
def pgrep_sleep() -> set:
    try:
        return set(subprocess.check_output(["pgrep", "-x", "sleep"]).split())
    except subprocess.CalledProcessError:
        return set()

before_sleep = pgrep_sleep()
t_shell = win.add_task("sleep 300 & sleep 300")
assert t_shell.shell is True
wait(1000)
mid_sleep = pgrep_sleep()
assert len(mid_sleep - before_sleep) >= 2, f"expected 2 sleeps, got {mid_sleep - before_sleep}"
t_shell.stop()
wait(5000)  # allow SIGTERM + 3s force-kill window
after_sleep = pgrep_sleep()
assert after_sleep == before_sleep, f"sleep processes leaked: {after_sleep - before_sleep}"
assert t_shell.state != bg_runner.Task.RUNNING
print("[ok] bug1: stop kills whole tree (shell children), state =", t_shell.state_text)

# --- BUG 2: reopening the console shows previously buffered output ---
t_hist = win.add_task('python3 -u -c "print(\'hello-history\')"')
wait(1000)
win.open_output(win.tasks[t_hist])
w1 = win.output_windows[t_hist]
assert "hello-history" in w1.text.toPlainText()
w1.close()
wait(150)
assert t_hist not in win.output_windows
win.open_output(win.tasks[t_hist])  # second open
w2 = win.output_windows[t_hist]
assert "hello-history" in w2.text.toPlainText(), "history must be visible on reopen"
print("[ok] bug2: second open shows previous output")
w2.close()

# --- BUG 3: ANSI escape codes are rendered as colors, not raw text ---
t_ansi = win.add_task(
    'python3 -c "print(\'\\x1b[31mRED-TEXT\\x1b[0m plain \\x1b[1;32mBOLD-GREEN\\x1b[0m\')"'
)
wait(1500)
win.open_output(win.tasks[t_ansi])
w3 = win.output_windows[t_ansi]
txt = w3.text.toPlainText()
assert "\x1b" not in txt, f"escape codes leaked: {txt!r}"
assert "RED-TEXT" in txt and "plain" in txt and "BOLD-GREEN" in txt, repr(txt)
# verify the color was actually applied: first char after 'RED-TEXT' start
cur = w3.text.textCursor()
cur.setPosition(1)
fg = cur.charFormat().foreground().color().name()
assert fg == "#cd0000", f"expected red foreground, got {fg}"
cur.setPosition(len("RED-TEXT plain ") + 3)  # inside BOLD-GREEN
fg2 = cur.charFormat().foreground().color().name()
assert fg2 == "#00cd00", f"expected green, got {fg2}"  # ANSI 32 = dark green
assert cur.charFormat().fontWeight() == QFont.Bold, "bold attribute lost"
print("[ok] bug3: ANSI colors rendered, no escape text:", repr(txt.strip()))
w3.close()

# --- cleanup leaked tasks from this section ---
win.clear_finished()
assert len(win.tasks) == 0

print("\nALL SMOKE TESTS PASSED")
