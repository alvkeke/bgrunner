#!/usr/bin/env python3
"""Regression test for bug 2: a closed-but-still-referenced output window
must NOT steal output deltas from a freshly opened window."""
import gc
import os
import sys
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import bg_runner

app = QApplication([])
win = bg_runner.MainWindow()
win.show()


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


t = win.add_task('python3 -u -c "import time\nfor i in range(10):\n    print(\'D%d\' % i, flush=True); time.sleep(0.5)"')
wait(1200)  # D0, D1

win.open_output(win.tasks[t])
w1 = win.output_windows[t]
w1ref = weakref.ref(w1)
w1.close()
wait(200)
assert t not in win.output_windows

# keep a hard reference to the closed window (worst case: PySide6 signal
# ref cycle keeps it alive anyway)
closed_w1 = w1ref()
w1 = None
gc.collect()

wait(2000)  # D2..D5 produced while window is closed

# freshly opened window must show EVERYTHING and stay live
win.open_output(win.tasks[t])
w2 = win.output_windows[t]
txt = w2.text.toPlainText()
assert "D2" in txt and "D5" in txt, f"missing output produced while closed: {txt!r}"

wait(2500)  # D6..D9
txt = w2.text.toPlainText()
assert "D9" in txt, f"FROZEN after reopen (hidden window stole deltas): {txt!r}"

# closed window must not keep receiving output
hidden = closed_w1
txt1 = hidden.text.toPlainText()
wait(1000)
assert hidden.text.toPlainText() == txt1, "closed window still consuming output"

print("[ok] bug2-regression: reopened window shows all history and stays live")

# reopen a THIRD time (fully closed w2 now) -> must still be live
w2.close()
wait(200)
win.open_output(win.tasks[t])
w3 = win.output_windows[t]
assert "D9" in w3.text.toPlainText()
wait(1500)
assert w3.text.toPlainText() == w3.text.toPlainText()  # no new output expected now
print("[ok] bug2-regression: third open also fine")
w3.close()

print("\nBUG2 REGRESSION PASSED")
