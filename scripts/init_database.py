"""旧初始化入口。数据库维护由管理员单独操作。"""

import sys

if __name__ == "__main__":
    print("已停用一键初始化。现有库请按 README 运行 ETL。", file=sys.stderr)
    sys.exit(2)
