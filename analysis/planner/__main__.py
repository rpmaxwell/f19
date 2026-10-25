import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import main  # noqa: E402

main()
