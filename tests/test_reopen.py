#!/usr/bin/env python3
"""Reproduce bug 2: reopen output window -> frozen output, no live append."""
import os
import sys

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


t = win.add_task('python3 -u -c "import time\nfor i in range(8):\n    print(\'L%d\' % i, flush=True); time.sleep(0.8)"')
wait(1500)  # L0, L1
win.open_output(win.tasks[t])
w1 = win.output_windows[t]
print("w1 shows:", repr(w1.text.toPlainText()))
wait(2000)  # L2,L3,L4 live append
print("w1 after live:", repr(w1.text.toPlainText()))

w1.close()
wait(200)
print("dict after close:", "w1 in dict?" , w1 in win.output_windows.values(), "count:", len(win.output_windows))
wait(2000)  # L5, L6 produced while closed

win.open_output(win.tasks[t])
w2 = win.output_windows[t]
print("w2 shows:", repr(w2.text.toPlainText()))
assert "L5" in w2.text.toPlainText(), f"MISSING output produced while closed! got {w2.text.toPlainText()!r}"

wait(2000)  # L7 live append
print("w2 after live:", repr(w2.text.toPlainText()))
assert "L7" in w2.text.toPlainText(), f"FROZEN: no live append after reopen! got {w2.text.toPlainText()!r}"

print("\nREPRO COMPLETE: bug not reproduced (works fine)")
