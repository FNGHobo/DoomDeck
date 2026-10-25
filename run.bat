@echo off
rem run.bat - start DoomDeck on Windows.
rem
rem Uses a Python that already has PySide6 if there is one. Otherwise it creates a
rem private virtual environment in .\.venv next to this file and installs PySide6 into
rem it, so nothing is installed system-wide and nothing is written outside this folder.
rem
rem With no arguments the window opens without a console box. With arguments
rem (--list, --launch "Preset", --check-updates ...) they are passed on and the
rem output is shown in this console.
setlocal
cd /d "%~dp0"

set "PY="
set "PYW="
where py >nul 2>nul && set "PY=py -3" && set "PYW=pyw -3"
if not defined PY where python >nul 2>nul && set "PY=python" && set "PYW=pythonw"
if not defined PY goto no_python

if exist ".venv\Scripts\pythonw.exe" goto have_venv
%PY% -c "import PySide6" >nul 2>nul
if not errorlevel 1 goto system_python

echo PySide6 not found - setting up .\.venv ^(one time, needs internet^)...
%PY% -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --quiet PySide6-Essentials
if errorlevel 1 goto failed

:have_venv
if "%~1"=="" (
  start "" ".venv\Scripts\pythonw.exe" -m doomdeck
  exit /b 0
)
".venv\Scripts\python.exe" -m doomdeck %*
exit /b %errorlevel%

:system_python
if "%~1"=="" (
  start "" %PYW% -m doomdeck
  exit /b 0
)
%PY% -m doomdeck %*
exit /b %errorlevel%

:no_python
echo Python 3 was not found. Install it from https://www.python.org/downloads/
echo ^(tick "Add python.exe to PATH"^) and run this file again.
pause
exit /b 1

:failed
echo Could not set up DoomDeck. See the messages above.
pause
exit /b 1
