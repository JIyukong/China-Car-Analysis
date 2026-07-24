from __future__ import annotations

import runpy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_WITH_BASE_INFRA_IMPORTS = [
    PROJECT_ROOT / "pipelines" / "dim" / "seed_dim_company_stock.py",
    PROJECT_ROOT / "pipelines" / "dim" / "seed_dim_oem_brand.py",
    PROJECT_ROOT / "pipelines" / "dim" / "seed_dim_date.py",
    PROJECT_ROOT / "pipelines" / "ingest" / "sales" / "load_16888_style_to_ods.py",
    PROJECT_ROOT / "pipelines" / "ingest" / "sales" / "load_16888_city_to_ods.py",
    PROJECT_ROOT / "pipelines" / "dim" / "build_sales_dimensions.py",
    PROJECT_ROOT / "pipelines" / "dim" / "refresh_dim_energy_vehicle.py",
]


class ScriptEntrypointImportTest(unittest.TestCase):
    def test_scripts_can_import_base_infra_when_run_directly(self) -> None:
        """验证各管道脚本可被 runpy 加载且 base_infra 导入正常。"""
        for script_path in SCRIPTS_WITH_BASE_INFRA_IMPORTS:
            with self.subTest(script=script_path.name):
                original_sys_path = sys.path[:]

                # 模拟直接执行：脚本目录在 sys.path 最前，项目根路径也保留
                sys.path.insert(0, str(PROJECT_ROOT))

                try:
                    runpy.run_path(str(script_path), run_name="__test__")
                except ModuleNotFoundError as exc:
                    self.fail(f"{script_path.name} 直接执行时导入失败: {exc}")
                finally:
                    sys.path = original_sys_path


if __name__ == "__main__":
    unittest.main()
