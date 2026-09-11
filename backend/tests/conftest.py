"""Put `backend/` on the import path.

The app imports as `ingest.…`, `core.…`, `api.…` — rooted at `backend/`, which
is the container's WORKDIR. Tests run from the repo root as often as not, so
the path is set here rather than depending on where pytest was invoked.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
