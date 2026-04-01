"""
Import resolution for pdf_pipeline modules.

When running inside the dutato repo, imports from ../pdf_pipeline.
When running standalone (as a submodule), imports from ./shared/.
"""

import sys
from pathlib import Path

_this_dir = Path(__file__).resolve().parent
_pdf_pipeline_dir = _this_dir.parent / "pdf_pipeline"

if _pdf_pipeline_dir.exists() and (_pdf_pipeline_dir / "extract.py").exists():
    # Running inside the dutato repo — use pdf_pipeline directly
    if str(_pdf_pipeline_dir) not in sys.path:
        sys.path.insert(0, str(_pdf_pipeline_dir))
else:
    # Standalone mode — use bundled shared/ copies
    _shared_dir = _this_dir / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
