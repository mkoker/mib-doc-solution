import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# local dep dir + source dir so `import extract` / `import rules` resolve offline
sys.path.insert(0, str(ROOT / ".venv/site"))
sys.path.insert(0, str(ROOT / "src"))
