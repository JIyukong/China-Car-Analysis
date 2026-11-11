"""离线显示调度与 ETL 步骤。"""

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from scheduler.runner import main

    sys.exit(main(["--plan"]))
