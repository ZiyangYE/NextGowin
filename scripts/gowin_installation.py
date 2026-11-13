"""Locate the hash-checked SDK, including an offline handoff bundle."""
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BUNDLED=ROOT/'runtime_bundle/gowin/IDE'
SDK=Path(os.environ.get('NEXTGOWIN_SDK',str(BUNDLED if BUNDLED.is_dir() else Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE')))).resolve()
