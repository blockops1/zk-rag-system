"""pytest conftest — add shared/ to sys.path so `import api_server` works from repo root.

The service runs with WorkingDirectory=./shared, so import resolution
works there. Tests run from repo root, so we replicate that path setup here.
"""

import sys
from pathlib import Path

# ./shared -> add to sys.path so `import api_server` resolves
_SHARED_DIR = Path(__file__).parent.parent / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))
