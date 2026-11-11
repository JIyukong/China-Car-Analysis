"""兼容旧的车型月链手跑命令，使用本地 CSV。"""

import argparse
import sys
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从本地 CSV 运行车型月链")
    parser.add_argument("--month")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scheduler.runner import main

    options = ["--chain", "style", "--from-csv"]
    if args.month:
        options.extend(["--period", args.month])
    if args.plan:
        options.append("--plan")
    sys.exit(main(options))
