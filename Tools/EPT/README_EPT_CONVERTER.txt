Droid Cloud EPT Converter Folder
================================

Put the fastest available EPT converter executable here so Droid Cloud can
convert LAS/LAZ point clouds into browser-ready EPT data.

Preferred:
- untwine.exe plus its required DLL files

Supported fallback tools:
- entwine.exe plus its required DLL files
- pdal.exe plus its required DLL files, with writers.ept support

Startup behavior:
- Dev and Live startup scripts automatically add this folder to PATH.
- If untwine.exe is present here, the backend uses it first.
- If not, the backend tries Entwine, then PDAL.

Environment variable overrides:
- UNTWINE_EXE=C:\full\path\to\untwine.exe
- ENTWINE_EXE=C:\full\path\to\entwine.exe
- PDAL_EXE=C:\full\path\to\pdal.exe

Important:
- The old PotreeConverter flow is intentionally removed.
- Point cloud output is now EPT only and opens through the Droid EPT viewer.
