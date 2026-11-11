"""旧 SQL 执行入口。日常运行账号不用于执行任意 SQL 文件。"""

import sys

if __name__ == "__main__":
    print("已停用任意 SQL 文件执行。数据库维护请由管理员在独立会话中操作。", file=sys.stderr)
    sys.exit(2)
