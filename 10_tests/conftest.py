import sys
from pathlib import Path

ROOT = Path(r"D:\ttxt")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
