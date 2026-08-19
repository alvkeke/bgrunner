#!/usr/bin/env python3
"""BG Runner — a small PySide6 GUI to run and maintain background tasks.

Features:
  * type a command (e.g. an SSH tunnel) and run it as a background process
  * drag & drop script files to run them
  * live list of running/finished tasks with PID and status
  * double-click a task to see its console output (live)
  * stop / restart / copy commands from the context menu
"""

from __future__ import annotations

import io
import os
import shlex
import shutil
import signal
import sys
import time

from PySide6.QtCore import QProcess, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ``setsid`` makes the child its own session/process-group leader so that
# killing the group (os.killpg) stops the whole tree, not just the shell.
HAS_SETSID = shutil.which("setsid") is not None

# xterm-ish palette for ANSI colors 0-15
ANSI_COLORS = [
    "#000000", "#cd0000", "#00cd00", "#cdcd00",
    "#0000ee", "#cd00cd", "#00cdcd", "#e5e5e5",
    "#7f7f7f", "#ff0000", "#00ff00", "#ffff00",
    "#5c5cff", "#ff00ff", "#00ffff", "#ffffff",
]


# ---------------------------------------------------------------- helpers

def needs_shell(command: str) -> bool:
    """True when the command uses shell syntax (pipes, redirects, &&, globs,
    $ expansion, ~, ...) and therefore cannot be exec'd directly.

    A small scanner that respects quoting: characters inside single quotes are
    literal; inside double quotes only ``$`` and backticks still need a shell.
    """
    quote: str | None = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            if ch == '"':
                quote = None
            elif ch == "\\" and i + 1 < n and command[i + 1] in '"\\$`':
                i += 1
            elif ch in "$`":
                return True
        else:
            if ch in "'\"":
                quote = ch
            elif ch in "|&;<>$`*?~":
                return True
        i += 1
    return quote is not None  # unterminated quote needs a shell to make sense


def short_label(text: str, max_len: int = 70) -> str:
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def runnable_command(path: str) -> str | None:
    """Command that runs a dropped file, or None when it is not directly runnable."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".py", ".pyw"):
        return f"python3 {shlex.quote(path)}"
    if ext == ".sh":
        return f"bash {shlex.quote(path)}"
    if os.access(path, os.X_OK):
        return shlex.quote(path)
    return None


# ---------------------------------------------------------------- ANSI

class AnsiParser:
    """Streaming parser for ANSI/VT escape sequences.

    SGR sequences (``\x1b[..m``) are turned into QTextCharFormat, everything
    else (cursor moves, OSC titles, ...) is stripped, so colored console
    output shows real colors instead of raw escape text.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._fmt = QTextCharFormat()

    def parse(self, text: str) -> list[tuple[str, QTextCharFormat]]:
        """Return (plain_text, format) fragments for *text*."""
        data = self._buf + text
        self._buf = ""
        out: list[tuple[str, QTextCharFormat]] = []
        i = 0
        n = len(data)
        start = 0
        while i < n:
            if data[i] != "\x1b":
                i += 1
                continue
            if i > start:
                out.append((data[start:i], QTextCharFormat(self._fmt)))
            if i + 1 >= n:
                self._buf = data[i:]  # lone ESC, wait for the rest
                start = n
                break
            kind = data[i + 1]
            if kind == "[":
                end = i + 2
                while end < n and not ("\x40" <= data[end] <= "\x7e"):
                    end += 1
                if end >= n:
                    self._buf = data[i:]  # incomplete CSI
                    start = n
                    break
                if data[end] == "m":
                    self._apply_sgr(data[i + 2:end])
                i = end + 1
            elif kind == "]":
                # OSC sequence, ends with BEL or ESC \\
                end = i + 2
                while end < n and data[end] != "\x07" and not (
                    data[end] == "\x1b" and end + 1 < n and data[end + 1] == "\\"
                ):
                    end += 1
                if end >= n:
                    self._buf = data[i:]  # incomplete OSC
                    start = n
                    break
                i = end + 2 if data[end] == "\x1b" else end + 1
            elif "\x40" <= kind <= "\x5f":
                i += 2  # two-char escape, e.g. ESC 7 / ESC (
            else:
                i += 1  # stray ESC, drop it
            start = i
        if start < n:
            out.append((data[start:], QTextCharFormat(self._fmt)))
        return out

    def _apply_sgr(self, params: str) -> None:
        if not params:
            nums = [0]
        else:
            try:
                nums = [int(x) for x in params.split(";") if x != ""]
            except ValueError:
                nums = [0]
        j = 0
        while j < len(nums):
            p = nums[j]
            if p == 0:
                self._fmt = QTextCharFormat()
            elif p == 1:
                self._fmt.setFontWeight(QFont.Bold)
            elif p == 22:
                self._fmt.setFontWeight(QFont.Normal)
            elif p == 4:
                self._fmt.setFontUnderline(True)
            elif p == 24:
                self._fmt.setFontUnderline(False)
            elif 30 <= p <= 37:
                self._fmt.setForeground(QBrush(QColor(ANSI_COLORS[p - 30])))
            elif 90 <= p <= 97:
                self._fmt.setForeground(QBrush(QColor(ANSI_COLORS[p - 90 + 8])))
            elif p == 39:
                self._fmt.clearForeground()
            elif 40 <= p <= 47:
                self._fmt.setBackground(QBrush(QColor(ANSI_COLORS[p - 40])))
            elif 100 <= p <= 107:
                self._fmt.setBackground(QBrush(QColor(ANSI_COLORS[p - 100 + 8])))
            elif p == 49:
                self._fmt.clearBackground()
            elif p == 38 and j + 1 < len(nums):
                mode = nums[j + 1]
                if mode == 5 and j + 2 < len(nums):
                    self._fmt.setForeground(QBrush(QColor(self._color256(nums[j + 2]))))
                    j += 2
                elif mode == 2 and j + 4 < len(nums):
                    self._fmt.setForeground(QBrush(
                        QColor(nums[j + 2], nums[j + 3], nums[j + 4])))
                    j += 4
            elif p == 48 and j + 1 < len(nums):
                mode = nums[j + 1]
                if mode == 5 and j + 2 < len(nums):
                    self._fmt.setBackground(QBrush(QColor(self._color256(nums[j + 2]))))
                    j += 2
                elif mode == 2 and j + 4 < len(nums):
                    self._fmt.setBackground(QBrush(
                        QColor(nums[j + 2], nums[j + 3], nums[j + 4])))
                    j += 4
            j += 1

    @staticmethod
    def _color256(idx: int) -> str:
        if idx < 16:
            return ANSI_COLORS[idx]
        if idx < 232:
            idx -= 16
            r, g, b = idx // 36, (idx // 6) % 6, idx % 6
            def level(v: int) -> int:
                return 0 if v == 0 else 55 + 40 * v
            return f"#{level(r):02x}{level(g):02x}{level(b):02x}"
        gray = 8 + (idx - 232) * 10
        return f"#{gray:02x}{gray:02x}{gray:02x}"


# ---------------------------------------------------------------- Task

class Task(QProcess):
    """A managed background process with buffered output."""

    output_appended = Signal(object)  # this task
    state_changed = Signal(object)    # this task

    RUNNING, FINISHED, FAILED, KILLED, ERROR = range(5)

    def __init__(self, command: str, shell: bool, parent=None):
        super().__init__(parent)
        self.command = command
        self.shell = shell
        self.pid: int | None = None
        self.exit_code: int | None = None
        self.state = Task.RUNNING
        self.started_at = time.time()
        self.finished_at: float | None = None
        self._output = io.StringIO()

        self.setProcessChannelMode(QProcess.MergedChannels)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.setInterval(3000)
        self._kill_timer.timeout.connect(self._force_kill)

        self.readyReadStandardOutput.connect(self._on_ready_read)
        self.started.connect(self._on_started)
        self.finished.connect(self._on_finished)
        self.errorOccurred.connect(self._on_error)

    # -- lifecycle -------------------------------------------------

    def launch(self) -> None:
        """Start the process. ``setsid`` makes it a session/process-group
        leader so stopping the task can kill the whole tree."""
        if self.shell:
            if HAS_SETSID:
                self.start("setsid", ["/bin/sh", "-c", self.command])
            else:
                self.start("/bin/sh", ["-c", self.command])
        else:
            parts = shlex.split(self.command)
            if not parts:
                self.state = Task.ERROR
                self.state_changed.emit(self)
                return
            if HAS_SETSID:
                self.start("setsid", [parts[0], *parts[1:]])
            else:
                self.start(parts[0], parts[1:])

    def stop(self) -> None:
        """SIGTERM the whole process tree, escalate to SIGKILL after 3 s."""
        pid = self.processId()
        if pid and pid > 0:
            self._signal_group(pid, signal.SIGTERM)
            self._kill_tree(pid, signal.SIGTERM)
        self.terminate()
        if self.state == Task.RUNNING:
            self._kill_timer.start()

    def _signal_group(self, pid: int, sig: int) -> None:
        if not hasattr(os, "killpg"):
            return
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _children_of(self, pid: int) -> list[int]:
        """Linux: direct children of *pid* read from /proc."""
        children: list[int] = []
        try:
            entries = os.listdir("/proc")
        except OSError:
            return children
        for ent in entries:
            if not ent.isdigit():
                continue
            try:
                with open(f"/proc/{ent}/stat") as f:
                    st = f.read()
                parts = st[st.rfind(")") + 2:].split()
                if int(parts[1]) == pid:
                    children.append(int(ent))
            except (OSError, ValueError, IndexError):
                continue
        return children

    def _kill_tree(self, pid: int, sig: int) -> None:
        """Recursively signal *pid* and all its descendants (fallback when
        the process group trick is unavailable)."""
        for child in self._children_of(pid):
            self._kill_tree(child, sig)
            try:
                os.kill(child, sig)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _force_kill(self) -> None:
        if self.state != Task.RUNNING:
            return
        pid = self.processId()
        if pid and pid > 0:
            self._signal_group(pid, signal.SIGKILL)
            self._kill_tree(pid, signal.SIGKILL)
        self.kill()

    # -- Qt slots --------------------------------------------------

    def _on_started(self) -> None:
        self.pid = self.processId()
        self.state = Task.RUNNING
        self.state_changed.emit(self)

    def _on_ready_read(self) -> None:
        data = bytes(self.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self._output.write(data)
            self.output_appended.emit(self)

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self.exit_code = code
        self.finished_at = time.time()
        if status == QProcess.CrashExit:
            self.state = Task.KILLED
        elif code == 0:
            self.state = Task.FINISHED
        else:
            self.state = Task.FAILED
        self._drain()
        self.state_changed.emit(self)

    def _on_error(self, err: QProcess.ProcessError) -> None:
        if err == QProcess.FailedToStart:
            self.state = Task.ERROR
            self._output.write(f"\n[bg-runner] failed to start: {self.command}\n")
            self.output_appended.emit(self)
            self.state_changed.emit(self)

    def _drain(self) -> None:
        data = bytes(self.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self._output.write(data)
            self.output_appended.emit(self)

    # -- output access ---------------------------------------------

    @property
    def full_output(self) -> str:
        return self._output.getvalue()

    def read_from(self, pos: int) -> tuple[str, int]:
        """Return (output since *pos*, new position).

        Every consumer (e.g. an OutputWindow) keeps its own position, so
        several windows can watch the same task independently without
        stealing each other's deltas.
        """
        value = self._output.getvalue()
        return value[pos:], len(value)

    @property
    def state_text(self) -> str:
        return {
            Task.RUNNING: "running",
            Task.FINISHED: "finished",
            Task.FAILED: "failed",
            Task.KILLED: "killed",
            Task.ERROR: "error",
        }[self.state]


# ---------------------------------------------------------------- windows

class OutputWindow(QWidget):
    """Shows the live console output of one task."""

    closed = Signal()
    restart_requested = Signal(object)  # task

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self.task = task
        self._shown_len = 0  # this window's own read position into task output
        self.setWindowTitle(self._title())
        self.resize(780, 430)

        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(200_000)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Monospace")
        font.setStyleHint(QFont.TypeWriter)
        self.text.setFont(font)

        self.copy_btn = QPushButton("Copy all")
        self.copy_btn.clicked.connect(self._copy_all)
        self.stop_btn = QPushButton("Stop task")
        self.stop_btn.clicked.connect(self.task.stop)
        self.restart_btn = QPushButton("Restart")
        self.restart_btn.clicked.connect(lambda: self.restart_requested.emit(self.task))

        bar = QHBoxLayout()
        bar.addWidget(self.copy_btn)
        bar.addWidget(self.stop_btn)
        bar.addWidget(self.restart_btn)
        bar.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.text)
        layout.addLayout(bar)

        self._ansi = AnsiParser()
        # show ALL buffered output (not just what arrived since the window
        # was last open), then sync this window's read position
        self._append(task.full_output)
        self._shown_len = len(task.full_output)
        task.output_appended.connect(self._on_output)
        task.state_changed.connect(self._on_state)
        self._on_state(task)

    def _title(self) -> str:
        return f"Output — {short_label(self.task.command, 60)}"

    def _on_output(self, task: Task) -> None:
        new, self._shown_len = task.read_from(self._shown_len)
        self._append(new)

    def _append(self, text: str) -> None:
        if not text:
            return
        sb = self.text.verticalScrollBar()
        stick = sb.value() >= sb.maximum() - 8
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.text.setTextCursor(cursor)
        for frag, fmt in self._ansi.parse(text):
            cursor.insertText(frag, fmt)
        if stick:
            sb.setValue(sb.maximum())

    def _on_state(self, task: Task) -> None:
        running = task.state == Task.RUNNING
        self.stop_btn.setEnabled(running)
        self.restart_btn.setEnabled(task.finished_at is not None)
        title = self._title()
        if not running:
            title += f"  [{task.state_text} exit={task.exit_code}]"
        self.setWindowTitle(title)

    def _copy_all(self) -> None:
        # copy the *rendered* text (ANSI sequences already stripped)
        QApplication.clipboard().setText(self.text.toPlainText())

    def closeEvent(self, event) -> None:
        # break the signal->window reference cycle so a closed window is
        # really destroyed and stops consuming task output
        try:
            self.task.output_appended.disconnect(self._on_output)
            self.task.state_changed.disconnect(self._on_state)
        except (RuntimeError, TypeError):
            pass
        self.closed.emit()
        super().closeEvent(event)


class HistoryLineEdit(QLineEdit):
    """A QLineEdit with up/down command history."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._index = 0

    def push(self, text: str) -> None:
        text = text.strip()
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._index = len(self._history)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Up:
            self._browse(-1)
            return
        if event.key() == Qt.Key_Down:
            self._browse(1)
            return
        super().keyPressEvent(event)

    def _browse(self, delta: int) -> None:
        if not self._history:
            return
        self._index = max(0, min(len(self._history), self._index + delta))
        if self._index == len(self._history):
            self.clear()
        else:
            self.setText(self._history[self._index])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BG Runner — background task manager")
        self.setAcceptDrops(True)
        self.resize(880, 500)

        self.tasks: dict[Task, QListWidgetItem] = {}
        self.output_windows: dict[Task, OutputWindow] = {}

        # ---- command bar ----
        self.cmd_edit = HistoryLineEdit(self)
        self.cmd_edit.setPlaceholderText(
            "Command, e.g.  ssh -N -L 8080:localhost:8080 user@host   (Enter to run, or drop a file)"
        )
        self.cmd_edit.returnPressed.connect(self.run_command)
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self.run_command)
        stop_btn = QPushButton("Stop selected")
        stop_btn.clicked.connect(self.stop_selected)
        clear_btn = QPushButton("Clear finished")
        clear_btn.clicked.connect(self.clear_finished)

        bar = QHBoxLayout()
        bar.addWidget(self.cmd_edit, 1)
        bar.addWidget(run_btn)
        bar.addWidget(stop_btn)
        bar.addWidget(clear_btn)

        # ---- task list ----
        self.list = QListWidget(self)
        self.list.itemDoubleClicked.connect(self.open_output)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(bar)
        layout.addWidget(self.list, 1)

        central = QWidget(self)
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")

    # ---- task management -----------------------------------------

    def add_task(self, command: str) -> Task | None:
        command = command.strip()
        if not command:
            return None
        task = Task(command, needs_shell(command), self)
        item = QListWidgetItem()
        self.tasks[task] = item
        self.list.addItem(item)
        task.state_changed.connect(self._update_item)
        task.launch()
        self._update_item(task)
        return task

    def _task_of(self, item: QListWidgetItem) -> Task | None:
        for task, it in self.tasks.items():
            if it is item:
                return task
        return None

    def run_command(self) -> None:
        cmd = self.cmd_edit.text()
        if not cmd.strip():
            return
        self.cmd_edit.push(cmd)
        self.cmd_edit.clear()
        self.add_task(cmd)

    def stop_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        task = self._task_of(item)
        if task and task.state == Task.RUNNING:
            task.stop()

    def clear_finished(self) -> None:
        for task, item in list(self.tasks.items()):
            if task.state != Task.RUNNING:
                win = self.output_windows.pop(task, None)
                if win is not None:
                    win.close()
                self.list.takeItem(self.list.row(item))
                del self.tasks[task]
        self._refresh_status()

    def open_output(self, item: QListWidgetItem) -> None:
        task = self._task_of(item)
        if task is None:
            return
        win = self.output_windows.get(task)
        if win is None:
            win = OutputWindow(task)
            win.closed.connect(lambda t=task: self.output_windows.pop(t, None))
            win.restart_requested.connect(self._restart_task)
            self.output_windows[task] = win
        win.show()
        win.raise_()
        win.activateWindow()

    def _restart_task(self, task: Task) -> None:
        task.stop()
        self.add_task(task.command)

    # ---- list item display ---------------------------------------

    def _update_item(self, task: Task) -> None:
        item = self.tasks.get(task)
        if item is None:
            return
        mark = {
            Task.RUNNING: "●", Task.FINISHED: "✓",
            Task.FAILED: "✗", Task.KILLED: "✗", Task.ERROR: "!",
        }[task.state]
        color = {
            Task.RUNNING: "#2e7d32", Task.FINISHED: "#757575",
            Task.FAILED: "#c62828", Task.KILLED: "#ef6c00", Task.ERROR: "#c62828",
        }[task.state]
        ts = time.strftime("%H:%M:%S", time.localtime(task.started_at))
        pid = f"  PID {task.pid}" if task.pid else ""
        extra = f"  exit={task.exit_code}" if task.finished_at is not None else ""
        item.setText(f"[{mark}] {ts}  {short_label(task.command)}{pid}{extra}")
        item.setForeground(QColor(color))
        item.setToolTip(f"{task.command}\nstate: {task.state_text}\npid: {task.pid}")
        self._refresh_status()

    def _refresh_status(self) -> None:
        running = sum(1 for t in self.tasks if t.state == Task.RUNNING)
        finished = sum(1 for t in self.tasks if t.state == Task.FINISHED)
        failed = sum(1 for t in self.tasks if t.state in (Task.FAILED, Task.KILLED, Task.ERROR))
        self.statusBar().showMessage(
            f"Running: {running}   Finished: {finished}   Failed: {failed}"
        )

    # ---- context menu ---------------------------------------------

    def _show_context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        task = self._task_of(item)
        if task is None:
            return
        menu = QMenu(self)
        menu.addAction("Open output", lambda: self.open_output(item))
        menu.addSeparator()
        stop_act = menu.addAction("Stop")
        stop_act.setEnabled(task.state == Task.RUNNING)
        stop_act.triggered.connect(task.stop)
        restart_act = menu.addAction("Restart")
        restart_act.setEnabled(task.finished_at is not None)
        restart_act.triggered.connect(lambda: self._restart_task(task))
        menu.addSeparator()
        menu.addAction("Copy command", lambda: QApplication.clipboard().setText(task.command))
        rm_act = menu.addAction("Remove from list")
        rm_act.setEnabled(task.state != Task.RUNNING)
        rm_act.triggered.connect(lambda: self._remove_task(task))
        menu.exec(self.list.mapToGlobal(pos))

    def _remove_task(self, task: Task) -> None:
        item = self.tasks.pop(task, None)
        if item is None:
            return
        self.list.takeItem(self.list.row(item))
        win = self.output_windows.pop(task, None)
        if win is not None:
            win.close()
        self._refresh_status()

    # ---- drag & drop ----------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        paths = [p for p in paths if p]
        if not paths:
            return
        event.acceptProposedAction()
        started = 0
        for path in paths:
            cmd = runnable_command(path)
            if cmd:
                self.add_task(cmd)
                started += 1
            else:
                self.cmd_edit.setText(path)
                self.statusBar().showMessage(
                    f"No runnable form for '{path}' — path placed in the command box", 6000
                )
        if started:
            self.statusBar().showMessage(f"Started {started} task(s)", 4000)

    # ---- shutdown ---------------------------------------------------

    def closeEvent(self, event) -> None:
        running = [t for t in self.tasks if t.state == Task.RUNNING]
        if running:
            answer = QMessageBox.question(
                self,
                "BG Runner",
                f"Terminate {len(running)} running task(s) before quitting?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.Yes:
                for task in running:
                    task.stop()
        for win in list(self.output_windows.values()):
            win.close()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("BG Runner")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
