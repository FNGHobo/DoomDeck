# DoomDeck

A Doom launcher for Linux (Python + PySide6) built around a sorted `wads/` folder
(category folder → project folder → files). It launches any ZDoom-family engine
(UZDoom, GZDoom...) with per-preset saves, screenshots, demos and engine configs.

Highlights: presets with a picture, tags and load order (auto-sort, find missing
files, drag and drop), a Mapsets tab for multi-file map sets, a Library tree with
readme previews, Continue-last-save, multiplayer settings, desktop shortcuts per
preset, import from a pasted command line, and daily config backups.

**This repository contains only the launcher.** It does not include Doom, any
IWAD or WAD/PK3 files, or an engine; you bring your own. Not affiliated with id
Software or the UZDoom/GZDoom projects.

## Requirements

* Linux with Python 3.10+ and PySide6 (Arch: `sudo pacman -S pyside6`; `run.sh`
  can also install it into a private `./.venv`)
* A ZDoom-family engine such as UZDoom, plus your own IWAD(s)

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

* **Engines** - add any number of engines (name, program, extra args). The three
  switches (config / save folder / screenshot folder) default to the ZDoom family
  and can be changed or cleared per engine. An UZDoom entry
  (`~/Games/doom/UZDoom/uzdoom`) is pre-filled; point it at your own install.
* **Folders** - wads folder (and the name of the folder inside it that holds map
  sets, `maps` by default), IWAD folder, one data folder (default
  `~/Games/doom/Doomdeck/data`) with the four default sub-folders (Config, Save,
  Demo, Screenshot) and a switch for "own subfolder per preset". With the defaults,
  preset "Foo" saves to `<data>/Save/Foo/`; the example under the settings shows the
  pattern. An optional starter `.ini` is copied into each new preset's config
  folder.
* **Library** - choose which folders the Library tab shows: untick a folder inside
  the wads folder to hide it (nothing is moved or deleted; you can also right-click a
  folder in the Library tab and pick "Hide"), and add extra folders from anywhere on
  disk, which then appear as if they were inside the wads folder. The Mapsets tab
  keeps listing the maps folder even if it is hidden from the Library.
* **General** - global wrapper (e.g. `gamemoderun`), global arguments, autoload
  files that load before everything else, and **Close DoomDeck when the game
  launches** (also a tick box next to the Launch button). The game keeps running
  after DoomDeck quits; the play count and last-played date are recorded at launch,
  but play time cannot be measured in this mode.

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
  Demos always play single player.

* **Continue** (next to Launch) - loads the preset's newest save and keeps playing;
  greyed out until the preset has a save.
* **Find missing files** (Setup tab, under the load order) - when sorting the wads
  folder moved files, this searches the library for each missing file by name and
  points the preset at the new location. The warning under the load order also tells
  you when two files share a name.
* **Desktop shortcut** (Setup tab) - adds the preset to your application menu
  (`~/.local/share/applications/doomdeck-<name>.desktop`) using the preset's picture
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
  Ctrl+Shift+Return continue the last save, Ctrl+Return launch. The **Preset** menu
  also has Find missing files, Auto-sort and Desktop shortcut.

Not included on purpose: dmflags/compatibility calculators.

## Tests

    python3 -m unittest discover -s tests -t .

`python3 -m doomdeck --print-command "Preset name"` prints a preset's command.
