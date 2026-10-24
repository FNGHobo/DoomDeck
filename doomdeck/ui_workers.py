"""Running slow work (network requests, downloads) away from the window.

`start` runs a function on a background thread and calls back on the window's thread
when it finishes, so the window never freezes. `run_with_popup` does the same behind a
small progress dialog with a Cancel button.

The function given to either one receives the worker; it can call `worker.report(done,
total)` to show progress and should check `worker.cancelled()` now and then.
"""

from __future__ import annotations

from typing import Callable

from PySide6 import QtCore, QtWidgets

INT_MAX = 2**31 - 1  # Qt signals carry 32-bit ints


class Worker(QtCore.QThread):
    """One background job. Emits finished_ok(result) or failed(message)."""

    progress = QtCore.Signal(int, int)
    finished_ok = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, func: Callable[["Worker"], object], parent=None):
        super().__init__(parent)
        self._func = func
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def report(self, done: int, total: int) -> None:
        self.progress.emit(min(int(done), INT_MAX), min(int(total), INT_MAX))

    def run(self) -> None:
        try:
            result = self._func(self)
        except Exception as error:  # noqa: BLE001 - anything a job raises becomes a message
            self.failed.emit(str(error) or error.__class__.__name__)
            return
        self.finished_ok.emit(result)


def start(owner: QtWidgets.QWidget, func: Callable[[Worker], object],
          on_done: Callable[[object], None],
          on_fail: Callable[[str], None] | None = None,
          on_progress: Callable[[int, int], None] | None = None) -> Worker:
    """Run `func` in the background. `on_done(result)` / `on_fail(message)` run on this thread.

    A job that was cancelled stays quiet: neither callback is called with its error.
    """
    worker = Worker(func, owner)
    alive = getattr(owner, "_workers", None)
    if alive is None:
        alive = set()
        owner._workers = alive  # keeps the thread objects referenced until they finish
    alive.add(worker)

    def failed(message: str) -> None:
        if worker.cancelled():
            return
        if on_fail is not None:
            on_fail(message)
        else:
            QtWidgets.QMessageBox.warning(owner, "DoomDeck", message)

    worker.finished_ok.connect(on_done)
    worker.failed.connect(failed)
    if on_progress is not None:
        worker.progress.connect(on_progress)

    def cleanup() -> None:
        alive.discard(worker)
        worker.deleteLater()

    worker.finished.connect(cleanup)
    worker.start()
    return worker


def run_with_popup(owner: QtWidgets.QWidget, label: str, func: Callable[[Worker], object],
                   on_done: Callable[[object], None],
                   on_fail: Callable[[str], None] | None = None) -> Worker:
    """Like `start`, with a modal progress dialog that can cancel the job."""
    popup = QtWidgets.QProgressDialog(label, "Cancel", 0, 0, owner)
    popup.setWindowTitle("DoomDeck")
    popup.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
    popup.setMinimumDuration(0)
    popup.setAutoClose(False)
    popup.setAutoReset(False)

    def progress(done: int, total: int) -> None:
        if total > 0:
            popup.setMaximum(total)
            popup.setValue(min(done, total))

    def close_popup() -> None:
        try:
            popup.canceled.disconnect()  # closing the dialog would otherwise count as Cancel
        except (RuntimeError, TypeError):
            pass
        popup.close()
        popup.deleteLater()

    def done(result: object) -> None:
        close_popup()
        on_done(result)

    def failed(message: str) -> None:
        close_popup()
        if on_fail is not None:
            on_fail(message)
        else:
            QtWidgets.QMessageBox.warning(owner, "DoomDeck", message)

    worker = start(owner, func, done, failed, progress)
    popup.canceled.connect(worker.cancel)
    popup.show()
    return worker
