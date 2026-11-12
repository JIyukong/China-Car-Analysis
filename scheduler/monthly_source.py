"""Validate one fresh 16888 snapshot and reconcile its business keys with ODS."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from contextlib import nullcontext
from importlib import import_module
from pathlib import Path
from tempfile import mkdtemp

from sqlalchemy import text
from sqlalchemy.engine import Connection

from scheduler.runtime import SkipTask, business_period

logger = logging.getLogger(__name__)

_SOURCES = {
    "style": ("ods_sales_monthly_model", "manufacturer", "manufacturer_name_raw", 400),
    "city": ("ods_sales_monthly_model_city", "city", "city_name_raw", 20_000),
}
_UNSIGNED_INT = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)")
_MAX_UNSIGNED = 2**32 - 1


class SourceRevisionChanged(ValueError):
    """ODS was changed after source acceptance."""


def _crawl_snapshot(chain: str, period: str, path: Path) -> None:
    from scheduler.jobs.crawl_16888_job import crawl_snapshot

    crawl_snapshot(chain, period, path)


def _snapshot_files(loader, chain: str, period: str, from_csv: bool) -> tuple[Path, Path]:
    stem = f"{chain}_{period}"
    if from_csv:
        data_dir = loader.data_dir()
        return data_dir / f"{stem}.csv", data_dir / f"{stem}.manifest.json"

    attempts = loader.data_dir() / "snapshots" / period
    attempts.mkdir(parents=True, exist_ok=True)
    attempt = Path(mkdtemp(dir=attempts))
    _crawl_snapshot(chain, period, attempt)
    return attempt / f"{stem}.csv", attempt / "manifest.json"


def _validated_csv(csv_path: Path, manifest_path: Path, chain: str, period: str) -> bytes:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"缺少抓取凭据 {manifest_path}，请重新完整抓取或恢复成对的 CSV 和 manifest") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"抓取凭据格式错误 {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("chain") != chain or manifest.get("period") != period:
        raise ValueError(f"抓取凭据的来源或月份不匹配: {manifest_path}")

    raw_csv = csv_path.read_bytes()
    if hashlib.sha256(raw_csv).hexdigest() != manifest.get("csv_sha256"):
        raise ValueError(f"CSV 的 SHA-256 与抓取凭据不符: {csv_path}")
    reader = csv.DictReader(io.StringIO(raw_csv.decode("utf-8-sig"), newline=""), strict=True)
    _, key_column, _, warn_minimum = _SOURCES[chain]
    required = {"month", "model_name", key_column, "sales_volume"}
    header = reader.fieldnames or []
    if not required.issubset(header) or len(header) != len(set(header)):
        raise ValueError(f"CSV 缺少必要字段或表头重复: {csv_path}")

    status = manifest.get("status")
    source_total = manifest.get("source_total")
    page_rows = manifest.get("page_rows")
    if (status == "unpublished" and source_total == 0 and page_rows == []
            and next(reader, None) is None):
        raise SkipTask(f"16888 {chain} {period} 页面明确显示暂无数据: {manifest_path}")
    if status != "published" or manifest.get("pagination_complete") is not True:
        raise ValueError(f"未验证全部分页，请重新完整抓取: {manifest_path}")
    if (type(source_total) is not int or source_total <= 0 or not isinstance(page_rows, list)
            or not page_rows or any(type(count) is not int or count <= 0 for count in page_rows)
            or sum(page_rows) != source_total):
        raise ValueError(f"分页行数与站点总数无法核对覆盖: {manifest_path}")

    key_lines = {}
    for row in reader:
        location = f"{csv_path}:{reader.line_num}"
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"CSV 列数异常: {location}")
        if row["month"].strip() != period:
            raise ValueError(f"月份与目标月 {period} 不符: {location}")
        key = (row["model_name"].strip(), row[key_column].strip())
        if not all(key) or len(key[0]) > 128 or len(key[1]) > (128 if chain == "style" else 64):
            raise ValueError(f"业务键缺失或超过 ODS 字段长度: {location}")
        if key in key_lines:
            raise ValueError(
                f"业务键重复 {key!r}: {location} 与第 {key_lines[key]} 行重复，请检查站点分页或同名车型"
            )
        key_lines[key] = reader.line_num
        sales = row["sales_volume"].strip()
        if not _UNSIGNED_INT.fullmatch(sales) or int(sales.replace(",", "")) > _MAX_UNSIGNED:
            raise ValueError(f"销量无效 {sales!r}: {location}")
        rank = row.get("rank", "").strip()
        if rank and (not _UNSIGNED_INT.fullmatch(rank) or int(rank.replace(",", "")) > _MAX_UNSIGNED):
            raise ValueError(f"排名无效 {rank!r}: {location}")
        if any(len(row.get(field, "").strip()) > 64 for field in ("price_range", "level")):
            raise ValueError(f"价格或级别超过 ODS 字段长度: {location}")

    if len(key_lines) != source_total:
        raise ValueError(f"CSV 覆盖不足或超出站点总数: {csv_path}，有效业务键 {len(key_lines)}/{source_total}")
    if len(key_lines) < warn_minimum:
        logger.warning("16888 %s %s 已核对站点覆盖，但 %d 行低于历史参考 %d 行", chain, period, len(key_lines), warn_minimum)
    return raw_csv


def _content_fields(chain: str) -> tuple[str, ...]:
    _, _, key_column, _ = _SOURCES[chain]
    fields = ("model_name_raw", key_column, "sales_volume", "rank_no", "price_range_raw")
    return (*fields, "level_raw") if chain == "city" else fields


def _content(records, fields: tuple[str, ...]) -> dict:
    return {(row[fields[0]], row[fields[1]]): tuple(row[field] for field in fields[2:]) for row in records}


def _revision_digest(chain: str, period: str, content: dict) -> str:
    payload = json.dumps((chain, period, sorted(content.items())), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ods_content(conn, loader, chain: str, period: str, *, lock: bool) -> dict:
    table = _SOURCES[chain][0]
    fields = _content_fields(chain)
    query = f"SELECT {', '.join(fields)} FROM {table} WHERE source = :source AND stat_month = :period"
    if lock:
        query += " FOR UPDATE"
    params = {"source": loader.SOURCE_TAG, "period": loader.parse_month(period)}
    return _content(conn.execute(text(query), params).mappings(), fields)


def _reconcile_ods(loader, chain: str, period: str, rows: list[dict]) -> str:
    fields = _content_fields(chain)
    expected = _content(rows, fields)
    with loader.engine.begin() as conn:
        existing = _ods_content(conn, loader, chain, period, lock=True)
        extra = existing.keys() - expected.keys()
        if extra:
            sample = sorted(extra)[:5]
            raise ValueError(
                f"{chain} {period} 有 {len(extra)} 个 ODS 历史业务键未出现在本次完整榜单，"
                f"请核对来源是否撤回或键名是否变化。示例: {sample!r}"
            )
        if existing != expected:
            changed = [row for row in rows if existing.get((row[fields[0]], row[fields[1]]))
                       != expected[(row[fields[0]], row[fields[1]])]]
            loader.upsert_rows(changed, connection=conn)
            if _ods_content(conn, loader, chain, period, lock=True) != expected:
                raise ValueError(f"{chain} {period} ODS 回读与榜单不一致，请检查业务键冲突或字段转换")
    return _revision_digest(chain, period, expected)


def _loader(chain: str):
    if chain not in _SOURCES:
        raise ValueError(f"无效月度数据链: {chain!r}")
    return import_module(f"pipelines.ingest.sales.load_16888_{chain}_to_ods")


def verify_ods_revision(chain: str, period: str, digest: str, *, connection: Connection | None = None) -> None:
    """Check persisted ODS content against the accepted digest, locking within a supplied transaction."""
    period = business_period(chain, period)
    loader = _loader(chain)
    with (loader.engine.connect() if connection is None else nullcontext(connection)) as conn:
        persisted = _ods_content(conn, loader, chain, period, lock=connection is not None)
    if _revision_digest(chain, period, persisted) != digest:
        raise SourceRevisionChanged(f"{chain} {period} ODS 内容与已验收版本不一致，请重新抓取并核对该月份")


def collect_month(chain: str, period: str, *, from_csv: bool = False, accepted_digest: str | None = None) -> str:
    """Return the verified source digest after atomic, incremental ODS reconciliation."""
    period = business_period(chain, period)
    loader = _loader(chain)
    csv_path, manifest_path = _snapshot_files(loader, chain, period, from_csv)
    try:
        raw_csv = _validated_csv(csv_path, manifest_path, chain, period)
    except SkipTask as exc:
        if accepted_digest is not None:
            raise ValueError(f"{chain} {period} 已验收的数据现在显示为空，请核对站点: {manifest_path}") from exc
        if from_csv:
            raise ValueError(f"重放文件没有已发布的数据: {manifest_path}") from exc
        raise
    except (UnicodeError, csv.Error) as exc:
        raise ValueError(f"CSV 格式异常 {csv_path}: {exc}") from exc
    rows = list(loader.read_rows(csv_path, raw_csv=raw_csv))
    logger.info("16888 %s %s 已验证 %s（%d 行）", chain, period, csv_path, len(rows))
    return _reconcile_ods(loader, chain, period, rows)
