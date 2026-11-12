"""ETL 存储过程调度任务薄壳。

每个函数对应一个数据仓库内的集合运算步骤，
业务逻辑均封装在 MySQL 存储过程中，Python 仅负责 CALL 和结果上报。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from scheduler.runtime import SkipTask, TaskResult

logger = get_logger(__name__)

from pipelines.dim.dimension_readiness import (  # noqa: E402
    check_dimension_readiness,
    generate_missing_dimension_csv,
)

# business_key 合法格式：YYYY-MM 或 YYYY-MM-DD
_BK_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _to_date(business_key: str | None) -> date | None:
    """把 YYYY-MM 或 YYYY-MM-DD 业务键转 date；空值/非法值返回 None。"""
    if not business_key:
        return None
    if not _BK_RE.match(business_key):
        raise ValueError(f"非法 business_key: {business_key!r}，期望 YYYY-MM 或 YYYY-MM-DD")
    try:
        return datetime.strptime(business_key[:7], "%Y-%m").date()
    except ValueError as exc:
        raise ValueError(f"非法 business_key 月份: {business_key!r}") from exc


def _alert_dimension_not_ready(
    task_id: str,
    business_key: str | None,
    human_report: str,
    missing_csv: str,
) -> None:
    """维度未就绪时第一时间推送飞书/钉钉，随后由调用方抛 SkipTask。

    run_id 使用稳定的 ``dim-gate:<business_key>``，配合 alert 模块的 10 分钟
    去重，避免同日 cron 重复探测时刷屏；补完维次日重跑通过后会另发恢复通知。
    """
    from scheduler import alert

    alert.send_alert(
        "WARNING",
        task_id,
        f"dim-gate:{business_key or 'all'}",
        "⚠️ 维度未就绪，DWD 之后链路已暂停\n"
        "原因：存在缺失 能源类型/车型大类等手工属性的车型（B 类，需人工补维）。\n"
        "处理：请按下方清单补全手工 CSV（dim_energy_vehicle 等）并重新导入，"
        "次日 cron 会自动重跑；补维完成前 DWS 聚合/生命周期刷新不会执行。\n"
        "注意：DWD 明细已落库（保留 NULL + dq_flag），不会重复抓取 ODS。\n\n"
        f"【就绪报告】\n{human_report}\n\n"
        f"【待补维清单】\n{missing_csv}",
        business_key=business_key,
    )


def _raise_if_dimensions_not_ready(
    report: object,
    business_key: str | None,
    context: str,
    task_id: str,
) -> None:
    """若维度未就绪，推送告警并抛 SkipTask 阻断后续链路。"""
    if report.is_ready():
        return
    human_report = report.to_human()
    missing_csv = generate_missing_dimension_csv(report, target_month=business_key)
    logger.warning(
        "%s 维度未就绪，跳过\n%s\n%s",
        context,
        human_report,
        missing_csv,
    )
    _alert_dimension_not_ready(task_id, business_key, human_report, missing_csv)
    raise SkipTask(f"{context}: {human_report}\n{missing_csv}")


def build_dimensions(business_key: str | None = None) -> TaskResult:
    """DIM 派生：ods_sales_monthly_model → dim_model。

    新车型/品牌/OEM 可自动入维（A 类），此处只发"新增实体"通知，不阻断链路。
    能源类型/车型大类等手工属性（B 类）的缺失，由 DWD 清洗后的
    dimension_readiness 门控阻断 DWS/lifecycle，职责分离。
    """
    from scheduler import alert
    from pipelines.dim.build_sales_dimensions import run_build_sales_dimensions

    target_month = _to_date(business_key)
    result = run_build_sales_dimensions(target_month)
    n = result.get("affected_rows", 0)
    new_models = result.get("new_models") or []

    if new_models:
        preview = "、".join(new_models[:30])
        more = "" if len(new_models) <= 30 else f"……等共 {len(new_models)} 个"
        month_label = target_month.strftime("%Y-%m") if target_month else "ALL"
        try:
            alert.send_alert(
                "INFO",
                "build_dimensions",
                f"build_dimensions:new_models:{month_label}",
                "🔔 月度维度刷新：检测到新增车型（已自动入维，不阻断链路）\n"
                f"目标月份：{month_label}\n"
                f"新增数量：{len(new_models)}\n"
                f"车型清单：{preview}{more}\n"
                "说明：新车型已自动写入 dim_model。若其 能源类型/"
                "车型大类尚未补全，DWD 清洗后的维度门控会阻断 DWS 聚合并告警，"
                "届时请补手工 CSV 后次日自动重跑。",
            )
        except Exception:  # noqa: BLE001
            logger.exception("发送新增车型通知失败（不影响链路）")

    return TaskResult(
        success=True,
        affected_rows=n,
        message=f"dim_model 派生 {n} 条，新增车型 {len(new_models)} 个",
    )



def run_dwd_clean(business_key: str | None = None) -> TaskResult:
    """DWD 清洗：ods_sales_monthly_model → dwd_sales_monthly_model。"""
    from pipelines.staging.clean_sales_dwd import run_clean_sales_dwd

    n = run_clean_sales_dwd(_to_date(business_key))
    report = check_dimension_readiness(business_key)
    _raise_if_dimensions_not_ready(report, business_key, "DWD 清洗后", "run_dwd_clean")
    return TaskResult(
        success=True,
        affected_rows=n,
        message=f"DWD 清洗新增 {n} 条，维度就绪",
    )


def _check_city_dim_model_ready(target_month: date | None) -> None:
    """跨链依赖预检：city DWD 依赖 style 链产出的 dim_model。

    轻量检查目标月 city ODS 是否有大量未匹配车型。若存在，说明 style 链
    尚未跑完，抛 :class:`SkipTask` 让次日 city cron 自动重试。

    注意：这里只做"跨链时序"判断（重试即可解决）；真正的零容忍预检、
    飞书告警和异常阻断在 run_clean_sales_city_dwd 内部完成。
    """
    month_clause = ""
    params: dict = {}
    if target_month is not None:
        month_clause = "AND ods.stat_month = :p_month"
        params["p_month"] = target_month

    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM ods_sales_monthly_model_city ods
                LEFT JOIN dim_model dm
                    ON dm.model_name = TRIM(ods.model_name_raw)
                WHERE ods.sales_volume IS NOT NULL
                  AND ods.sales_volume > 0
                  AND ods.model_name_raw IS NOT NULL
                  AND TRIM(ods.model_name_raw) <> ''
                  {month_clause}
                  AND dm.id IS NULL
                """
            ),
            params,
        ).fetchone()
    unmatched = int(row[0] or 0) if row else 0
    if unmatched > 0:
        raise SkipTask(
            f"dim_model 未就绪：目标月 city ODS 有 {unmatched} 行未匹配车型，"
            f"style 链可能尚未跑完，次日自动重试"
        )


def run_dwd_city_clean(business_key: str | None = None) -> TaskResult:
    """DWD 清洗：ods_sales_monthly_model_city → dwd_sales_monthly_model_city。"""
    from pipelines.staging.clean_sales_city_dwd import run_clean_sales_city_dwd

    target_month = _to_date(business_key)
    # 跨链依赖：city DWD 关联 dim_model，若 style 链未跑完则跳过，次日重试
    _check_city_dim_model_ready(target_month)
    n = run_clean_sales_city_dwd(target_month)

    # 后置检查：城市维度 area / vehicle_subtype 覆盖率
    report = check_dimension_readiness(business_key)
    _raise_if_dimensions_not_ready(report, business_key, "城市 DWD 清洗后", "run_dwd_city_clean")

    return TaskResult(
        success=True,
        affected_rows=n,
        message=f"城市 DWD 清洗新增 {n} 条，维度就绪",
    )


def update_lifecycle(business_key: str | None = None) -> TaskResult:
    """维度生命周期刷新：dim_oem / dim_model 活跃位。"""
    from pipelines.dim.update_lifecycle import run_update_lifecycle

    n = run_update_lifecycle(_to_date(business_key))
    return TaskResult(success=True, affected_rows=n, message=f"生命周期更新影响 {n} 行")


def run_dws_agg(business_key: str | None = None) -> TaskResult:
    """DWS 聚合：公司 + 品牌两层月度销量。"""
    from pipelines.serve.agg_sales_monthly import run_agg_sales_monthly

    report = check_dimension_readiness(business_key)
    _raise_if_dimensions_not_ready(report, business_key, "DWS 聚合前", "run_dws_agg")

    n = run_agg_sales_monthly(_to_date(business_key))
    return TaskResult(success=True, affected_rows=n, message=f"DWS 聚合影响 {n} 行")
