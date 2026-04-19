#!/usr/bin/env python3
"""
SlicerBridge — Universal 3D printing slicer protocol bridge
https://github.com/YOUR_USERNAME/SlicerBridge

Build:  pyinstaller --onefile --noconsole main.py

Modes:
  SlicerBridge.exe                 → GUI installer / configurator
  SlicerBridge.exe <protocol://…>  → silent handler (download + open slicer)
  SlicerBridge.exe --register      → write registry (called automatically via UAC)
"""

import sys, os, json, shutil, subprocess, tempfile, traceback
import urllib.parse, urllib.request
import ctypes, winreg

# ═══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

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
    True if the exe is running from a temporary / download location
    and has not been installed to a permanent path yet.
    """
    exe = sys.executable.lower()
    suspicious = ["downloads", "desktop", "\\temp\\", "\\tmp\\",
                  "/downloads/", "/desktop/", "/temp/", "/tmp/"]
    already_installed = load_config().get("installed_exe", "")
    if already_installed and os.path.isfile(already_installed):
        return False
    return any(s in exe for s in suspicious)

def copy_to_install_dir(dest_dir: str) -> str:
    """
    Copies this exe to dest_dir\SlicerBridge.exe.
    Returns the full path of the installed exe.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, EXE_NAME)
    if os.path.abspath(sys.executable).lower() != dest.lower():
        shutil.copy2(sys.executable, dest)
    return dest

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
    log(f"\n{'─'*50}")
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

        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in VALID_EXT:
            log(f"Unknown extension '{ext}' -> using .3mf")
            ext = ".3mf"

        target = os.path.join(tempfile.gettempdir(), f"slicerbridge{ext}")
        log(f"Downloading -> {target}")

        req = urllib.request.Request(
            url,
            headers={"User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )}
        )
        with urllib.request.urlopen(req, timeout=30) as r, \
             open(target, "wb") as f:
            f.write(r.read())

        log("Downloaded OK. Launching slicer...")
        subprocess.Popen(
            [slicer, target],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        log("Slicer launched OK")

    except urllib.error.URLError as e:
        log(f"Download error: {e}\n{traceback.format_exc()}")
    except Exception as e:
        log(f"Error: {e}\n{traceback.format_exc()}")

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

    root = tk.Tk()
    root.title("SlicerBridge")
    root.geometry("700x660" if offer_install else "700x580")
    root.resizable(False, False)
    root.configure(bg=BG)

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
        tk.Label(root, text="Step 1 — Install location",
                 bg=BG, fg=FG, font=FONT_LG).pack(anchor="w", padx=28)
        tk.Label(root,
                 text="SlicerBridge will copy itself here so it stays after you clean Downloads.",
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

        # Determine where the handler exe will live
        if offer_install:
            dest_dir = install_dir_var.get().strip()
            if not dest_dir:
                status_var.set("⚠  Please enter an install directory.")
                status_lbl.configure(fg=RED_C)
                return
            try:
                handler_exe = copy_to_install_dir(dest_dir)
            except Exception as e:
                status_var.set(f"⚠  Could not copy to {dest_dir}: {e}")
                status_lbl.configure(fg=RED_C)
                return
        else:
            handler_exe = sys.executable

        # Save config, then write registry (needs UAC)
        save_config({
            "slicer_path":   chosen_slicer,
            "installed_exe": handler_exe,
            "handler_exe":   handler_exe,
        })

        if not is_admin():
            status_var.set("Requesting admin rights (UAC)…")
            status_lbl.configure(fg=YELLOW)
            root.update()
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas",
                handler_exe, "--register",
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

    def mkbtn(text, cmd, primary=False):
        tk.Button(
            btn_frame, text=text, command=cmd,
            bg=ACCENT if primary else BORDER,
            fg=BG if primary else FG,
            font=("Segoe UI", 10, "bold") if primary else FONT,
            relief="flat", cursor="hand2", padx=16, pady=7,
            activebackground=GREEN if primary else GRAY,
            activeforeground=BG
        ).pack(side="left", padx=5)

    mkbtn("  ✓  Install  ", do_install, primary=True)
    mkbtn("Open log",
          lambda: os.startfile(LOG_FILE) if os.path.isfile(LOG_FILE)
          else status_var.set("No log yet."))
    mkbtn("Config folder",
          lambda: (os.makedirs(CONFIG_DIR, exist_ok=True),
                   os.startfile(CONFIG_DIR)))

    # ── Footer ────────────────────────────────────────────────────────
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=28, pady=(12, 4))
    tk.Label(
        root,
        text=f"Keep SlicerBridge.exe in the install location — "
             f"the registry points directly to it.",
        bg=BG, fg=FG_DIM, font=FONT_SM
    ).pack(anchor="w", padx=28, pady=(0, 14))

    root.mainloop()

# ═══════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

def _is_protocol_uri(s: str) -> bool:
    return any(s.startswith(p + "://") for p in PROTOCOLS)

if __name__ == "__main__":
    args = sys.argv[1:]

    if args and args[0] == "--register":
        # Called automatically by the GUI after UAC elevation
        if not is_admin():
            sys.exit(1)
        cfg = load_config()
        exe = cfg.get("handler_exe", sys.executable)
        try:
            write_registry(exe)
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showinfo(
                "SlicerBridge",
                f"Installation complete!\n\n"
                f"{len(PROTOCOLS)} protocols registered.\n\n"
                f"Handler:  {exe}\n"
                f"Slicer:   {cfg.get('slicer_path', '???')}"
            )
            r.destroy()
        except Exception as e:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("SlicerBridge — Error", str(e))
            r.destroy()
        sys.exit(0)

    elif args and _is_protocol_uri(args[0]):
        # Silent protocol handler mode
        handle_protocol(args[0])

    else:
        # GUI configurator / installer
        show_gui()