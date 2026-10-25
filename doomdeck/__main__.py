"""Entry point: `python -m doomdeck` opens the window.

`python -m doomdeck --print-command "Preset name"` prints the command for a preset
and exits; `--launch "Preset name"` starts a preset without a window, `--list` prints the
preset names, `--check-updates` asks the download sources whether any library project has
a newer version (add `--json` for machine-readable output). None of these import Qt, so
they also work without PySide6.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__
from . import config as config_io
from . import launch, osinfo, updates


def print_command(name: str) -> int:
    """Print the launch command for a preset (a debugging / scripting aid)."""
    config = config_io.load()
    preset = config.preset_named(name)
    if preset is None:
        names = ", ".join(p.name for p in config.presets) or "(none yet)"
        print(f"No preset named {name!r}. Presets: {names}", file=sys.stderr)
        return 1
    for problem in launch.check(config, preset):
        print(f"{problem.level}: {problem.text}", file=sys.stderr)
    print(launch.format_command(launch.build_command(config, preset)))
    return 0


def launch_preset(name: str) -> int:
    """Start a preset without opening the window (used by launcher-menu shortcuts).

    The game is started in its own session so it outlives this process; the play
    count and last-played date are recorded now, since nobody is left to time it.
    Problems that stop a launch are printed and give exit code 1.
    """
    config = config_io.load()
    preset = config.preset_named(name)
    if preset is None:
        names = ", ".join(p.name for p in config.presets) or "(none yet)"
        print(f"No preset named {name!r}. Presets: {names}", file=sys.stderr)
        return 1
    problems = launch.check(config, preset)
    for problem in problems:
        print(f"{problem.level}: {problem.text}", file=sys.stderr)
    if any(p.level == "error" for p in problems):
        return 1
    try:
        launch.launch(config, preset, config_io.APP_DIR / "logs", detach=True)
    except (OSError, ValueError) as error:
        print(f"Launch failed: {error}", file=sys.stderr)
        return 1
    now = time.time()
    launch.record_session(preset, now, now)
    config_io.save(config)
    return 0


def check_updates(as_json: bool) -> int:
    """Ask each project's source for its newest version and print what changed.

    Exit code 0 when everything is current, 10 when at least one update exists, so a
    script or desktop notifier can act on the result without parsing the text.
    """
    import json

    config = config_io.load()
    results = updates.check_all(config.library_meta)
    if as_json:
        print(json.dumps([
            {"project": r.key, "kind": r.kind, "installed": r.installed, "latest": r.latest,
             "update": r.is_update, "title": r.title, "url": r.url, "error": r.error}
            for r in results
        ], indent=2))
    elif results:
        print(updates.report_text(results))
    else:
        print("No project has an update source yet (right-click a library project to set one).")
    return 10 if any(r.is_update for r in results) else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="doomdeck", description="A Doom launcher.")
    parser.add_argument("--version", action="version", version=f"DoomDeck {__version__}")
    parser.add_argument("--check-updates", action="store_true",
                        help="check downloaded wads/mods for newer versions and exit")
    parser.add_argument("--json", action="store_true", help="with --check-updates: print JSON")
    parser.add_argument("--print-command", metavar="PRESET", help="print a preset's command and exit")
    parser.add_argument("--launch", metavar="PRESET", help="start a preset without opening the window")
    parser.add_argument("--list", action="store_true", help="list preset names and exit")
    args = parser.parse_args()
    if args.print_command:
        return print_command(args.print_command)
    if args.launch:
        return launch_preset(args.launch)
    if args.check_updates:
        return check_updates(args.json)
    if args.list:
        for preset in config_io.load().presets:
            print(preset.name)
        return 0

    # Qt's Wayland text-input plugin prints a harmless warning every time focus moves
    # between fields. Hide just that category (keep any rules the user already set).
    # This has to happen before Qt is imported. Only Linux has Wayland.
    if osinfo.current() == osinfo.LINUX:
        existing = os.environ.get("QT_LOGGING_RULES", "")
        os.environ["QT_LOGGING_RULES"] = ";".join(
            filter(None, [existing, "qt.qpa.wayland.textinput=false"])
        )

    try:
        from PySide6 import QtGui, QtWidgets
    except ImportError:
        hints = {
            osinfo.LINUX: "On Arch: sudo pacman -S pyside6",
            osinfo.WINDOWS: "Or: py -3 -m pip install PySide6-Essentials",
            osinfo.MACOS: "Or: python3 -m pip install PySide6-Essentials",
        }
        print(
            f"PySide6 is not installed. {hints[osinfo.current()]}\n"
            f"Or start DoomDeck with {osinfo.launcher_script()}, which sets up a private copy for you.",
            file=sys.stderr,
        )
        return 1
    from . import ui_theme
    from .ui_main import MainWindow

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("DoomDeck")
    # Matches doomdeck.desktop, so the desktop shows the launcher's icon for the window.
    app.setDesktopFileName("doomdeck")
    icon_file = config_io.find_icon()
    if icon_file is not None:
        app.setWindowIcon(QtGui.QIcon(str(icon_file)))
    ui_theme.apply(app)
    window = MainWindow(config_io.load(), config_io.default_path())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
