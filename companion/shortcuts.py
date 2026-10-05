"""Windows shortcuts for starting the companion: on the desktop, and in the Startup folder."""
import os
import subprocess
import sys
from pathlib import Path

NAME = "WoWZA.lnk"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _launch_command():
    """(target, arguments, working folder) that start this app without a console window."""
    if getattr(sys, "frozen", False):  # PyInstaller exe
        exe = Path(sys.executable)
        return exe, "", exe.parent
    here = Path(__file__).resolve().parent
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return pythonw, f'"{here / "app.py"}"', here


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def _create(lnk_expr):
    target, args, workdir = _launch_command()
    script = (f"$p = {lnk_expr}; $s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); "
              f"$s.TargetPath = {_ps_quote(target)}; $s.Arguments = {_ps_quote(args)}; "
              f"$s.WorkingDirectory = {_ps_quote(workdir)}; $s.Description = 'WoWZA companion'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def create_desktop_shortcut():
    # The real Desktop folder (it may be redirected into OneDrive).
    _create(f"Join-Path ([Environment]::GetFolderPath('Desktop')) {_ps_quote(NAME)}")


def _desktop_dir():
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    return Path(buf.value) if buf.value else None


def migrate_old_shortcuts():
    """Swap shortcuts from before the rename ("Claude Advisor.lnk") for WoWZA ones, where they existed."""
    old = "Claude Advisor.lnk"
    if (STARTUP_DIR / old).exists():
        (STARTUP_DIR / old).unlink()
        set_startup(True)
    desktop = _desktop_dir()
    if desktop and (desktop / old).exists():
        (desktop / old).unlink()
        create_desktop_shortcut()


def startup_enabled():
    return (STARTUP_DIR / NAME).exists()


def set_startup(enabled):
    if enabled:
        STARTUP_DIR.mkdir(parents=True, exist_ok=True)
        _create(_ps_quote(STARTUP_DIR / NAME))
    else:
        (STARTUP_DIR / NAME).unlink(missing_ok=True)
