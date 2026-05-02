#!/usr/bin/env python3
"""
SlicerBridge — Universal 3D printing slicer protocol bridge
https://github.com/LukysGaming/SlicerBridge

Build:  pyinstaller --onedir --noconsole --name SlicerBridge main.py

Modes:
  SlicerBridge.exe                 → GUI installer / configurator
  SlicerBridge.exe <protocol://…>  → silent handler (download + open slicer)
  SlicerBridge.exe --register      → copy exe + write registry (via UAC elevation)
  SlicerBridge.exe --uninstall     → remove registry + files  (via UAC elevation)
  SlicerBridge.exe --reset         → delete config (developer helper)
"""

from __future__ import annotations

import ctypes
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import winreg
from datetime import datetime
from typing import Optional


# ── Hide the console window as the very first thing ──────────────────────────
# PyInstaller's onedir bootloader can briefly flash a console before --noconsole
# takes effect. Hiding it here suppresses that flash.

def _hide_console() -> None:
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0
    except Exception:
        pass

_hide_console()


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

VERSION         = "2.0.1"
SCHEMA_VERSION  = 1          # bump when config structure changes (triggers migration)
APP_NAME        = "SlicerBridge"
EXE_NAME        = "SlicerBridge.exe"
INSTALL_DIR_DEF = r"C:\Program Files (x86)\SlicerBridge"
TEMP_PREFIX     = "slicerbridge_"
TEMP_MAX_AGE_S  = 86_400  # clean up temp files older than 24 h
GITHUB_REPO     = "LukysGaming/SlicerBridge"
GITHUB_API_URL  = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

CONFIG_DIR  = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_FILE    = os.path.join(CONFIG_DIR, "log.txt")

# Protocols intercepted → redirected to the user's chosen slicer
PROTOCOLS: dict[str, str] = {
    "bambustudio":  "BambuStudio  (MakerWorld)",
    "orcaslicer":   "OrcaSlicer   (MakerWorld, Printables)",
    "prusaslicer":  "PrusaSlicer  (Printables)",
    "prusa3d":      "Prusa3D      (Printables)",
    "cura":         "Cura         (Thingiverse, Cura Marketplace)",
    "ideamaker":    "ideaMaker    (Raise3D Library)",
    "simplify3d":   "Simplify3D",
    "flashprint":   "FlashPrint   (FlashForge)",
    "thingiverse":  "Thingiverse  (direct)",
    "creality":     "Creality     (Creality Cloud)",
    "slicerbridge": "SlicerBridge (multi-file, Tampermonkey)",
}

# Slicer auto-detection — first existing path in each list wins
SLICERS: list[dict] = [
    {
        "name": "Creality Print",
        "paths": [
            r"C:\Program Files\Creality\Creality Print 7.1\CrealityPrint.exe",
            r"C:\Program Files\Creality\Creality Print 7.0\CrealityPrint.exe",
            r"C:\Program Files\Creality\Creality Print\CrealityPrint.exe",
        ],
    },
    {
        "name": "UltiMaker Cura",
        "paths": [
            r"C:\Program Files\UltiMaker Cura 5.9\UltiMaker-Cura.exe",
            r"C:\Program Files\UltiMaker Cura 5.8\UltiMaker-Cura.exe",
            r"C:\Program Files\UltiMaker Cura 5.7\UltiMaker-Cura.exe",
            r"C:\Program Files\UltiMaker Cura 5.6\UltiMaker-Cura.exe",
            r"C:\Program Files\UltiMaker Cura 5.5\UltiMaker-Cura.exe",
            r"C:\Program Files\Ultimaker Cura 5.4\UltiMaker-Cura.exe",
            r"C:\Program Files\Ultimaker Cura 5.3\UltiMaker-Cura.exe",
            r"C:\Program Files\Ultimaker Cura 5.2\UltiMaker-Cura.exe",
        ],
    },
    {
        "name": "OrcaSlicer",
        "paths": [
            r"C:\Program Files\OrcaSlicer\OrcaSlicer.exe",
            r"C:\Program Files\Orca-Slicer\OrcaSlicer.exe",
        ],
    },
    {
        "name": "PrusaSlicer",
        "paths": [
            r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe",
            r"C:\Program Files\PrusaSlicer\prusa-slicer.exe",
        ],
    },
    {
        "name": "BambuStudio",
        "paths": [
            r"C:\Program Files\Bambu Studio\bambu-studio.exe",
            r"C:\Program Files\BambuStudio\bambu-studio.exe",
        ],
    },
    {
        "name": "Simplify3D",
        "paths": [
            r"C:\Program Files\Simplify3D\Simplify3D.exe",
            r"C:\Program Files (x86)\Simplify3D\Simplify3D.exe",
        ],
    },
    {
        "name": "ideaMaker",
        "paths": [
            r"C:\Program Files\Raise3D\ideaMaker\ideaMaker.exe",
            r"C:\Program Files (x86)\Raise3D\ideaMaker\ideaMaker.exe",
        ],
    },
    {
        "name": "FlashPrint",
        "paths": [
            r"C:\Program Files\FlashForge\FlashPrint5\FlashPrint5.exe",
            r"C:\Program Files\FlashForge\FlashPrint\FlashPrint.exe",
            r"C:\Program Files (x86)\FlashForge\FlashPrint5\FlashPrint5.exe",
        ],
    },
    {
        "name": "Chitubox",
        "paths": [
            r"C:\Program Files\CBD-Tech\CHITUBOX\CHITUBOX.exe",
            r"C:\Program Files\CHITUBOX\CHITUBOX.exe",
        ],
    },
]

VALID_EXTENSIONS = {
    ".3mf", ".stl", ".obj", ".amf", ".step", ".stp",
    ".gcode", ".ctb", ".cbddlp", ".photon",
}

# HTTP headers sent with every download request
_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/octet-stream,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def log(msg: str) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# MIGRATION
# ═══════════════════════════════════════════════════════════════════════════════

def _run_migrations(cfg: dict) -> dict:
    """
    Runs any pending config migrations in order.
    Each migration_v{N}_to_v{N+1} receives the config dict and returns updated dict.
    Registry is re-written after any migration so new protocols are registered.
    """
    current = cfg.get("schema_version", 0)

    # ── Define migrations here as the app evolves ─────────────────────────────
    # Example:
    # def _migrate_v1_to_v2(c: dict) -> dict:
    #     c.setdefault("some_new_key", "default_value")
    #     return c
    # migrations = {1: _migrate_v1_to_v2}
    migrations: dict = {}

    changed = False
    while current < SCHEMA_VERSION:
        fn = migrations.get(current)
        if fn:
            log(f"Migration: schema v{current} → v{current + 1}")
            cfg = fn(cfg)
        current += 1
        changed = True

    if changed:
        cfg["schema_version"] = SCHEMA_VERSION
        save_config(cfg)
        log(f"Migration complete — schema now at v{SCHEMA_VERSION}")
        # Re-register protocols silently so any new ones take effect
        installed = cfg.get("installed_exe", "")
        if installed and os.path.isfile(installed):
            try:
                write_registry(installed)
                log("Registry refreshed after migration.")
            except Exception as e:
                log(f"Registry refresh after migration failed: {e}")

    return cfg


def run_migrations_if_needed() -> None:
    """Called on startup — checks schema version and migrates if behind."""
    cfg = load_config()
    if cfg.get("schema_version", 0) < SCHEMA_VERSION:
        _run_migrations(cfg)


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-UPDATE
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_version(v: str) -> tuple[int, ...]:
    """Parses '2.0.1' or 'v2.0.1' into (2, 0, 1)."""
    v = v.lstrip("v").strip()
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _fetch_latest_release() -> tuple[str, str] | None:
    """
    Calls GitHub API and returns (tag, download_url) of the latest release exe,
    or None on any error.
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"User-Agent": f"SlicerBridge/{VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        tag = data.get("tag_name", "")
        assets = data.get("assets", [])
        exe_asset = next(
            (a for a in assets if a.get("name", "").endswith(".exe")),
            None,
        )
        if not tag or not exe_asset:
            return None
        return tag, exe_asset["browser_download_url"]
    except Exception as e:
        log(f"Update check failed: {e}")
        return None


def _do_update(new_version: str, download_url: str) -> None:
    """Downloads new exe, writes a helper bat, restarts."""
    import tkinter as tk
    from tkinter import messagebox

    log(f"Update: downloading v{new_version} from {download_url}")

    # Download to temp
    tmp_dir = tempfile.mkdtemp(prefix=TEMP_PREFIX)
    new_exe = os.path.join(tmp_dir, EXE_NAME)

    try:
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": f"SlicerBridge/{VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(new_exe, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:
        log(f"Update download failed: {e}")
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("SlicerBridge — Update failed", f"Could not download update:\n{e}")
        r.destroy()
        return

    current_exe = os.path.abspath(sys.executable)
    current_pid = os.getpid()

    # Write a helper bat that:
    #   1. waits for old process to exit
    #   2. copies new exe over old exe
    #   3. starts new exe
    #   4. deletes itself
    bat_path = os.path.join(tmp_dir, "sb_updater.bat")
    bat = (
        "@echo off\n"
        f":wait\n"
        f"tasklist /FI \"PID eq {current_pid}\" 2>nul | find \"{current_pid}\" >nul\n"
        f"if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\n"
        f"copy /y \"{new_exe}\" \"{current_exe}\" >nul\n"
        f"start \"\" \"{current_exe}\"\n"
        f"del \"%~f0\"\n"
    )
    with open(bat_path, "w") as f:
        f.write(bat)

    log(f"Update: launching helper bat, exiting current process.")
    subprocess.Popen(
        ["cmd.exe", "/c", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    sys.exit(0)


def _show_update_prompt(new_version: str, download_url: str) -> None:
    """Small standalone tkinter window asking user if they want to update."""
    import tkinter as tk

    root = tk.Tk()
    root.overrideredirect(True)
    root.configure(bg=Theme.BG)
    root.attributes("-topmost", True)

    # Center on screen
    root.update_idletasks()
    w, h = 420, 160
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # Titlebar
    tb = tk.Frame(root, bg=Theme.BORDER, height=28)
    tb.pack(fill="x")
    tb.pack_propagate(False)
    tk.Label(tb, text="⬡  SlicerBridge — Update available",
             bg=Theme.BORDER, fg=Theme.FG, font=("Segoe UI", 9, "bold")).pack(side="left", padx=10, pady=4)
    tk.Label(tb, text="✕", bg=Theme.BORDER, fg=Theme.GRAY,
             font=("Segoe UI", 11), cursor="hand2", padx=10).pack(side="right")\
        .bind("<Button-1>", lambda e: root.destroy())

    drag: dict = {}
    tb.bind("<Button-1>",  lambda e: drag.update(x=e.x, y=e.y))
    tb.bind("<B1-Motion>", lambda e: root.geometry(
        f"+{root.winfo_x() + e.x - drag['x']}+{root.winfo_y() + e.y - drag['y']}"
    ))

    # Body
    body = tk.Frame(root, bg=Theme.BG)
    body.pack(fill="both", expand=True, padx=20, pady=12)

    tk.Label(body,
             text=f"Version {new_version} is available  (current: {VERSION})\nDo you want to update now?",
             bg=Theme.BG, fg=Theme.FG, font=("Segoe UI", 10),
             justify="left").pack(anchor="w")

    btn_row = tk.Frame(body, bg=Theme.BG)
    btn_row.pack(anchor="e", pady=(12, 0))

    def on_yes():
        root.destroy()
        _do_update(new_version, download_url)

    tk.Button(btn_row, text="  Yes, update  ",
              bg=Theme.ACCENT, fg=Theme.BG, relief="flat",
              font=("Segoe UI", 9, "bold"), cursor="hand2",
              activebackground=Theme.FG, activeforeground=Theme.BG,
              command=on_yes, padx=10, pady=4).pack(side="left", padx=(0, 8))

    tk.Button(btn_row, text="Not now",
              bg=Theme.BORDER, fg=Theme.GRAY, relief="flat",
              font=("Segoe UI", 9), cursor="hand2",
              activebackground=Theme.PANEL, activeforeground=Theme.FG,
              command=root.destroy, padx=10, pady=4).pack(side="left")

    root.mainloop()


def start_update_check() -> None:
    """
    Spawns a background thread that checks for updates.
    If a newer version is found, shows the update prompt on the main thread
    via root.after (if GUI is open) or directly (if running as protocol handler).
    """
    def _check():
        result = _fetch_latest_release()
        if result is None:
            return
        tag, url = result
        if _parse_version(tag) > _parse_version(VERSION):
            log(f"Update available: {tag}")
            # Show prompt — safe to call from thread since it creates its own root
            _show_update_prompt(tag.lstrip("v"), url)

    t = threading.Thread(target=_check, daemon=True, name="sb-update-check")
    t.start()


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_build_date() -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(sys.executable)).strftime("%Y-%m-%d  %H:%M")
    except Exception:
        return "unknown"


def scan_slicers() -> list[tuple[dict, str]]:
    """Returns [(slicer_def, found_path), ...] for every detected slicer."""
    found = []
    for slicer in SLICERS:
        for path in slicer["paths"]:
            if os.path.isfile(path):
                found.append((slicer, path))
                break
    return found


def needs_install() -> bool:
    """
    Returns True when the user should be prompted for an install location.

    Skipped when config already records a valid installed exe path.
    Triggered on first run, or when the exe is running from a temp/download folder.
    """
    cfg = load_config()
    installed = cfg.get("installed_exe", "")
    if installed and os.path.isfile(installed):
        return False  # Already set up

    if not os.path.isfile(CONFIG_FILE):
        return True   # First-ever run

    # Running from a staging / download-like folder
    exe = sys.executable.lower()
    staging_indicators = [
        "downloads", "desktop",
        "\\temp\\", "\\tmp\\", "/temp/", "/tmp/",
    ]
    return any(s in exe for s in staging_indicators)



def move_to_install_dir(dest_dir: str, remove_source: bool = True) -> str:
    """
    Copies (or moves) SlicerBridge into dest_dir.

    Handles both PyInstaller layouts:
      - onedir : copies the entire directory (exe + _internal DLLs/pyds)
      - onefile: copies just the exe

    Returns the full path of the installed SlicerBridge.exe.
    """
    os.makedirs(dest_dir, exist_ok=True)
    src_exe  = os.path.abspath(sys.executable)
    src_dir  = os.path.dirname(src_exe)
    dest_exe = os.path.join(dest_dir, EXE_NAME)

    is_onedir = any(f.endswith((".dll", ".pyd", ".so")) for f in os.listdir(src_dir))

    if is_onedir:
        if os.path.normcase(src_dir) != os.path.normcase(dest_dir):
            if os.path.isdir(dest_dir):
                shutil.rmtree(dest_dir, ignore_errors=True)
            shutil.copytree(src_dir, dest_dir)
            if remove_source:
                shutil.rmtree(src_dir, ignore_errors=True)
    else:
        if os.path.normcase(src_exe) != os.path.normcase(dest_exe):
            shutil.copy2(src_exe, dest_exe)
            if remove_source:
                try:
                    os.remove(src_exe)
                except OSError:
                    pass

    return dest_exe


def cleanup_old_temp_files() -> None:
    """Removes SlicerBridge temp files/dirs older than TEMP_MAX_AGE_S seconds."""
    tmp     = tempfile.gettempdir()
    cutoff  = time.time() - TEMP_MAX_AGE_S
    try:
        for name in os.listdir(tmp):
            if not name.startswith(TEMP_PREFIX):
                continue
            path = os.path.join(tmp, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

def write_registry(exe_path: str) -> None:
    """Registers all protocols in HKEY_CLASSES_ROOT. Requires admin."""
    for proto, label in PROTOCOLS.items():
        root_key = winreg.CreateKeyEx(
            winreg.HKEY_CLASSES_ROOT, proto, 0,
            winreg.KEY_WRITE | winreg.KEY_CREATE_SUB_KEY,
        )
        winreg.SetValueEx(root_key, "",             0, winreg.REG_SZ, f"URL:{label}")
        winreg.SetValueEx(root_key, "URL Protocol", 0, winreg.REG_SZ, "")
        winreg.CloseKey(root_key)

        cmd_key = winreg.CreateKeyEx(
            winreg.HKEY_CLASSES_ROOT,
            rf"{proto}\shell\open\command", 0,
            winreg.KEY_WRITE | winreg.KEY_CREATE_SUB_KEY,
        )
        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, f'"{exe_path}" "%1"')
        winreg.CloseKey(cmd_key)

    log(f"Registry written — {len(PROTOCOLS)} protocols → {exe_path}")


def remove_registry() -> None:
    """Removes all SlicerBridge protocol registrations. Requires admin."""
    for proto in PROTOCOLS:
        for subkey in [
            rf"{proto}\shell\open\command",
            rf"{proto}\shell\open",
            rf"{proto}\shell",
            proto,
        ]:
            try:
                winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, subkey)
            except FileNotFoundError:
                pass
    log(f"Registry cleared — {len(PROTOCOLS)} protocols removed.")


def is_registered() -> bool:
    """Returns True if SlicerBridge has registered at least one protocol."""
    # Our own protocol is guaranteed to be ours if present
    try:
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"slicerbridge\shell\open\command")
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        pass

    # Fallback: check the first protocol and verify it points to us
    first_proto = next(iter(PROTOCOLS))
    try:
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{first_proto}\shell\open\command")
        val, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        return APP_NAME.lower() in val.lower()
    except (FileNotFoundError, OSError):
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD
# ═══════════════════════════════════════════════════════════════════════════════

def download_file(url: str, dest: str) -> None:
    """
    Downloads url to dest using streaming chunks (no full-file memory load).
    Adds Referer/Origin headers derived from the URL's own origin.
    Raises urllib.error.HTTPError / urllib.error.URLError on failure.
    """
    parsed = urllib.parse.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    headers = {
        **_DOWNLOAD_HEADERS,
        "Referer":        origin + "/",
        "Origin":         origin,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.HTTPRedirectHandler(),
    )
    req = urllib.request.Request(url, headers=headers)
    with opener.open(req, timeout=30) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)  # streams in chunks — safe for large files


def extract_url(uri: str) -> Optional[str]:
    """
    Extracts a download URL from a slicer protocol URI.

    Tries recognised query-parameter names first (file, model, url,
    download_url, files), then falls back to the first 'http' substring.
    Returns None if no URL can be found.
    """
    try:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
        for key in ("file", "model", "url", "download_url", "files"):
            if key in params:
                return urllib.parse.unquote(params[key][0])
    except Exception as e:
        log(f"  URL extraction (query params) failed: {e}")

    idx = uri.find("http")
    if idx != -1:
        return urllib.parse.unquote(uri[idx:].split("&")[0])

    return None


def _safe_extension(url: str, fallback: str = ".3mf") -> str:
    """Returns the file extension from the URL path, or fallback if not recognised."""
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return ext if ext in VALID_EXTENSIONS else fallback


# ═══════════════════════════════════════════════════════════════════════════════
# PROTOCOL HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

def _launch_slicer(slicer_path: str, *file_paths: str) -> None:
    """Launches slicer_path with the given files. No console window."""
    subprocess.Popen(
        [slicer_path, *file_paths],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def handle_protocol(uri: str) -> None:
    """Entry point for all protocol URIs. Routes multi-file URIs to handle_multi."""
    log(f"\n--- PROTOCOL CLICK ---")
    log(f"URI: {uri}")

    if uri.startswith("slicerbridge://multi"):
        handle_multi(uri)
        return

    cfg    = load_config()
    slicer = cfg.get("slicer_path", "")

    if not slicer or not os.path.isfile(slicer):
        log("Slicer not configured — opening GUI")
        show_gui(error="Slicer is not configured or was not found. Please select one below.")
        return

    log(f"Slicer: {slicer}")

    url = extract_url(uri)
    if not url:
        log(f"Could not extract a download URL from: {uri}")
        return

    log(f"URL: {url}")

    stamp  = int(time.time() * 1000) % 100_000
    ext    = _safe_extension(url)
    target = os.path.join(tempfile.gettempdir(), f"{TEMP_PREFIX}{stamp}{ext}")
    log(f"Downloading to: {target}")

    try:
        cleanup_old_temp_files()
        download_file(url, target)
        log("Download OK — launching slicer")
        _launch_slicer(slicer, target)
        log("Slicer launched OK")
    except urllib.error.HTTPError as e:
        log(f"HTTP error: {e}\n{traceback.format_exc()}")
    except urllib.error.URLError as e:
        log(f"Network error: {e}\n{traceback.format_exc()}")
    except Exception as e:
        log(f"Unexpected error: {e}\n{traceback.format_exc()}")


def handle_multi(uri: str) -> None:
    """
    Handles slicerbridge://multi?files=<url1>|<url2>|...&names=<name1>|<name2>|...
    Downloads all files then opens the slicer with all of them at once.
    """
    log(f"\n--- MULTI-FILE ---")
    log(f"URI: {uri}")

    cfg    = load_config()
    slicer = cfg.get("slicer_path", "")

    if not slicer or not os.path.isfile(slicer):
        log("Slicer not configured — opening GUI")
        show_gui(error="Slicer is not configured or was not found. Please select one below.")
        return

    try:
        params    = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
        raw_files = params.get("files", [""])[0]
        raw_names = params.get("names", [""])[0]

        urls  = [u for u in urllib.parse.unquote(raw_files).split("|") if u.strip()]
        names = [n for n in urllib.parse.unquote(raw_names).split("|") if n.strip()]

        if not urls:
            log("No files to download")
            return

        log(f"Downloading {len(urls)} file(s)...")
        cleanup_old_temp_files()

        tmp_dir    = tempfile.mkdtemp(prefix=f"{TEMP_PREFIX}multi_")
        downloaded = []

        for i, url in enumerate(urls):
            filename = (
                names[i] if i < len(names) and names[i]
                else os.path.basename(urllib.parse.urlparse(url).path) or f"model_{i + 1}.stl"
            )
            dest = os.path.join(tmp_dir, filename)
            log(f"  [{i + 1}/{len(urls)}] {filename} ← {url}")
            try:
                download_file(url, dest)
                downloaded.append(dest)
                log(f"  OK: {dest}")
            except Exception as e:
                log(f"  FAILED {filename}: {e}")

        if not downloaded:
            log("No files downloaded successfully")
            return

        log(f"Downloaded {len(downloaded)}/{len(urls)} — launching slicer")
        _launch_slicer(slicer, *downloaded)
        log("Slicer launched OK")

    except Exception as e:
        log(f"Multi-file error: {e}\n{traceback.format_exc()}")


# ═══════════════════════════════════════════════════════════════════════════════
# GUI — Theme
# ═══════════════════════════════════════════════════════════════════════════════

class Theme:
    BG      = "#16161e"
    PANEL   = "#1a1b26"
    BORDER  = "#2a2b3d"
    ACCENT  = "#7aa2f7"
    GREEN   = "#9ece6a"
    RED     = "#f7768e"
    YELLOW  = "#e0af68"
    GRAY    = "#565f89"
    FG      = "#c0caf5"
    FG_DIM  = "#414868"
    FONT    = ("Segoe UI", 10)
    FONT_SM = ("Segoe UI", 9)
    FONT_LG = ("Segoe UI", 12, "bold")
    FONT_H  = ("Segoe UI", 18, "bold")


# ═══════════════════════════════════════════════════════════════════════════════
# GUI — Widget helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_button(parent, text: str, cmd, *, primary=False, danger=False) -> None:
    """Creates a flat styled button and packs it to the left."""
    import tkinter as tk

    bg    = Theme.ACCENT if primary else (Theme.RED    if danger else Theme.BORDER)
    fg    = Theme.BG     if primary else (Theme.BG     if danger else Theme.FG)
    hover = Theme.GREEN  if primary else (Theme.RED    if danger else Theme.GRAY)
    font  = ("Segoe UI", 10, "bold") if primary else Theme.FONT

    tk.Button(
        parent, text=text, command=cmd,
        bg=bg, fg=fg, font=font,
        relief="flat", cursor="hand2", padx=16, pady=7,
        activebackground=hover, activeforeground=Theme.BG,
    ).pack(side="left", padx=5)


def _sep(parent) -> None:
    """Draws a horizontal rule."""
    import tkinter as tk
    tk.Frame(parent, bg=Theme.BORDER, height=1).pack(fill="x", padx=28, pady=10)


# ═══════════════════════════════════════════════════════════════════════════════
# GUI — Section builders
# ═══════════════════════════════════════════════════════════════════════════════

def _build_titlebar(root) -> None:
    """Custom frameless titlebar with drag, minimize, and close."""
    import tkinter as tk

    tb = tk.Frame(root, bg=Theme.BORDER, height=32)
    tb.pack(fill="x", side="top")
    tb.pack_propagate(False)

    tk.Label(
        tb, text="⬡  SlicerBridge",
        bg=Theme.BORDER, fg=Theme.FG, font=("Segoe UI", 10, "bold"),
    ).pack(side="left", padx=12, pady=6)

    def _minimize():
        root.overrideredirect(False)
        root.iconify()

    root.bind("<Map>", lambda e: root.overrideredirect(True))

    for txt, cmd, hover_color in [("✕", root.destroy, Theme.RED), ("─", _minimize, Theme.GRAY)]:
        b = tk.Label(tb, text=txt, bg=Theme.BORDER, fg=Theme.GRAY,
                     font=("Segoe UI", 11), cursor="hand2", padx=12)
        b.pack(side="right")
        b.bind("<Button-1>", lambda e, c=cmd: c())
        b.bind("<Enter>",    lambda e, w=b, h=hover_color: w.configure(fg=h, bg="#3a3b4d"))
        b.bind("<Leave>",    lambda e, w=b: w.configure(fg=Theme.GRAY, bg=Theme.BORDER))

    drag: dict = {}
    tb.bind("<Button-1>",  lambda e: drag.update(x=e.x, y=e.y))
    tb.bind("<B1-Motion>", lambda e: root.geometry(
        f"+{root.winfo_x() + e.x - drag['x']}+{root.winfo_y() + e.y - drag['y']}"
    ))


def _build_install_section(root, install_dir_var, move_var) -> None:
    """Step 1: choose install location (only shown when running from a staging folder)."""
    import tkinter as tk
    from tkinter import filedialog

    tk.Label(root, text="Step 1 — Choose install location",
             bg=Theme.BG, fg=Theme.FG, font=Theme.FONT_LG).pack(anchor="w", padx=28)
    tk.Label(root,
             text="SlicerBridge will copy itself here so it works permanently.\n"
                  "Default is Program Files (x86) — you can change it below.",
             bg=Theme.BG, fg=Theme.GRAY, font=Theme.FONT_SM).pack(anchor="w", padx=30, pady=(2, 8))

    row = tk.Frame(root, bg=Theme.BG)
    row.pack(fill="x", padx=28, pady=4)

    tk.Entry(row, textvariable=install_dir_var, width=52,
             bg=Theme.PANEL, fg=Theme.FG, insertbackground=Theme.FG,
             relief="flat", font=Theme.FONT, bd=6).pack(side="left", padx=(0, 8))

    def browse():
        p = filedialog.askdirectory(title="Choose install folder")
        if p:
            install_dir_var.set(p.replace("/", "\\"))

    tk.Button(row, text="Browse…", bg=Theme.BORDER, fg=Theme.FG, relief="flat",
              font=Theme.FONT, activebackground=Theme.ACCENT, activeforeground=Theme.BG,
              cursor="hand2", command=browse, padx=10).pack(side="left")

    tk.Checkbutton(
        root,
        text="Move file to install location (removes it from Downloads)",
        variable=move_var,
        bg=Theme.BG, fg=Theme.FG, selectcolor=Theme.BG,
        activebackground=Theme.BG, activeforeground=Theme.ACCENT,
        font=Theme.FONT_SM, cursor="hand2",
    ).pack(anchor="w", padx=28, pady=(4, 0))

    # Quick-pick shortcuts
    picks = tk.Frame(root, bg=Theme.BG)
    picks.pack(anchor="w", padx=28, pady=(4, 0))
    tk.Label(picks, text="Quick pick: ", bg=Theme.BG, fg=Theme.GRAY,
             font=Theme.FONT_SM).pack(side="left")
    for label, path in [
        ("Program Files (x86)", r"C:\Program Files (x86)\SlicerBridge"),
        ("Program Files",       r"C:\Program Files\SlicerBridge"),
        ("AppData",             os.path.join(os.environ.get("APPDATA", ""), APP_NAME)),
    ]:
        tk.Button(picks, text=label, bg=Theme.FG_DIM, fg=Theme.FG, relief="flat",
                  font=Theme.FONT_SM, activebackground=Theme.GRAY, activeforeground=Theme.BG,
                  cursor="hand2", padx=6, pady=2,
                  command=lambda p=path: install_dir_var.set(p)).pack(side="left", padx=3)

    _sep(root)


def _build_slicer_section(root, found_slicers, current_slicer,
                           sel_var, manual_var, section_title: str) -> None:
    """Slicer selection: radio buttons for auto-detected slicers + manual browse."""
    import tkinter as tk
    from tkinter import filedialog

    tk.Label(root, text=section_title, bg=Theme.BG, fg=Theme.FG,
             font=Theme.FONT_LG).pack(anchor="w", padx=28)
    tk.Label(root, text="All intercepted protocols will open models in this slicer.",
             bg=Theme.BG, fg=Theme.GRAY, font=Theme.FONT_SM).pack(anchor="w", padx=30, pady=(2, 8))

    list_frame = tk.Frame(root, bg=Theme.PANEL)
    list_frame.pack(fill="x", padx=28, ipady=4)

    if found_slicers:
        for slicer_def, path in found_slicers:
            row = tk.Frame(list_frame, bg=Theme.PANEL)
            row.pack(fill="x", padx=12, pady=3)
            tk.Radiobutton(row, text=f"  {slicer_def['name']}", variable=sel_var, value=path,
                           bg=Theme.PANEL, fg=Theme.FG, selectcolor=Theme.BG,
                           activebackground=Theme.PANEL, activeforeground=Theme.ACCENT,
                           font=Theme.FONT, cursor="hand2").pack(side="left")
            tk.Label(row, text=f"  {path}", bg=Theme.PANEL, fg=Theme.GRAY,
                     font=Theme.FONT_SM).pack(side="left")

        sel_var.set(
            current_slicer if current_slicer and os.path.isfile(current_slicer)
            else found_slicers[0][1]
        )
    else:
        tk.Label(list_frame,
                 text="  ⚠  No slicer found automatically — enter the path below.",
                 bg=Theme.PANEL, fg=Theme.RED, font=Theme.FONT).pack(anchor="w", padx=12, pady=10)

    _sep(root)
    tk.Label(root, text="Or enter the path manually (.exe)", bg=Theme.BG, fg=Theme.FG,
             font=Theme.FONT).pack(anchor="w", padx=28)

    mf = tk.Frame(root, bg=Theme.BG)
    mf.pack(fill="x", padx=28, pady=6)
    tk.Entry(mf, textvariable=manual_var, width=56, bg=Theme.PANEL, fg=Theme.FG,
             insertbackground=Theme.FG, relief="flat", font=Theme.FONT, bd=6).pack(side="left", padx=(0, 8))

    def browse():
        p = filedialog.askopenfilename(
            title="Select slicer .exe",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if p:
            manual_var.set(p)
            sel_var.set("")

    tk.Button(mf, text="Browse…", bg=Theme.BORDER, fg=Theme.FG, relief="flat", font=Theme.FONT,
              activebackground=Theme.ACCENT, activeforeground=Theme.BG,
              cursor="hand2", command=browse, padx=10).pack(side="left")


def _build_uninstall_section(root, status_var, status_lbl) -> None:
    """Danger zone — only shown when registry entries already exist."""
    import tkinter as tk
    from tkinter import messagebox

    frame = tk.Frame(root, bg=Theme.BG)
    frame.pack(anchor="w", padx=28, pady=(0, 4))
    tk.Label(frame, text="Danger zone", bg=Theme.BG, fg=Theme.GRAY,
             font=Theme.FONT_SM).pack(side="left", padx=(0, 12))

    def do_uninstall():
        if not messagebox.askyesno(
            "Uninstall SlicerBridge",
            "This will remove all registry entries.\n\n"
            "Your slicer and downloaded models are NOT affected.\n\nContinue?",
            icon="warning",
        ):
            return
        if not is_admin():
            status_lbl.configure(fg=Theme.YELLOW)
            status_var.set("Requesting admin rights (UAC)…")
            root.update()
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "--uninstall", None, 1)
            root.after(2000, root.destroy)
            return
        try:
            remove_registry()
            try:
                os.remove(CONFIG_FILE)
            except FileNotFoundError:
                pass
            status_lbl.configure(fg=Theme.GREEN)
            status_var.set("✓  Uninstalled. Registry cleared and config removed.")
        except Exception as e:
            status_lbl.configure(fg=Theme.RED)
            status_var.set(f"✗  {e}")

    _make_button(frame, "  ✗  Uninstall  ", do_uninstall, danger=True)


def _build_footer(root) -> None:
    import tkinter as tk

    tk.Frame(root, bg=Theme.BORDER, height=1).pack(fill="x", padx=28, pady=(12, 4))
    footer = tk.Frame(root, bg=Theme.BG)
    footer.pack(fill="x", padx=28, pady=(0, 14))

    tk.Label(footer, text=f"v{VERSION}  ·  built {get_build_date()}",
             bg=Theme.BG, fg=Theme.FG_DIM, font=Theme.FONT_SM).pack(side="left")

    gh = tk.Label(footer, text="GitHub ↗", bg=Theme.BG, fg=Theme.ACCENT,
                  font=Theme.FONT_SM, cursor="hand2")
    gh.pack(side="right")
    gh.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/LukysGaming/SlicerBridge"))
    gh.bind("<Enter>",    lambda e: gh.configure(fg=Theme.FG))
    gh.bind("<Leave>",    lambda e: gh.configure(fg=Theme.ACCENT))


# ═══════════════════════════════════════════════════════════════════════════════
# GUI — Main window
# ═══════════════════════════════════════════════════════════════════════════════

def show_gui(error: str = "") -> None:
    import tkinter as tk

    cfg             = load_config()
    current_slicer  = cfg.get("slicer_path", "")
    current_install = cfg.get("installed_exe", "")
    found_slicers   = scan_slicers()
    offer_install   = needs_install()

    TITLEBAR_H = 32
    win_height  = (660 if offer_install else 580) + TITLEBAR_H

    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"700x{win_height}")
    root.configure(bg=Theme.BG)

    _build_titlebar(root)

    # ── Header ────────────────────────────────────────────────────────────────
    tk.Label(root, text="⬡  SlicerBridge",
             bg=Theme.BG, fg=Theme.ACCENT, font=Theme.FONT_H).pack(anchor="w", padx=28, pady=(22, 0))
    tk.Label(root, text="Redirects all slicer protocols to your preferred slicer.",
             bg=Theme.BG, fg=Theme.GRAY, font=Theme.FONT_SM).pack(anchor="w", padx=30)
    _sep(root)

    # ── Section 1: Install location (only on first run / staging folder) ──────
    install_dir_var = tk.StringVar(value=(
        os.path.dirname(current_install) if current_install else INSTALL_DIR_DEF
    ))
    move_var = tk.BooleanVar(value=True)

    if offer_install:
        _build_install_section(root, install_dir_var, move_var)
        step2_label = "Step 2 — Choose your slicer"
    else:
        step2_label = "Target slicer"

    # ── Section 2: Slicer selection ───────────────────────────────────────────
    sel_var    = tk.StringVar()
    manual_var = tk.StringVar()
    _build_slicer_section(root, found_slicers, current_slicer, sel_var, manual_var, step2_label)

    # ── Status line ───────────────────────────────────────────────────────────
    status_var = tk.StringVar(value=error)
    status_lbl = tk.Label(root, textvariable=status_var,
                          bg=Theme.BG, fg=Theme.RED if error else Theme.GRAY,
                          font=Theme.FONT_SM)
    status_lbl.pack(anchor="w", padx=30, pady=(6, 0))

    tk.Label(root, text="Intercepted: " + "  ".join(PROTOCOLS.keys()),
             bg=Theme.BG, fg=Theme.FG_DIM, font=Theme.FONT_SM).pack(anchor="w", padx=28, pady=(4, 0))
    _sep(root)

    # ── Install button ────────────────────────────────────────────────────────
    btn_frame = tk.Frame(root, bg=Theme.BG)
    btn_frame.pack(pady=4)

    def do_install():
        chosen = manual_var.get().strip() or sel_var.get()
        if not chosen:
            status_lbl.configure(fg=Theme.RED)
            status_var.set("⚠  Please select a slicer or enter its path.")
            return
        if not os.path.isfile(chosen):
            status_lbl.configure(fg=Theme.RED)
            status_var.set(f"⚠  File not found: {chosen}")
            return

        if offer_install:
            dest_dir = install_dir_var.get().strip()
            if not dest_dir:
                status_lbl.configure(fg=Theme.RED)
                status_var.set("⚠  Please enter an install directory.")
                return
            handler_exe = os.path.join(dest_dir, EXE_NAME)
        else:
            dest_dir    = ""
            handler_exe = sys.executable

        save_config({
            "schema_version":  SCHEMA_VERSION,
            "slicer_path":   chosen,
            "installed_exe": handler_exe,
            "handler_exe":   handler_exe,
            "install_dir":   dest_dir,
            "source_exe":    sys.executable,
            "move_source":   move_var.get() if offer_install else False,
        })

        if not is_admin():
            status_lbl.configure(fg=Theme.YELLOW)
            status_var.set("Requesting admin rights (UAC)…")
            root.update()
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "--register", None, 1)
            root.after(2000, root.destroy)
            return

        # Already admin (user ran as admin directly — uncommon but valid)
        try:
            write_registry(handler_exe)
            status_lbl.configure(fg=Theme.GREEN)
            status_var.set(f"✓  Done! {len(PROTOCOLS)} protocols registered → {os.path.basename(chosen)}")
        except PermissionError:
            status_lbl.configure(fg=Theme.RED)
            status_var.set("✗  Permission denied. Please run as Administrator.")
        except Exception as e:
            status_lbl.configure(fg=Theme.RED)
            status_var.set(f"✗  {e}")

    _make_button(btn_frame, "  ✓  Install  ", do_install, primary=True)
    _make_button(btn_frame, "Open log",
                 lambda: os.startfile(LOG_FILE) if os.path.isfile(LOG_FILE) else status_var.set("No log yet."))
    _make_button(btn_frame, "Config folder",
                 lambda: (os.makedirs(CONFIG_DIR, exist_ok=True), os.startfile(CONFIG_DIR)))

    # ── Uninstall (danger zone) — only when already registered ───────────────
    if is_registered():
        _sep(root)
        _build_uninstall_section(root, status_var, status_lbl)

    _build_footer(root)
    root.mainloop()


# ═══════════════════════════════════════════════════════════════════════════════
# CLI MODES
# ═══════════════════════════════════════════════════════════════════════════════

def _cmd_register() -> None:
    """--register: UAC-elevated subprocess — copies exe to install dir, writes registry."""
    import tkinter as tk
    from tkinter import messagebox

    if not is_admin():
        sys.exit(1)

    cfg    = load_config()
    dest_dir = cfg.get("install_dir", "")
    slicer   = cfg.get("slicer_path", "???")

    r = tk.Tk(); r.withdraw()
    try:
        if dest_dir:
            handler_exe = move_to_install_dir(dest_dir, remove_source=cfg.get("move_source", True))
            cfg["installed_exe"] = handler_exe
            cfg["handler_exe"]   = handler_exe
            save_config(cfg)
        else:
            handler_exe = sys.executable

        write_registry(handler_exe)

        messagebox.showinfo(
            "SlicerBridge — Installed",
            f"Installation complete!\n\n"
            f"{len(PROTOCOLS)} protocols registered.\n\n"
            f"Handler: {handler_exe}\n"
            f"Slicer:  {slicer}",
        )
    except Exception as e:
        messagebox.showerror("SlicerBridge — Error", str(e))
    finally:
        r.destroy()


def _cmd_uninstall() -> None:
    """--uninstall: UAC-elevated subprocess — removes registry entries and install folder."""
    import tkinter as tk
    from tkinter import messagebox

    if not is_admin():
        sys.exit(1)

    cfg           = load_config()
    installed_exe = cfg.get("installed_exe", "")

    r = tk.Tk(); r.withdraw()
    try:
        remove_registry()

        try:
            os.remove(CONFIG_FILE)
        except FileNotFoundError:
            pass

        exe_note = ""
        if installed_exe:
            install_dir = os.path.dirname(installed_exe)
            target      = install_dir if os.path.isdir(install_dir) else installed_exe
            try:
                if os.path.isdir(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
                exe_note = f"\nInstall folder removed: {install_dir}"
            except OSError:
                exe_note = f"\nCould not remove install folder: {install_dir}"

        messagebox.showinfo(
            "SlicerBridge — Uninstalled",
            f"All {len(PROTOCOLS)} protocol registrations removed.{exe_note}",
        )
    except Exception as e:
        messagebox.showerror("SlicerBridge — Error", str(e))
    finally:
        r.destroy()


def _cmd_reset() -> None:
    """--reset: developer helper — wipes config so the next launch shows Step 1."""
    try:
        os.remove(CONFIG_FILE)
        print(f"Config deleted: {CONFIG_FILE}")
    except FileNotFoundError:
        print("No config found — already clean.")


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = sys.argv[1:]

    # Always run on startup — migrations are no-ops when schema is current
    run_migrations_if_needed()
    # Background update check — never blocks, shows prompt if newer version found
    start_update_check()

    if not args:
        show_gui()
        return

    cmd = args[0]

    if cmd == "--register":
        _cmd_register()
    elif cmd == "--uninstall":
        _cmd_uninstall()
    elif cmd == "--reset":
        _cmd_reset()
    elif any(cmd.startswith(p + "://") for p in PROTOCOLS):
        handle_protocol(cmd)
    else:
        log(f"Unknown argument: {cmd!r} — falling back to GUI")
        show_gui()


if __name__ == "__main__":
    main()