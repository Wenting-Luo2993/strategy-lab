import sys
from pathlib import Path

"""Pytest configuration utilities.

Ensures the project root (python/) is on sys.path so that the package
`src` can be imported as a top-level module (i.e. `from src...`).
We purposefully add the parent directory of `src`, not `src` itself.

If we add the `src` directory directly, Python would then look for a
subdirectory `src/src`, causing `ModuleNotFoundError: src`.
"""

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # .../strategy-lab/python
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Optionally expose the absolute path to tests needing filesystem fixtures
TESTS_DIR = Path(__file__).parent
