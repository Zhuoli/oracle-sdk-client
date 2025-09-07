"""Test package for OCI client."""

import sys
from pathlib import Path

# Add the src directory to the path so we can import the package
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
