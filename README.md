# DoomDeck

A Doom launcher for Linux, Windows and macOS (Python + PySide6) built around a sorted `wads/` folder
(category folder → project folder → files). It launches any ZDoom-family engine
(UZDoom, GZDoom...) with per-preset saves, screenshots, demos and engine configs.

Highlights: presets with a picture, tags and load order (auto-sort, find missing
files, drag and drop), a Mapsets tab for multi-file map sets, a Library tree with
readme previews, Continue-last-save, multiplayer settings, desktop shortcuts per
preset, import from a pasted command line, and daily config backups. Also:

* **Get WADs** - search the Doomworld /idgames archive and install straight into
  your sorted wads folder.
* **Update tracking** - knows which downloads have a newer version (also from the
  command line, for a desktop notifier).
* **Maps tab** - per-map "finished" ticks and play counts, and "play this map".
* **Compatibility warnings** - before launch: map style vs IWAD, ZDoom-only mods on
  other engines, duplicate maps, a start map that does not exist.
* **Engine management** - several versions side by side, one-click install of the
  newest UZDoom release.
* **Server check** - ask a Zandronum or Odamex server which files it runs, see which
  ones you are missing, and download them (checksum-verified) before joining.
* **Shareable presets** - exports carry a SHA-256 of every file, so a friend (or you,
  for multiplayer) can check that both sides have exactly the same files.

**This repository contains only the launcher.** It does not include Doom, any
IWAD or WAD/PK3 files, or an engine; you bring your own. Not affiliated with id
Software or the UZDoom/GZDoom projects.

## Requirements and dependencies

DoomDeck was developed and tested on Arch Linux with Hyprland (Wayland). **Windows
and macOS support was added later and has been tested only with automated tests and
mocks, not yet by hand on those systems** - see "Windows" and "macOS" below, and
please report anything that does not work.

| What | Needed? | Notes |
| --- | --- | --- |
| **Python 3.10 or newer** | required | Developed on 3.13. Everything except the window uses only the standard library. |
| **PySide6 (Qt 6)** | required | The only third-party Python package. `PySide6-Essentials` is enough. |
| **A ZDoom-family engine** | required | UZDoom, GZDoom or similar. Not included; set its path in Settings. |
| **An IWAD** (`DOOM2.WAD`, `DOOM.WAD`...) | required | Not included; bring your own, in the IWAD folder you set in Settings. |
| `qt6-wayland` | Wayland only | Lets Qt open a native Wayland window. It is normally present on a Wayland desktop. |
| `qt6-imageformats` | optional | Only to use `.webp` images as preset pictures. PNG, JPG and BMP work without it. |
| `xdg-utils` | optional | The "Open folder" buttons use the desktop's default file manager. |
| `gamemode`, `mangohud`... | optional | Only if you set one as a launch wrapper. |
| `python3-venv` | optional | Only on distros that split it out (e.g. Debian/Ubuntu), if `run.sh` has to create its own environment. |

### Installing PySide6

* **Arch Linux:** `sudo pacman -S python pyside6` (add `qt6-wayland` if the window
  fails to open on Wayland)
* **Anything else:** nothing to do. `./run.sh` notices that PySide6 is missing,
  creates a private virtual environment in `./.venv` and runs
  `pip install PySide6-Essentials` there (one time, needs internet). Nothing is
  installed system-wide. You can also do the same by hand:

      python3 -m venv .venv
      .venv/bin/pip install PySide6-Essentials
      .venv/bin/python -m doomdeck

### Troubleshooting

* **"PySide6 is not installed"** - use `./run.sh` (it sets up `./.venv`), or install
  PySide6 as above.
* **Qt "could not load the platform plugin" / no window on Wayland** - install
  `qt6-wayland`, or try `QT_QPA_PLATFORM=xcb ./run.sh`.
* **The window opens but nothing launches** - check File → Settings → Engines
  points at the engine program (it must be executable); problems are also marked
  next to the preset and listed under the load order.

### Windows

* Install Python 3.10+ from python.org (tick "Add python.exe to PATH"), then
  double-click **`run.bat`**. It creates `.venv`, installs PySide6 once, and starts the
  program without a console window.
* Defaults: Doom files in `%USERPROFILE%\Games\Doom` (change them in Settings); DoomDeck
  keeps its settings beside the program in the portable layout, or in `%APPDATA%\DoomDeck`
  when installed as a package. Engines must be `.exe` files.
* **Install latest...** downloads the 64-bit Windows build of UZDoom (an ARM64 PC
  uses the x64 build through Windows' emulation).
* Preset shortcuts are `.lnk` files in the Start menu (made with PowerShell).

### macOS

* Install Python 3.10+ (python.org or `brew install python`), then run
  `chmod +x run.command` once and double-click **`run.command`** (or run it in Terminal).
  It creates `.venv` and installs PySide6 on first use.
* Defaults: Doom files in `~/Games/Doom`; settings in `~/Library/Application
  Support/DoomDeck` when installed. An engine can be a `.app` (e.g. `UZDoom.app`);
  DoomDeck runs the program inside it.
* **Install latest...** handles `.dmg` and `.zip` downloads. UZDoom currently publishes
  an Apple-silicon (ARM64) macOS build only; on an Intel Mac pick a different engine
  (e.g. a GZDoom build) and set its path in Settings.
* Preset shortcuts are small `.app` files in `~/Applications/DoomDeck`. Unsigned apps
  may need right-click → Open the first time.

## Wads folder layout

DoomDeck expects your files sorted as `wads/<category>/<project>/<files>`, for
example:

    wads/
      maps/Eviternity/Eviternity.wad
      gameplay-mods/Brutal Doom/brutal.pk3
      visuals/SmoothDoom/SmoothDoom.pk3
      audio/…

The category folders are yours to name; `maps` (changeable in Settings) is the one
the Mapsets tab lists. Folders holding files directly still show up, under
"(no category)". Extra folders from anywhere on disk can be added in
Settings → Library.

## Install

    git clone https://github.com/FNGHobo/DoomDeck.git
    cd DoomDeck
    ./run.sh

Then open **File → Settings** and set your engine, wads folder and IWAD folder (see
"First-time setup" below). Your settings are saved to `doomdeck.json` beside the
program; that file, your logs, saves and preset pictures are git-ignored.

## Run

    ./run.sh

Uses the system PySide6 if present (Arch: `sudo pacman -S pyside6`), otherwise
creates `./.venv` beside the program and installs it there. The config
(`doomdeck.json`) and engine logs (`logs/`) also live beside the program.

## Launcher entry (app menu / dock)

`doomdeck.desktop.in` (with the icon `doomdeck.png`) is a template for a launcher
entry. Fill in this folder's location and install it for your user with:

    mkdir -p ~/.local/share/applications
    sed "s|@INSTALL_DIR@|$PWD|g" doomdeck.desktop.in > ~/.local/share/applications/doomdeck.desktop

It uses absolute paths, so run that again after moving the folder.

## First-time setup (File → Settings)

* **Engines** - add any number of engines (name, program, extra args, which are
  added to every launch with that engine). The three switches (config / save folder /
  screenshot folder) default to the ZDoom family and can be changed or cleared per
  engine. An UZDoom entry (`~/Games/doom/UZDoom/uzdoom`) is pre-filled; point it at
  your own install. **Version** and **Update source** (a GitHub `owner/repository`,
  `UZDoom/UZDoom` for UZDoom) enable **Check for update** and **Install latest...**,
  which downloads the newest Linux release into DoomDeck's own `engines/<version>/`
  folder as a *new* engine entry (the old one stays, so you can go back) and offers
  to switch your presets to it.
* **Folders** - wads folder, IWAD folder, one data folder (default
  `~/Games/doom/Doomdeck/data`) with the four default sub-folders (Config, Save,
  Demo, Screenshot) and a switch for "own subfolder per preset". With the defaults,
  preset "Foo" saves to `<data>/Save/Foo/`; the example under the settings shows the
  pattern. An optional starter `.ini` is copied into each new preset's config
  folder.
* **Library** - choose which folders the Library tab shows: untick a folder inside
  the wads folder to hide it (nothing is moved or deleted; you can also right-click a
  folder in the Library tab and pick "Hide"), and add extra folders from anywhere on
  disk, which then appear as if they were inside the wads folder. The Mapsets tab
  keeps listing the maps folder even if it is hidden from the Library. This tab
  also names the **folder for map sets** (`maps` by default): the Mapsets tab lists
  it, and downloaded maps are saved there.
* **General** - global wrapper (e.g. `gamemoderun`; a preset can set its own) and
  autoload files that load before everything else. "Close on launch" is the tick box
  next to the Launch button: the game keeps running after DoomDeck quits; the play
  count and last-played date are recorded at launch, but play time cannot be
  measured in this mode.

Versions before 0.3 had a global "Extra arguments" setting. It was a duplicate of
the per-engine one, so it was removed; if you had one, it is added to your engines'
extra arguments automatically the first time 0.3 starts.

## Using it

* **Library tab** - the sorted tree with search, favorites and ratings
  (right-click). Double-click adds to the current preset; "New preset from
  project" builds one, guessing Doom vs Doom II from the map names. Projects with
  several files ask which to use.
* **Mapsets tab** - the map sets (the projects inside the `maps` folder) with a tick
  box on every file, for map sets that need several files. Ticking a project ticks
  all its files; switch the list to "Everything" to also tick resource packs or
  mods from other folders. Files load in the order you ticked them, so tick
  resources first. "New preset from ticked" builds a preset (and guesses the IWAD);
  "Add ticked to preset" appends to the current one. Selecting a row shows how many
  maps it contains.
* **Get WADs tab** - searches the Doomworld /idgames archive (by title, file name,
  author, description or text file; or paste a Doomworld link or file id) and shows
  descriptions. **Download and install** fetches the file from an archive mirror and
  unpacks it into `<wads folder>/<category>/<project>/` (levels go to the map sets
  folder, sounds and music to `audio`, graphics and themes to `visuals`, utilities to
  `utilities`, combos to `total-conversions`, anything else to `needs-to-be-sorted`).
  It asks before replacing a project that is already there and can then make a preset
  from it. Needs an internet connection; nothing is downloaded until you press the
  button.
* **Updates** - projects installed from Get WADs remember where they came from. For
  anything else, right-click it in the Library tab, **Set update source...** and give
  an idgames link or id, or a GitHub link or `owner/repository` (what you have now is
  taken to be current). **Library → Updates** (or Tools → Check library for updates)
  marks projects that have a newer version with a **⬆**; right-click for **Update now**
  (idgames: re-downloads over the same folder; GitHub: opens the releases page),
  **Mark as up to date** or **Forget update source**. From a terminal,
  `./run.sh --check-updates` prints the same report (`--json` for scripts) and exits
  with status 10 when something has an update, which suits a desktop notifier.
* **Maps tab** - lists every map in the preset's files. Tick a map when you have
  finished it; the "Played" column counts launches that started on that map (a normal
  start counts for the preset's Start map). Double-click or **Play this map** starts
  on that map for one launch without changing the preset's own Start map.
* **Setup tab** - engine, IWAD, skill, **config**, start map (list read from the
  WAD/PK3), toggles, resolution, wrapper, extra args, and the load order (tick to
  enable, drag to reorder, drop files from a file manager).
  The **Config** dropdown picks which engine `.ini` the preset uses: the default
  (`<engine>.ini`), any other `.ini` in the preset's config folder, or one from the
  shared config folder (marked "shared"). **New...** creates a config, blank or
  copied from the starter config or an existing one, and switches to it.
  **Import...** copies an existing config into the preset and switches to it: the
  engine's own config (found in `~/.config/uzdoom` and similar, or next to the
  engine), a config from another preset, or any `.ini` you browse to. The original
  is never changed.
* **Saves / Demos / Screenshots** - per-preset; load a save, play a demo, view
  thumbnails, open or delete.
* **Info** - the readme of each project the preset uses: a text file in the project
  folder, otherwise a readme inside its PK3/ZIP (root or one folder deep), otherwise
  a summary of the files (size and map count). **Output** - engine log of the last run.
* **Preset picture** - **Set picture...** on the Setup tab (the file dialog opens in
  the preset's screenshot folder), or **Use as preset picture** on the Screenshots tab
  (also in the right-click menu). A shrunk copy is kept in `pictures/` beside the
  program, so deleting the screenshot does not lose it. The picture shows on the Setup
  tab and as a small icon in the preset list; **Remove picture** deletes the copy.
  Pictures are not included when exporting a preset.
* The bar at the bottom always shows the exact command; **Copy** copies it.
  Ctrl+Return launches. File → Quick play launches files without a preset.

* **Multiplayer** (Setup tab) - Mode: off, host or join. Hosting passes `-host
  <players>` plus the game type (co-op, deathmatch or alt deathmatch); joining passes
  `-join <address>`; an optional port goes to both as `-port`. These are the
  ZDoom-family switches (UZDoom, GZDoom). Nobody else needs DoomDeck: they only need
  the same engine version, IWAD and files in the same load order (the **Copy**
  button gives them the exact command). The host decides skill, map and rules.
  Demos always play single player. For **Zandronum** and **Odamex** only joining is
  offered, and it passes `-connect <address[:port]>` (their `-port` means the local port,
  so a port you set is written into the address instead).
* **Server check** (Multiplayer box, when joining) - **Check server...** asks the
  server over UDP (port 10666 unless you give another) which IWAD and files it runs, in
  load order, with each file's MD5. Each file is matched against your library by name
  and checksum (✔ same, ≠ different version, ✖ missing). DoomDeck then offers to
  download what is missing, always asking first: from the download sites an Odamex server
  advertises, then from the /idgames archive by file name. A download is kept only if its
  checksum equals the server's, and is unpacked into your wads folder. Finally it offers to
  put the server's files, in the server's order, into the profile (replacing its file
  list). The tick box **On Launch, check the server and get its files** does this every
  time you press Launch. Limits: only Zandronum and Odamex servers can be asked (UZDoom
  and GZDoom hosts have no query; use a shared preset for those); files that are on
  neither the site list nor /idgames cannot be fetched (a Zandronum server's website is
  shown for you to open); IWADs are never downloaded; a server may refuse a query
  for about ten seconds after the last one. Odamex servers are asked with the older
  query that current servers still answer.

* **Compatibility warnings** - shown under the load order and in the preset list:
  a Doom II style map list (MAPxx) with a Doom 1 IWAD or the other way round, files
  that both contain the same map, a start map that is in none of the files, and mods
  that use ZDoom-only features (DECORATE, ZScript, GLDEFS, UDMF maps, a PK3...) when the
  engine is a Boom or vanilla port. They are advice; nothing is blocked.
* **Sharing a preset** - **Export...** writes the preset with the size and SHA-256
  of every file and of the IWAD, and the engine version. **Import...** finds each file
  in your library by name, points the preset at your copy, and reports files that are
  missing or that differ from the sender's (a different version of a wad is the usual
  cause of a multiplayer desync). **Preset → Check files against a shared preset...**
  does the comparison without importing. Old exports without checksums still import.
* **Continue** (next to Launch) - loads the preset's newest save and keeps playing;
  greyed out until the preset has a save.
* **Find missing files** (Setup tab, under the load order) - when sorting the wads
  folder moved files, this searches the library for each missing file by name and
  points the preset at the new location. The warning under the load order also tells
  you when two files share a name.
* **Desktop shortcut** (Setup tab) - adds the preset to your application menu (Linux:
  `~/.local/share/applications/doomdeck-<name>.desktop`; Windows: the Start menu;
  macOS: `~/Applications/DoomDeck`) using the preset's picture
  as its icon. It starts the game directly, without opening DoomDeck. Run it again
  after renaming the preset or changing its picture. From a terminal:
  `./run.sh --launch "Preset name"`, and `./run.sh --list` lists the presets.
* **Search** in the Presets tab matches the preset name, its notes and the names of
  its files. Hover a preset for its play stats and files.
* **Backups** - the first save of each day copies `doomdeck.json` into `backups/`
  (the newest 10 are kept), so a deleted preset or a bad edit can be undone by
  copying a backup back over `doomdeck.json`.

* **Auto-sort** (Setup tab) - puts the load order in the usual sequence: audio,
  visuals, utilities, then gameplay mods and total conversions, then maps, judged by
  the first-level folder each file lives in. Files of the same kind keep the order
  you gave them, and it asks before changing anything.
* **Tags** (Setup tab) - labels such as `co-op, slaughter`, separated by commas. The
  Presets tab has a tag filter next to "Favorites only", and search matches tags too.
* **Sorting the preset list** - the box next to the search offers Name, Last played,
  Most played (time), Times launched and Favorites first; the **⇅** button reverses
  whichever one is chosen.
* **New preset from a command line** (Presets tab, **From command...**) - paste an
  existing command (from DoomRunner, a script, a shortcut). The IWAD, `-file` and
  `-deh` files, skill, `+map`/`-warp`, size and the common switches are read; files
  that moved are found again by name; unrecognised switches are kept as extra
  arguments. Config, save and demo switches are dropped, since DoomDeck manages those
  per preset.
* **Drop files on the window** - drop wads, pk3s or whole folders anywhere on the
  DoomDeck window to make a new preset from them (sorted resources, mods, maps).
  Dropping onto the load-order list adds them to the current preset instead.
* **Library and Mapsets info** - selecting a project shows its readme (a text file in
  its folder, or one inside a pk3), or otherwise its files with sizes and map counts.
* **Problem markers** - a preset with a warning shows **⚠** and one that cannot
  launch shows **✖** in the list (hover for the details). **Preset → Check all
  presets...** lists every problem at once.
* **Shortcuts** - Ctrl+N new preset, Ctrl+D duplicate, F2 rename, Ctrl+F search,
  Ctrl+Shift+Return continue the last save, Ctrl+Return launch, Ctrl+Shift+G Get WADs. The **Preset** menu also has Find missing files,
  Auto-sort and Desktop shortcut; the **Tools** menu has Get WADs and the
  update checks.

Not included on purpose: dmflags/compatibility calculators.

## Install as a package (Arch / pip)

`packaging/PKGBUILD` builds an Arch package (`cd packaging && makepkg -si`); it needs
a tagged release on GitHub (`v0.4.0`). Installed that way, DoomDeck keeps its config,
logs and downloaded engines in `~/.config/doomdeck` instead of beside the program
(set `DOOMDECK_HOME` to choose another folder). `pip install .` also works and gives a
`doomdeck` command.

## Tests

No PySide6 and no display are needed. The window code runs against a small stand-in
for Qt (`tests/qtstub.py`), so the tests catch mistakes in DoomDeck's own code but not
how a real window looks:

    python3 -m unittest discover -s tests -t .

GitHub Actions runs them for every push; `packaging/ci.yml` is a version that also runs
them on Windows and macOS (copy it to `.github/workflows/`).
`python3 -m doomdeck --print-command "Preset name"` prints a preset's command.

## About the code

DoomDeck was written with the help of an AI assistant (Claude). It is MIT licensed and
the tests are in the repository.

## License

MIT, see [LICENSE](LICENSE). This covers the launcher code only; Doom, its IWADs and
any WADs or engines you use belong to their respective owners.
