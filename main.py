#!/usr/bin/env python3
"""
SlicerBridge — Universal 3D printing slicer protocol bridge
https://github.com/LukysGaming/SlicerBridge

Build:  pyinstaller --onefile --noconsole main.py

Modes:
  SlicerBridge.exe                 → GUI installer / configurator
  SlicerBridge.exe <protocol://…>  → silent handler (download + open slicer)
  SlicerBridge.exe --register      → write registry (called automatically via UAC)
"""

import sys, os, json, shutil, subprocess, tempfile, traceback
import urllib.parse, urllib.request, urllib.error
import http.cookiejar
import ctypes, winreg

# ═══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

VERSION          = "1.0"
APP_NAME         = "SlicerBridge"
INSTALL_DIR_DEF  = r"C:\Program Files (x86)\SlicerBridge"
EXE_NAME         = "SlicerBridge.exe"
CONFIG_DIR       = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_FILE      = os.path.join(CONFIG_DIR, "config.json")
LOG_FILE         = os.path.join(CONFIG_DIR, "log.txt")

# Protocols to intercept → redirect to user's chosen slicer
PROTOCOLS = {
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
}

# Slicer definitions — first path that exists on disk wins
SLICERS = [
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

VALID_EXT = {".3mf", ".stl", ".obj", ".amf", ".step", ".stp",
             ".gcode", ".ctb", ".cbddlp", ".photon"}

# ═══════════════════════════════════════════════════════════════════════
#  UTILITIES
# ═══════════════════════════════════════════════════════════════════════

def log(msg: str):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(data: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def scan_slicers() -> list:
    """Returns [(slicer_def, found_path), ...]"""
    found = []
    for s in SLICERS:
        for p in s["paths"]:
            if os.path.isfile(p):
                found.append((s, p))
                break
    return found

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def needs_install() -> bool:
    """
    True when the user should be prompted to choose an install location.

    Triggers if:
      - The exe is running from a temporary / download-like folder, OR
      - There is no config yet at all (genuine first run from any location)
    Suppressed if the config already records a valid installed exe path.
    """
    cfg = load_config()
    already_installed = cfg.get("installed_exe", "")
    if already_installed and os.path.isfile(already_installed):
        return False                          # already set up -> skip

    # First-ever run (no config file) -> always ask where to install
    if not os.path.isfile(CONFIG_FILE):
        return True

    # Running from a temporary / staging folder
    exe = sys.executable.lower()
    suspicious = [
        "downloads", "desktop",
        "\\temp\\", "\\tmp\\",
        "/downloads/", "/desktop/", "/temp/", "/tmp/",
    ]
    return any(s in exe for s in suspicious)

def get_build_date() -> str:
    """Returns the .exe modification time as a human-readable build date."""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(os.path.getmtime(sys.executable)).strftime("%Y-%m-%d  %H:%M")
    except Exception:
        return "unknown"

def _schedule_delete(path: str):
    """Spawns a detached .bat that retries until the source exe is deleted."""
    bat = os.path.join(tempfile.gettempdir(), "slicerbridge_cleanup.bat")
    with open(bat, "w") as f:
        f.write(
            "@echo off\n"
            ":loop\n"
            f'del /f /q "{path}" 2>nul\n'
            f'if exist "{path}" (timeout /t 1 >nul && goto loop)\n'
            'del /f /q "%~f0"\n'
        )
    si = subprocess.STARTUPINFO()
    si.dwFlags  |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0   # SW_HIDE
    subprocess.Popen(
        ["cmd.exe", "/c", bat],
        startupinfo=si,
        creationflags=subprocess.DETACHED_PROCESS,
    )

def move_to_install_dir(dest_dir: str) -> str:
    """
    Copies this exe to dest_dir\SlicerBridge.exe, then removes the original.
    Schedules a background cleanup if the OS refuses immediate deletion.
    Returns the full path of the installed exe.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, EXE_NAME)
    src  = os.path.abspath(sys.executable)
    if src.lower() != dest.lower():
        shutil.copy2(src, dest)
        try:
            os.remove(src)
        except OSError:
            _schedule_delete(src)
    return dest

copy_to_install_dir = move_to_install_dir

def is_registered() -> bool:
    """Returns True if at least one SlicerBridge protocol is in the registry."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, list(PROTOCOLS.keys())[0])
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False

def remove_registry():
    """Removes all SlicerBridge protocol registrations from the registry. Requires admin."""
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
    log(f"Registry removed — {len(PROTOCOLS)} protocols unregistered.")

# ═══════════════════════════════════════════════════════════════════════
#  REGISTRY
# ═══════════════════════════════════════════════════════════════════════

def write_registry(exe_path: str):
    """Registers all protocols in HKEY_CLASSES_ROOT. Requires admin."""
    for proto, label in PROTOCOLS.items():
        key = winreg.CreateKeyEx(
            winreg.HKEY_CLASSES_ROOT, proto, 0,
            winreg.KEY_WRITE | winreg.KEY_CREATE_SUB_KEY
        )
        winreg.SetValueEx(key, "",             0, winreg.REG_SZ, f"URL:{label}")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        winreg.CloseKey(key)

        cmd_key = winreg.CreateKeyEx(
            winreg.HKEY_CLASSES_ROOT,
            rf"{proto}\shell\open\command", 0,
            winreg.KEY_WRITE | winreg.KEY_CREATE_SUB_KEY
        )
        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, f'"{exe_path}" "%1"')
        winreg.CloseKey(cmd_key)

    log(f"Registry OK — {len(PROTOCOLS)} protocols -> {exe_path}")

# ═══════════════════════════════════════════════════════════════════════
#  PROTOCOL HANDLER
# ═══════════════════════════════════════════════════════════════════════

def extract_url(uri: str):
    try:
        parsed = urllib.parse.urlparse(uri)
        params = urllib.parse.parse_qs(parsed.query)
        for name in ("file", "model", "url", "download_url", "files"):
            if name in params:
                return urllib.parse.unquote(params[name][0])
    except Exception as e:
        log(f"  parse_qs error: {e}")

    idx = uri.find("http")
    if idx != -1:
        return urllib.parse.unquote(uri[idx:].split("&")[0])
    return None

def handle_protocol(uri: str):
    log(f"\n--- NOVY KLIK ---")
    log(f"argv: {sys.argv}")
    log(f"URI: {uri}")

    cfg    = load_config()
    slicer = cfg.get("slicer_path", "")

    if not slicer or not os.path.isfile(slicer):
        log("Slicer not configured -> opening GUI")
        show_gui(error="Slicer is not configured or was not found. Please select one below.")
        return

    log(f"Slicer: {slicer}")

    try:
        url = extract_url(uri)
        if not url:
            log(f"Could not extract URL from: {uri}")
            return

        log(f"URL: {url}")

        parsed_url = urllib.parse.urlparse(url)
        ext = os.path.splitext(parsed_url.path)[1].lower()
        if ext not in VALID_EXT:
            log(f"Neznámá přípona '{ext}', defaultuji na .3mf")
            ext = ".3mf"

        target = os.path.join(tempfile.gettempdir(), f"slicerbridge{ext}")
        log(f"Stahuji do: {target}")

        # Build a Referer from the download URL's origin so sites like
        # Thingiverse don't return 403 Forbidden.
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        referer = origin + "/"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer":        referer,
            "Origin":         origin,
            "Accept":         "application/octet-stream,*/*;q=0.9",
            "Accept-Language":"en-US,en;q=0.9",
            "Accept-Encoding":"gzip, deflate, br",
            "Connection":     "keep-alive",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
        }

        # Some sites (Thingiverse) redirect through auth — follow up to 5 hops.
        cookie_jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar),
            urllib.request.HTTPRedirectHandler(),
        )
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=30) as r, \
             open(target, "wb") as f:
            f.write(r.read())

        log("Staženo OK. Spouštím slicer...")
        subprocess.Popen(
            [slicer, target],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        log("Slicer spuštěn OK")

    except urllib.error.HTTPError as e:
        log(f"CHYBA stahování: {e}\n{traceback.format_exc()}")
    except urllib.error.URLError as e:
        log(f"CHYBA sítě: {e}\n{traceback.format_exc()}")
    except Exception as e:
        log(f"CHYBA: {e}\n{traceback.format_exc()}")

# ═══════════════════════════════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════════════════════════════

def show_gui(error: str = ""):
    import tkinter as tk
    from tkinter import filedialog, messagebox

    # ── Colour palette ────────────────────────────────────────────────
    BG      = "#16161e"
    PANEL   = "#1a1b26"
    BORDER  = "#2a2b3d"
    ACCENT  = "#7aa2f7"
    GREEN   = "#9ece6a"
    RED_C   = "#f7768e"
    YELLOW  = "#e0af68"
    GRAY    = "#565f89"
    FG      = "#c0caf5"
    FG_DIM  = "#414868"
    FONT    = ("Segoe UI", 10)
    FONT_SM = ("Segoe UI", 9)
    FONT_LG = ("Segoe UI", 12, "bold")
    FONT_H  = ("Segoe UI", 18, "bold")

    cfg     = load_config()
    current_slicer  = cfg.get("slicer_path", "")
    current_install = cfg.get("installed_exe", "")
    found   = scan_slicers()
    offer_install = needs_install()

    TITLEBAR_H = 32
    WIN_H = (660 if offer_install else 580) + TITLEBAR_H

    root = tk.Tk()
    root.overrideredirect(True)          # remove native titlebar
    root.geometry(f"700x{WIN_H}")
    root.configure(bg=BG)

    # ── Custom titlebar ───────────────────────────────────────────────
    tb = tk.Frame(root, bg=BORDER, height=TITLEBAR_H)
    tb.pack(fill="x", side="top")
    tb.pack_propagate(False)

    tk.Label(tb, text="⬡  SlicerBridge", bg=BORDER, fg=FG,
             font=("Segoe UI", 10, "bold")).pack(side="left", padx=12, pady=6)

    def _close():  root.destroy()

    def _minimize():
        root.overrideredirect(False)
        root.iconify()

    def _on_map(e):
        root.overrideredirect(True)

    root.bind("<Map>", _on_map)

    for txt, cmd, hover in [("✕", _close, RED_C), ("─", _minimize, GRAY)]:
        b = tk.Label(tb, text=txt, bg=BORDER, fg=GRAY,
                     font=("Segoe UI", 11), cursor="hand2", padx=12)
        b.pack(side="right")
        b.bind("<Button-1>", lambda e, c=cmd: c())
        b.bind("<Enter>",    lambda e, w=b, h=hover: w.configure(fg=h, bg="#3a3b4d"))
        b.bind("<Leave>",    lambda e, w=b: w.configure(fg=GRAY, bg=BORDER))

    # Drag support
    _drag = {}
    def _drag_start(e): _drag["x"], _drag["y"] = e.x, e.y
    def _drag_move(e):
        root.geometry(f"+{root.winfo_x()+e.x-_drag['x']}+{root.winfo_y()+e.y-_drag['y']}")
    tb.bind("<Button-1>",   _drag_start)
    tb.bind("<B1-Motion>",  _drag_move)

    def sep():
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=28, pady=10)

    # ── Header ────────────────────────────────────────────────────────
    tk.Label(root, text="⬡  SlicerBridge",
             bg=BG, fg=ACCENT, font=FONT_H).pack(anchor="w", padx=28, pady=(22, 0))
    tk.Label(root,
             text="Redirects all slicer protocols to your preferred slicer.",
             bg=BG, fg=GRAY, font=FONT_SM).pack(anchor="w", padx=30)

    sep()

    # ── SECTION 1: Install location (shown when running from Downloads etc.) ──
    install_dir_var = tk.StringVar()

    if offer_install:
        tk.Label(root, text="Step 1 — Choose install location",
                 bg=BG, fg=FG, font=FONT_LG).pack(anchor="w", padx=28)
        tk.Label(root,
                 text="SlicerBridge will copy itself to this folder so it works permanently.\n"
                      "Default is Program Files (x86) — you can change it below.",
                 bg=BG, fg=GRAY, font=FONT_SM).pack(anchor="w", padx=30, pady=(2, 8))

        # Suggest Program Files (x86) by default, or current installed location
        install_dir_var.set(
            os.path.dirname(current_install) if current_install
            else INSTALL_DIR_DEF
        )

        inst_frame = tk.Frame(root, bg=BG)
        inst_frame.pack(fill="x", padx=28, pady=4)

        inst_entry = tk.Entry(
            inst_frame, textvariable=install_dir_var, width=52,
            bg=PANEL, fg=FG, insertbackground=FG,
            relief="flat", font=FONT, bd=6
        )
        inst_entry.pack(side="left", padx=(0, 8))

        def browse_install():
            p = filedialog.askdirectory(title="Choose install folder")
            if p:
                install_dir_var.set(p.replace("/", "\\"))

        tk.Button(
            inst_frame, text="Browse…",
            bg=BORDER, fg=FG, relief="flat", font=FONT,
            activebackground=ACCENT, activeforeground=BG,
            cursor="hand2", command=browse_install, padx=10
        ).pack(side="left")

        # Move vs copy checkbox
        move_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            root,
            text="Move file to install location (removes it from Downloads)",
            variable=move_var,
            bg=BG, fg=FG, selectcolor=BG,
            activebackground=BG, activeforeground=ACCENT,
            font=FONT_SM, cursor="hand2",
        ).pack(anchor="w", padx=28, pady=(4, 0))

        # Quick-pick shortcuts
        picks_frame = tk.Frame(root, bg=BG)
        picks_frame.pack(anchor="w", padx=28, pady=(4, 0))
        tk.Label(picks_frame, text="Quick pick: ", bg=BG, fg=GRAY,
                 font=FONT_SM).pack(side="left")
        for label, path in [
            ("Program Files (x86)", r"C:\Program Files (x86)\SlicerBridge"),
            ("Program Files",       r"C:\Program Files\SlicerBridge"),
            ("AppData",             os.path.join(os.environ.get("APPDATA",""), "SlicerBridge")),
        ]:
            tk.Button(
                picks_frame, text=label,
                bg=FG_DIM, fg=FG, relief="flat", font=FONT_SM,
                activebackground=GRAY, activeforeground=BG,
                cursor="hand2", padx=6, pady=2,
                command=lambda p=path: install_dir_var.set(p)
            ).pack(side="left", padx=3)

        sep()
        step2_label = "Step 2 — Choose your slicer"
    else:
        step2_label = "Target slicer"

    # ── SECTION 2: Slicer selection ───────────────────────────────────
    tk.Label(root, text=step2_label,
             bg=BG, fg=FG, font=FONT_LG).pack(anchor="w", padx=28)
    tk.Label(root, text="All intercepted protocols will open models in this slicer.",
             bg=BG, fg=GRAY, font=FONT_SM).pack(anchor="w", padx=30, pady=(2, 8))

    sel_var = tk.StringVar()

    list_frame = tk.Frame(root, bg=PANEL)
    list_frame.pack(fill="x", padx=28, ipady=4)

    if found:
        for slicer_def, path in found:
            row = tk.Frame(list_frame, bg=PANEL)
            row.pack(fill="x", padx=12, pady=3)
            tk.Radiobutton(
                row, text=f"  {slicer_def['name']}",
                variable=sel_var, value=path,
                bg=PANEL, fg=FG, selectcolor=BG,
                activebackground=PANEL, activeforeground=ACCENT,
                font=FONT, cursor="hand2"
            ).pack(side="left")
            tk.Label(row, text=f"  {path}",
                     bg=PANEL, fg=GRAY, font=FONT_SM).pack(side="left")

        sel_var.set(
            current_slicer if current_slicer and os.path.isfile(current_slicer)
            else found[0][1]
        )
    else:
        tk.Label(list_frame,
                 text="  ⚠  No slicer found automatically — enter the path below.",
                 bg=PANEL, fg=RED_C, font=FONT).pack(anchor="w", padx=12, pady=10)

    # ── Manual slicer path ────────────────────────────────────────────
    sep()
    tk.Label(root, text="Or enter the path manually (.exe)",
             bg=BG, fg=FG, font=FONT).pack(anchor="w", padx=28)

    manual_frame = tk.Frame(root, bg=BG)
    manual_frame.pack(fill="x", padx=28, pady=6)

    manual_var = tk.StringVar()
    tk.Entry(
        manual_frame, textvariable=manual_var, width=56,
        bg=PANEL, fg=FG, insertbackground=FG,
        relief="flat", font=FONT, bd=6
    ).pack(side="left", padx=(0, 8))

    def browse_slicer():
        p = filedialog.askopenfilename(
            title="Select slicer .exe",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")]
        )
        if p:
            manual_var.set(p)
            sel_var.set("")

    tk.Button(
        manual_frame, text="Browse…",
        bg=BORDER, fg=FG, relief="flat", font=FONT,
        activebackground=ACCENT, activeforeground=BG,
        cursor="hand2", command=browse_slicer, padx=10
    ).pack(side="left")

    # ── Status line ───────────────────────────────────────────────────
    status_var = tk.StringVar(value=error)
    status_lbl = tk.Label(root, textvariable=status_var,
                          bg=BG, fg=RED_C if error else GRAY, font=FONT_SM)
    status_lbl.pack(anchor="w", padx=30, pady=(6, 0))

    # ── Protocols info ────────────────────────────────────────────────
    tk.Label(root, text="Intercepted: " + "  ".join(PROTOCOLS.keys()),
             bg=BG, fg=FG_DIM, font=FONT_SM).pack(anchor="w", padx=28, pady=(4, 0))

    sep()

    # ── Install button ────────────────────────────────────────────────
    def do_install():
        chosen_slicer = manual_var.get().strip() or sel_var.get()

        if not chosen_slicer:
            status_var.set("⚠  Please select a slicer or enter its path.")
            status_lbl.configure(fg=RED_C)
            return
        if not os.path.isfile(chosen_slicer):
            status_var.set(f"⚠  File not found: {chosen_slicer}")
            status_lbl.configure(fg=RED_C)
            return

        # Determine where the handler exe will live.
        # NOTE: we do NOT copy here — copying to Program Files needs admin,
        # so the actual copy+registry write both happen inside --register
        # (the UAC-elevated process).  We only need to know the destination.
        if offer_install:
            dest_dir = install_dir_var.get().strip()
            if not dest_dir:
                status_var.set("⚠  Please enter an install directory.")
                status_lbl.configure(fg=RED_C)
                return
            handler_exe = os.path.join(dest_dir, EXE_NAME)
        else:
            dest_dir    = ""
            handler_exe = sys.executable

        # Persist everything the elevated process will need
        save_config({
            "slicer_path":   chosen_slicer,
            "installed_exe": handler_exe,
            "handler_exe":   handler_exe,
            "install_dir":   dest_dir,
            "source_exe":    sys.executable,   # so --register knows what to move
        })

        if not is_admin():
            status_var.set("Requesting admin rights (UAC)…")
            status_lbl.configure(fg=YELLOW)
            root.update()
            # Elevate from the CURRENT exe (still in Downloads / wherever)
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas",
                sys.executable, "--register",
                None, 1
            )
            root.after(2000, root.destroy)
            return

        _write(handler_exe, chosen_slicer)

    def _write(handler_exe, slicer):
        try:
            write_registry(handler_exe)
            status_lbl.configure(fg=GREEN)
            status_var.set(
                f"✓  Done! {len(PROTOCOLS)} protocols registered  →  "
                f"{os.path.basename(slicer)}"
            )
        except PermissionError:
            status_lbl.configure(fg=RED_C)
            status_var.set("✗  Permission denied. Please run as Administrator.")
        except Exception as e:
            status_lbl.configure(fg=RED_C)
            status_var.set(f"✗  Error: {e}")

    btn_frame = tk.Frame(root, bg=BG)
    btn_frame.pack(pady=4)

    def mkbtn(parent, text, cmd, primary=False, danger=False):
        c_bg = ACCENT if primary else (RED_C if danger else BORDER)
        c_fg = BG    if primary else (BG     if danger else FG)
        c_ho = GREEN if primary else (RED_C  if danger else GRAY)
        tk.Button(
            parent, text=text, command=cmd,
            bg=c_bg, fg=c_fg,
            font=("Segoe UI", 10, "bold") if primary else FONT,
            relief="flat", cursor="hand2", padx=16, pady=7,
            activebackground=c_ho, activeforeground=BG
        ).pack(side="left", padx=5)

    mkbtn(btn_frame, "  ✓  Install  ", do_install, primary=True)
    mkbtn(btn_frame, "Open log",
          lambda: os.startfile(LOG_FILE) if os.path.isfile(LOG_FILE)
          else status_var.set("No log yet."))
    mkbtn(btn_frame, "Config folder",
          lambda: (os.makedirs(CONFIG_DIR, exist_ok=True),
                   os.startfile(CONFIG_DIR)))

    # ── Uninstall (only shown when registry entries actually exist) ──────
    if is_registered():
        sep()
        uninstall_frame = tk.Frame(root, bg=BG)
        uninstall_frame.pack(anchor="w", padx=28, pady=(0, 4))
        tk.Label(uninstall_frame, text="Danger zone",
                 bg=BG, fg=GRAY, font=FONT_SM).pack(side="left", padx=(0, 12))

        def do_uninstall():
            from tkinter import messagebox
            if not messagebox.askyesno(
                "Uninstall SlicerBridge",
                "This will remove all registry entries.\n\n"
                "Your slicer and downloaded models are NOT affected.\n\n"
                "Continue?",
                icon="warning"
            ):
                return
            if not is_admin():
                status_var.set("Requesting admin rights (UAC)…")
                status_lbl.configure(fg=YELLOW)
                root.update()
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas",
                    sys.executable, "--uninstall",
                    None, 1
                )
                root.after(2000, root.destroy)
                return
            try:
                remove_registry()
                # Delete config entirely so next launch shows Step 1
                try:
                    os.remove(CONFIG_FILE)
                except FileNotFoundError:
                    pass
                status_lbl.configure(fg=GREEN)
                status_var.set("✓  Uninstalled. You can delete SlicerBridge.exe manually.")
            except Exception as e:
                status_lbl.configure(fg=RED_C)
                status_var.set(f"✗  {e}")

        mkbtn(uninstall_frame, "  ✗  Uninstall  ", do_uninstall, danger=True)

    # ── Footer ────────────────────────────────────────────────────────
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=28, pady=(12, 4))
    footer_frame = tk.Frame(root, bg=BG)
    footer_frame.pack(fill="x", padx=28, pady=(0, 14))

    tk.Label(footer_frame,
             text=f"v{VERSION}  ·  built {get_build_date()}",
             bg=BG, fg=FG_DIM, font=FONT_SM).pack(side="left")

    gh_lbl = tk.Label(footer_frame, text="GitHub ↗",
                      bg=BG, fg=ACCENT, font=FONT_SM, cursor="hand2")
    gh_lbl.pack(side="right")
    gh_lbl.bind("<Button-1>", lambda e: __import__("webbrowser").open(
        "https://github.com/LukysGaming/SlicerBridge"))
    gh_lbl.bind("<Enter>", lambda e: gh_lbl.configure(fg=FG))
    gh_lbl.bind("<Leave>", lambda e: gh_lbl.configure(fg=ACCENT))

    root.mainloop()

# ═══════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def _is_protocol_uri(s: str) -> bool:
    return any(s.startswith(p + "://") for p in PROTOCOLS)

if __name__ == "__main__":
    args = sys.argv[1:]

    if args and args[0] == "--register":
        # Called automatically by the GUI after UAC elevation.
        # This process has admin rights, so it can:
        #   1. Create the install directory and copy/move the exe there
        #   2. Write the registry
        if not is_admin():
            sys.exit(1)
        import tkinter as tk
        from tkinter import messagebox
        cfg        = load_config()
        dest_dir   = cfg.get("install_dir", "")
        source_exe = cfg.get("source_exe", sys.executable)
        slicer     = cfg.get("slicer_path", "???")
        try:
            # Step 1 — move exe to its permanent home (if a dest_dir was chosen)
            if dest_dir:
                handler_exe = move_to_install_dir(dest_dir)
                # Update config so future launches use the new path
                cfg["installed_exe"] = handler_exe
                cfg["handler_exe"]   = handler_exe
                save_config(cfg)
            else:
                handler_exe = sys.executable

            # Step 2 — write registry pointing to the new location
            write_registry(handler_exe)

            r = tk.Tk(); r.withdraw()
            messagebox.showinfo(
                "SlicerBridge — Installed",
                f"Installation complete!\n\n"
                f"{len(PROTOCOLS)} protocols registered.\n\n"
                f"Handler:  {handler_exe}\n"
                f"Slicer:   {slicer}"
            )
            r.destroy()
        except Exception as e:
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("SlicerBridge — Error", str(e))
            r.destroy()
        sys.exit(0)

    elif args and args[0] == "--uninstall":
        # Called by the GUI Uninstall button after UAC elevation
        if not is_admin():
            sys.exit(1)
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk(); r.withdraw()
        try:
            remove_registry()
            try:
                os.remove(CONFIG_FILE)
            except FileNotFoundError:
                pass
            messagebox.showinfo(
                "SlicerBridge — Uninstalled",
                f"All {len(PROTOCOLS)} protocol registrations removed.\n\n"
                "You can now delete SlicerBridge.exe from its install folder."
            )
        except Exception as e:
            messagebox.showerror("SlicerBridge — Error", str(e))
        r.destroy()
        sys.exit(0)

    elif args and args[0] == "--reset":
        # Developer helper: wipe config so the next launch shows Step 1 again.
        # Usage: SlicerBridge.exe --reset
        try:
            os.remove(CONFIG_FILE)
            print(f"Config deleted: {CONFIG_FILE}")
        except FileNotFoundError:
            print("No config found — already clean.")
        sys.exit(0)

    elif args and _is_protocol_uri(args[0]):
        # Silent protocol handler mode
        handle_protocol(args[0])

    else:
        # GUI configurator / installer
        show_gui()
