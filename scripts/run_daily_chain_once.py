"""兼容旧的日链手跑命令。"""

import argparse
import sys
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行日链增量采集")
    parser.add_argument("--date")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scheduler.runner import main

    options = ["--chain", "daily"]
    if args.date:
        options.extend(["--period", args.date])
    if args.plan:
        options.append("--plan")
    sys.exit(main(options))
