"""A small stand-in for PySide6, so the window code can be exercised without Qt installed.

This is NOT Qt. It lets the UI modules be imported and driven (buttons "clicked", text
typed, dialogs answered) so mistakes in DoomDeck's own code (misspelt attributes, wrong
argument counts, bad logic) show up in the unit tests. It cannot tell whether a real
widget looks right or whether a real Qt call is spelt correctly: for that, run the program.

How it works:
  * every unknown attribute of a widget is a permissive object that records `.connect(f)`
    handlers and calls them on `.emit(...)`; calling it returns another such object;
  * the widgets whose values the program reads back (line edits, combo boxes, check
    boxes, spin boxes, lists, trees, labels, text boxes) are small real implementations;
  * dialogs and message boxes answer with whatever `CANNED` says; threads run at once.

Usage:  from tests import qtstub; qtstub.install()  before importing doomdeck.ui_*.
"""

from __future__ import annotations

import inspect
import sys
import types

CANNED: dict = {}  # name -> value (or callable) the next dialog call should answer with
LOG: list = []  # (kind, title, text) of every message box shown


def _call_slot(function, args) -> None:
    """Call a connected slot the way Qt does: extra signal arguments are dropped."""
    try:
        params = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        function(*args)
        return
    if any(p.kind == p.VAR_POSITIONAL for p in params):
        function(*args)
        return
    count = sum(1 for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
    function(*args[:count])


class Any:
    """Accepts any attribute, call or signal operation."""

    def __init__(self, *args, owner=None, **kwargs):
        self.__dict__["_slots"] = []
        self.__dict__["_children"] = {}
        self.__dict__["_owner"] = owner

    def connect(self, function, *_a):
        self._slots.append(function)

    def disconnect(self, *_a):
        self._slots.clear()

    def emit(self, *args):
        owner = self.__dict__.get("_owner")
        if owner is not None and owner.__dict__.get("_blocked"):
            return
        for function in list(self._slots):
            _call_slot(function, args)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        children = self.__dict__.setdefault("_children", {})
        if name not in children:
            children[name] = Any()
        return children[name]

    def __call__(self, *args, **kwargs):
        return self.__getattr__("_call_result")

    def __or__(self, other):
        return self

    __ror__ = __or__

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True

    def __str__(self):
        return "<stub>"


class Meta(type):
    """Class-level attributes that do not exist (enums, statics) are permissive too."""

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        cache = cls.__dict__.get("_class_any")
        if cache is None:
            cache = {}
            type.__setattr__(cls, "_class_any", cache)
        if name not in cache:
            cache[name] = Any()
        return cache[name]


class Signal:
    """class-level `Signal(int, str)`: every instance gets its own connectable signal."""

    def __init__(self, *types):
        self.name = ""

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj.__dict__.setdefault("_sig_" + self.name, Any(owner=obj))


class Obj(metaclass=Meta):
    """Base of every stub class: unknown attributes behave like `Any`."""

    def __init__(self, *args, **kwargs):
        self.__dict__["_children"] = {}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        children = self.__dict__.setdefault("_children", {})
        if name not in children:
            children[name] = Any(owner=self)
        return children[name]

    def __bool__(self):
        return True

    def blockSignals(self, flag):
        previous = bool(self.__dict__.get("_blocked"))
        self.__dict__["_blocked"] = bool(flag)
        return previous


# ----------------------------------------------------------------------------- widgets


class QWidget(Obj):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._enabled = True
        self._visible = True
        self._tip = ""
        self._name = ""
        self._hidden_flag = False

    def setEnabled(self, value):
        self._enabled = bool(value)

    def isEnabled(self):
        return self._enabled

    def setVisible(self, value):
        self._visible = bool(value)

    def isVisible(self):
        return self._visible

    def setToolTip(self, text):
        self._tip = text

    def toolTip(self):
        return self._tip

    def setObjectName(self, name):
        self._name = name

    def objectName(self):
        return self._name


class QObject(Obj):
    pass


class QDialog(QWidget):
    class DialogCode:
        Accepted = "Accepted"
        Rejected = "Rejected"

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._accepted = False

    def exec(self):
        value = CANNED.get("dialog_exec", QDialog.DialogCode.Rejected)
        return value() if callable(value) else value

    def accept(self):
        self._accepted = True

    def reject(self):
        self._accepted = False


class QMainWindow(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._status = QStatusBar()

    def statusBar(self):
        return self._status

    def setStatusBar(self, bar):
        self._status = bar

    def close(self):
        self.__dict__["_closed"] = True
        return True


class QStatusBar(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self.last = ""

    def showMessage(self, text, timeout=0):
        self.last = text


class QLabel(QWidget):
    def __init__(self, text="", *a, **k):
        super().__init__()
        self._text = text

    def setText(self, text):
        self._text = text

    def text(self):
        return self._text


class QLineEdit(QWidget):
    def __init__(self, text="", *a, **k):
        super().__init__()
        self._text = text if isinstance(text, str) else ""

    def setText(self, text):
        text = str(text)
        changed = text != self._text
        self._text = text
        if changed:
            self.textChanged.emit(text)

    def text(self):
        return self._text

    def selectAll(self):
        pass

    def type_text(self, text):
        """Test helper: what typing does (textEdited and textChanged both fire)."""
        self._text = text
        self.textEdited.emit(text)
        self.textChanged.emit(text)


class QPlainTextEdit(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._text = ""

    def setPlainText(self, text):
        self._text = text

    def toPlainText(self):
        return self._text

    def verticalScrollBar(self):
        return _Bar()


class _Bar:
    def value(self):
        return 0

    def maximum(self):
        return 0

    def setValue(self, v):
        pass


class QCheckBox(QWidget):
    def __init__(self, text="", *a, **k):
        super().__init__()
        self._checked = False
        self._text = text

    def setChecked(self, value):
        value = bool(value)
        changed = value != self._checked
        self._checked = value
        if changed:
            self.toggled.emit(value)

    def isChecked(self):
        return self._checked

    def click(self):
        self.setChecked(not self._checked)
        self.clicked.emit(self._checked)


class QPushButton(QWidget):
    def __init__(self, text="", *a, **k):
        super().__init__()
        self._text = text
        self._checked = False

    def setText(self, text):
        self._text = text

    def text(self):
        return self._text

    def setCheckable(self, value):
        pass

    def isChecked(self):
        return self._checked

    def setChecked(self, value):
        value = bool(value)
        if value != self._checked:
            self._checked = value
            self.toggled.emit(value)

    def click(self):
        if self._enabled:
            self.clicked.emit(False)


class QSpinBox(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._value, self._min, self._max = 0, 0, 99

    def setRange(self, low, high):
        self._min, self._max = low, high
        self._value = max(low, min(high, self._value))

    def setMinimum(self, low):
        self._min = low

    def setMaximum(self, high):
        self._max = high

    def setValue(self, value):
        value = max(self._min, min(self._max, int(value)))
        changed = value != self._value
        self._value = value
        if changed:
            self.valueChanged.emit(value)

    def value(self):
        return self._value


class QComboBox(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._items: list[list] = []  # [text, data]
        self._index = -1
        self._edit = ""
        self._editable = False

    def addItem(self, text, data=None):
        self._items.append([text, data])
        if self._index < 0:
            self._index = 0
            self.currentIndexChanged.emit(0)

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def clear(self):
        self._items, self._index, self._edit = [], -1, ""

    def count(self):
        return len(self._items)

    def itemText(self, i):
        return self._items[i][0]

    def itemData(self, i, role=None):
        return self._items[i][1] if 0 <= i < len(self._items) else None

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, i):
        i = i if 0 <= i < len(self._items) else -1
        changed = i != self._index
        self._index = i
        if changed:
            self.currentIndexChanged.emit(i)

    def currentText(self):
        if self._editable and self._edit:
            return self._edit
        return self._items[self._index][0] if self._index >= 0 else ""

    def currentData(self, role=None):
        return self._items[self._index][1] if self._index >= 0 else None

    def findText(self, text, *a):
        for i, (t, _d) in enumerate(self._items):
            if t == text:
                return i
        return -1

    def findData(self, data, *a):
        for i, (_t, d) in enumerate(self._items):
            if d == data:
                return i
        return -1

    def setEditable(self, value):
        self._editable = bool(value)

    def setEditText(self, text):
        self._edit = text

    def lineEdit(self):
        return Any()


# ----------------------------------------------------------------------------- lists


class QListWidgetItem(Obj):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._text = next((a for a in args if isinstance(a, str)), "")
        self._data = {}
        self._tip = ""
        self._check = 0
        self._flags = 0

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def data(self, role):
        return self._data.get(role)

    def setData(self, role, value):
        self._data[role] = value

    def setToolTip(self, tip):
        self._tip = tip

    def toolTip(self):
        return self._tip

    def setCheckState(self, state):
        self._check = state

    def checkState(self):
        return self._check

    def flags(self):
        return self._flags

    def setFlags(self, flags):
        self._flags = flags

    def setIcon(self, icon):
        pass


class QListWidget(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._items: list[QListWidgetItem] = []
        self._row = -1

    def addItem(self, item):
        if isinstance(item, str):
            item = QListWidgetItem(item)
        self._items.append(item)

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def insertItem(self, row, item):
        self._items.insert(row, item)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def count(self):
        return len(self._items)

    def clear(self):
        self._items, self._row = [], -1

    def takeItem(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def currentRow(self):
        return self._row

    def setCurrentRow(self, row):
        row = row if 0 <= row < len(self._items) else -1
        old = self.currentItem()
        self._row = row
        new = self.currentItem()
        if new is not old:
            self.currentItemChanged.emit(new, old)
            self.currentRowChanged.emit(row)

    def currentItem(self):
        return self._items[self._row] if 0 <= self._row < len(self._items) else None

    def setCurrentItem(self, item):
        row = self._items.index(item) if item in self._items else -1
        self.setCurrentRow(row)

    def selectedItems(self):
        item = self.currentItem()
        return [item] if item is not None else []

    def row(self, item):
        return self._items.index(item)


class QTreeWidgetItem(Obj):
    def __init__(self, texts=None, *a, **k):
        super().__init__()
        self._texts = list(texts) if isinstance(texts, (list, tuple)) else [""]
        self._data = {}
        self._tips = {}
        self._children: list[QTreeWidgetItem] = []
        self._parent = None
        self._hidden = False
        self._check = {}
        self._flags = 0
        self._expanded = False

    def text(self, col=0):
        return self._texts[col] if col < len(self._texts) else ""

    def setText(self, col, text):
        while len(self._texts) <= col:
            self._texts.append("")
        self._texts[col] = text

    def data(self, col, role):
        return self._data.get((col, role))

    def setData(self, col, role, value):
        self._data[(col, role)] = value

    def setToolTip(self, col, tip):
        self._tips[col] = tip

    def toolTip(self, col=0):
        return self._tips.get(col, "")

    def addChild(self, child):
        child._parent = self
        self._children.append(child)

    def childCount(self):
        return len(self._children)

    def child(self, i):
        return self._children[i]

    def parent(self):
        return self._parent

    def setHidden(self, value):
        self._hidden = bool(value)

    def isHidden(self):
        return self._hidden

    def setExpanded(self, value):
        self._expanded = bool(value)

    def setCheckState(self, col, state):
        self._check[col] = state

    def checkState(self, col):
        return self._check.get(col, 0)

    def flags(self):
        return self._flags

    def setFlags(self, flags):
        self._flags = flags


class QTreeWidget(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._top: list[QTreeWidgetItem] = []
        self._current = None

    def invisibleRootItem(self):
        root = QTreeWidgetItem([""])
        root._children = self._top
        return root

    def clear(self):
        self._top, self._current = [], None

    def addTopLevelItem(self, item):
        self._top.append(item)

    def topLevelItemCount(self):
        return len(self._top)

    def topLevelItem(self, i):
        return self._top[i]

    def indexOfTopLevelItem(self, item):
        return self._top.index(item)

    def currentItem(self):
        return self._current

    def setCurrentItem(self, item):
        old, self._current = self._current, item
        if item is not old:
            self.currentItemChanged.emit(item, old)

    def itemAt(self, pos):
        return self._current

    def header(self):
        return Any()


class FakeMessageBox(QWidget):
    """QMessageBox whose static questions answer from CANNED and are logged."""

    class StandardButton:
        Yes = "Yes"
        No = "No"
        Ok = "Ok"
        Cancel = "Cancel"
        Discard = "Discard"
        Save = "Save"

    @staticmethod
    def _answer(kind, title, text, default):
        LOG.append((kind, str(title), str(text)))
        value = CANNED.get(kind, default)
        return value() if callable(value) else value

    @staticmethod
    def question(parent, title, text, *a, **k):
        return FakeMessageBox._answer("question", title, text, "Yes")

    @staticmethod
    def information(parent, title, text, *a, **k):
        return FakeMessageBox._answer("information", title, text, "Ok")

    @staticmethod
    def warning(parent, title, text, *a, **k):
        return FakeMessageBox._answer("warning", title, text, "Ok")

    @staticmethod
    def critical(parent, title, text, *a, **k):
        return FakeMessageBox._answer("critical", title, text, "Ok")


class FakeFileDialog(QWidget):
    @staticmethod
    def _get(name, default):
        value = CANNED.get(name, default)
        return value() if callable(value) else value

    @staticmethod
    def getOpenFileName(*a, **k):
        return FakeFileDialog._get("open_file", ("", ""))

    @staticmethod
    def getOpenFileNames(*a, **k):
        return FakeFileDialog._get("open_files", ([], ""))

    @staticmethod
    def getSaveFileName(*a, **k):
        return FakeFileDialog._get("save_file", ("", ""))

    @staticmethod
    def getExistingDirectory(*a, **k):
        return FakeFileDialog._get("directory", "")


class FakeInputDialog(QWidget):
    @staticmethod
    def getText(parent, title, label, *a, **k):
        value = CANNED.get("text", ("", False))
        return value() if callable(value) else value

    @staticmethod
    def getItem(parent, title, label, items, *a, **k):
        value = CANNED.get("item", (items[0] if items else "", True))
        return value() if callable(value) else value


class FakeProgressDialog(QWidget):
    def __init__(self, *a, **k):
        super().__init__()

    def show(self):
        pass

    def close(self):
        pass


class QThread(QObject):
    """Runs its job immediately on the calling thread, then reports finished."""

    finished = Signal()

    def __init__(self, *a, **k):
        super().__init__()

    def start(self):
        self.run()
        self.finished.emit()


class QTimer(QObject):
    timeout = Signal()

    def start(self, *a):
        pass


class QSize(Obj):
    def __init__(self, w=0, h=0):
        super().__init__()
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class QUrl(Obj):
    OPENED: list = []

    @staticmethod
    def fromLocalFile(path):
        return ("file", path)


class FakeDesktopServices(Obj):
    @staticmethod
    def openUrl(url):
        QUrl.OPENED.append(url)
        return True


# ----------------------------------------------------------------------------- install


def _module(name: str, members: dict) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(members)

    def fallback(attr):  # any other Qt class the program names is a plain widget stub
        if attr.startswith("__"):
            raise AttributeError(attr)
        value = type(attr, (QWidget,), {})
        module.__dict__[attr] = value
        return value

    module.__getattr__ = fallback
    return module


def install() -> None:
    """Put the stub in sys.modules as PySide6 (does nothing if real PySide6 is loaded)."""
    if "PySide6" in sys.modules and not getattr(sys.modules["PySide6"], "IS_STUB", False):
        return
    QtCore = _module("PySide6.QtCore", {
        "QObject": QObject, "QThread": QThread, "QTimer": QTimer, "QSize": QSize, "QUrl": QUrl,
        "Signal": Signal, "Qt": Obj.__class__("Qt", (Obj,), {}),
    })
    QtGui = _module("PySide6.QtGui", {"QDesktopServices": FakeDesktopServices})
    QtWidgets = _module("PySide6.QtWidgets", {
        "QWidget": QWidget, "QDialog": QDialog, "QMainWindow": QMainWindow, "QStatusBar": QStatusBar,
        "QLabel": QLabel, "QLineEdit": QLineEdit, "QPlainTextEdit": QPlainTextEdit,
        "QCheckBox": QCheckBox, "QPushButton": QPushButton, "QSpinBox": QSpinBox,
        "QComboBox": QComboBox, "QListWidget": QListWidget, "QListWidgetItem": QListWidgetItem,
        "QTreeWidget": QTreeWidget, "QTreeWidgetItem": QTreeWidgetItem,
        "QMessageBox": FakeMessageBox, "QFileDialog": FakeFileDialog,
        "QInputDialog": FakeInputDialog, "QProgressDialog": FakeProgressDialog,
    })
    package = types.ModuleType("PySide6")
    package.IS_STUB = True
    package.QtCore, package.QtGui, package.QtWidgets = QtCore, QtGui, QtWidgets
    sys.modules.update({
        "PySide6": package, "PySide6.QtCore": QtCore, "PySide6.QtGui": QtGui,
        "PySide6.QtWidgets": QtWidgets,
    })
    # Text widgets keep their text when the program reads it back.
    QtWidgets.QTextEdit = QPlainTextEdit
