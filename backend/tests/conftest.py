"""pytest configuration - automatically adds the backend directory to the Python path."""
import sys
from pathlib import Path

# Add the backend directory to the Python path to avoid manual sys.path.insert in each test file
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
