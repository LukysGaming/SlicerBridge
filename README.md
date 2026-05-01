# ⬡ SlicerBridge

**One-click bridge between any 3D model website and your preferred slicer.**

Download a model from MakerWorld, Printables, Thingiverse — or anywhere else — and it opens directly in your slicer of choice, no matter what slicer the site natively supports.

---

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

## License

MPL 2.0
