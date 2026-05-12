# ⬡ SlicerBridge

**One-click bridge between any 3D model website and your preferred slicer.**

Download a model from MakerWorld, Printables, Thingiverse — or anywhere else — and it opens directly in your slicer of choice, no matter what slicer the site natively supports.

---

## Table of Contents
- [How it works](#how-it-works)
- [Supported protocols](#supported-protocols)
- [Supported target slicers](#supported-target-slicers)
- [Installation](#installation)
  - [Option A — Download release (recommended)](#option-a---download-release-recommended)
  - [Option B — Build from source](#option-b---build-from-source)
- [Configuration](#configuration)
- [Tampermonkey (Printables Companion)](#tampermonkey-printables-companion)
  - [Planned](#planned)
- [⚠️ Windows Defender False Positive](#windows-defender-false-positive)
  - [Why does this happen?](#why-does-this-happen)
  - [How to fix it](#how-to-fix-it)
- [License](#license)

## How it works

Sites like MakerWorld and Printables use custom URL protocols (`bambustudio://`, `prusa3d://`, `cura://`, etc.) to open models in specific slicers. SlicerBridge hijacks all of those protocols in the Windows registry and routes every model to **your** slicer instead.

```
Website button click
        │
        ▼
bambustudio://open?file=https://...
        │
  Windows registry
        │
        ▼
  SlicerBridge.exe         ← intercepts any protocol
        │
   downloads file
        │
        ▼
  YourSlicer.exe model.3mf  ← opens in your actual slicer
```

---

## Supported protocols

| Protocol | Site |
|---|---|
| `bambustudio://` | MakerWorld |
| `orcaslicer://` | MakerWorld, Printables |
| `prusaslicer://` | Printables, prusaslicer.com |
| `prusa3d://` | Printables |
| `cura://` | **Thingiverse**, Cura Marketplace |
| `ideamaker://` | Raise3D Library |
| `simplify3d://` | Simplify3D |
| `flashprint://` | FlashForge |
| `thingiverse://` | Thingiverse direct |
| `creality://` | Creality Cloud |

---

## Supported target slicers

SlicerBridge auto-detects what you have installed:

- Creality Print
- UltiMaker Cura
- OrcaSlicer
- PrusaSlicer
- BambuStudio
- Simplify3D
- ideaMaker
- FlashPrint
- Chitubox

Don't see yours? Just browse to any `.exe` manually in the installer.

---

## Installation

### Option A — Download release (recommended)

1. Download `SlicerBridge.exe` from [Releases](../../releases)
2. Run it — if it detects you're in Downloads it will ask where to install
3. Pick your slicer
4. Click **Install** → approve UAC
5. Done. Click any model on MakerWorld / Printables / Thingiverse

### Option B — Build from source

Requirements: Python 3.10+, PyInstaller

```bash
git clone https://github.com/YOUR_USERNAME/SlicerBridge.git
cd SlicerBridge
pip install pyinstaller
build.bat
# or manually:
pyinstaller --onefile --noconsole main.py
```

The compiled exe will be at `dist\SlicerBridge.exe`.

---

## Configuration

Config is stored at `%APPDATA%\SlicerBridge\config.json`.  
Log is at `%APPDATA%\SlicerBridge\log.txt`.

To change your slicer later: just run `SlicerBridge.exe` again, pick a different one, click Install.

---

## Tampermonkey (Printables Companion)

To get the most out of SlicerBridge on Printables, install the companion Tampermonkey userscript. It injects a "⬡ Open in Slicer" button next to every folder, plus an "⬡ Open ALL in Slicer" button to grab everything at once.

> **[Install from GreasyFork](https://greasyfork.org/en/scripts/576211-slicerbridge)** (Recommended)
> *Alternatively, you can install it manually by copying the contents of `slicerbridge.user.js` into a new Tampermonkey script.*

### Planned
* 3d model sites support (renaming the native "Open in XXX" buttons to your actual slicer).

## ⚠️ Windows Defender False Positive

Windows Defender (and some other antivirus tools) may flag SlicerBridge as
`Trojan:Win32/Bearfoos.A!ml` and quarantine the executable. **This is a false positive.**

### Why does this happen?

SlicerBridge is built with [PyInstaller](https://pyinstaller.org/), which bundles
a Python runtime and your script into a single `.exe`. This packaging technique is
commonly used by legitimate tools — but it's also used by malware, so Windows Defender's
**machine-learning heuristic** (`!ml` suffix = ML-based detection, not a known signature)
flags it as suspicious.

Additional factors that contribute to the false positive:
- The `.exe` isnt signed
- It writes to the Windows Registry (`winreg`) to register a URL protocol handler
- It requests UAC elevation on first run
- It stores config in `%APPDATA%`

All of these are **required for SlicerBridge to function**, and the full source code is
available in this repository for anyone to audit.

### How to fix it

**Option A — Run the exclusion script (recommended):**
Download and run [`add_exclusion.ps1`](./add_exclusion.ps1) as Administrator.
It adds a Windows Defender exclusion for the SlicerBridge executable.

**Option B — Restore & exclude manually:**
1. Open **Windows Security → Virus & threat protection → Protection history**
2. Find the SlicerBridge entry and click **Restore**
3. Go to **Virus & threat protection settings → Exclusions → Add an exclusion**
4. Add the path to `SlicerBridge.exe`

## License

MPL 2.0
