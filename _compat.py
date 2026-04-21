"""
Import resolution for bundled shared modules.

curriculum_creator is a self-contained submodule — all its shared utilities
live in ./shared/. This file adds ./shared/ to sys.path so module-level
imports like `from chunk import chunk_text` resolve correctly.
"""

import sys
from pathlib import Path

_shared_dir = Path(__file__).resolve().parent / "shared"
if str(_shared_dir) not in sys.path:
    sys.path.insert(0, str(_shared_dir))
