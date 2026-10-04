# PyInstaller-Bauplan für die Windows- und die macOS-Fassung.
#
#     pyinstaller packaging/sl-office.spec --noconfirm
#
# Ergebnis unter Windows: dist/SL-Office/SL-Office.exe samt Ordner. Ein Ordner
# statt einer einzelnen EXE: so startet SL-Office schneller und Virenscanner
# beanstanden es seltener. Das Setup baut anschließend packaging/sl-office.iss.
#
# Ergebnis unter macOS: zusätzlich dist/SL-Office.app; die Versionsnummer kommt
# aus der Umgebungsvariablen SL_OFFICE_VERSION. Das Disk-Image baut anschließend
# packaging/macos-dmg.sh.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

daten = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "static"), "static"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "migrations"), "migrations"),
]

versteckt = (
    collect_submodules("sl_office")
    + collect_submodules("reportlab.graphics.barcode")
    + ["waitress", "openpyxl", "pyotp", "flask_migrate", "alembic", "sqlalchemy.dialects.sqlite"]
    # Die Migrationen liegen als Dateien bei und werden erst zur Laufzeit
    # geladen; was sie importieren, sieht PyInstaller darum nicht von selbst.
    + collect_submodules("alembic")
    + ["logging.config", "zoneinfo", "tzdata"]
)

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    datas=daten,
    hiddenimports=versteckt,
    excludes=["gunicorn", "pytest", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SL-Office",
    console=False,
    icon=str(ROOT / "packaging" / "sl-office.ico") if (ROOT / "packaging" / "sl-office.ico").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SL-Office")

if sys.platform == "darwin":
    version = os.environ.get("SL_OFFICE_VERSION", "0.0.0")
    app = BUNDLE(
        coll,
        name="SL-Office.app",
        icon=str(ROOT / "packaging" / "sl-office.icns") if (ROOT / "packaging" / "sl-office.icns").exists() else None,
        bundle_identifier="de.sl-office.desktop",
        version=version,
        info_plist={
            "CFBundleDisplayName": "SL-Office",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
            "CFBundleDevelopmentRegion": "de",
        },
    )
