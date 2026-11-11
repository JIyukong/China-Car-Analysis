"""兼容旧探活命令，提醒改为检查进程服务状态。"""

import sys

if __name__ == "__main__":
    print("数据库心跳探活已停用。请检查 NSSM 或 systemd 服务状态和运行日志。", file=sys.stderr)
    sys.exit(2)
