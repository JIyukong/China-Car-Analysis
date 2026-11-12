"""飞书多维表格补录任务的传输、校验与应用。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from base_infra.db import engine
from pipelines.dim.manual_refresh import apply_feishu_dimension
from pipelines.dim.refresh_dim_area import ALLOWED_CITY_LEVELS, ALLOWED_PROVINCE_TYPES
from pipelines.dim.refresh_dim_energy_vehicle import ALLOWED_ENERGY_TYPES, ALLOWED_VEHICLE_TYPES
from pipelines.dim.refresh_dim_model_ads import normalize_listed_at

TASK_KEY = "任务键"
KIND = "维度类型"
BUSINESS_KEY = "业务键"
STATUS = "状态"
PERIODS = "月份"
CONTEXT = "缺失信息"
INPUT = "补录数据"
FEEDBACK = "处理结果"
PENDING = "待补录"
SUBMITTED = "已提交"
REJECTED = "校验退回"
APPLIED = "已应用"

_OPEN_API = "https://open.feishu.cn/open-apis"
_HTTP_TIMEOUT = 15
_AREA_TEXT_FIELDS = {
    "city_code": 6, "city_name": 64, "city_level": 16, "province_code": 6,
    "province_name": 32, "province_short_name": 16, "province_type": 16,
    "region_code": 8, "region_name": 16,
}
_REGIONS = {"EC": "华东", "SC": "华南", "NC": "华北", "CC": "华中", "SW": "西南", "NW": "西北", "NE": "东北"}
_KEY_LENGTHS = {"energy": 128, "ads": 128, "area": 32, "subtype": 32, "ownership": 128}
_ENERGY_FIELDS = ("energy_type", "vehicle_type")
_ADS_FIELDS = ("ads_proportion", "ads_supplier", "ads_supplier_mode", "listed_at")
_FORM_FIELDS = {
    "energy": _ENERGY_FIELDS, "ads": _ADS_FIELDS, "area": tuple(_AREA_TEXT_FIELDS),
    "subtype": ("subtype_code", "type_name"), "ownership": ("company_code", "brand_code"),
}


class FeishuError(RuntimeError):
    """飞书请求失败，异常消息可写入调度日志。"""


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, app_token: str, table_id: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._records_path = f"/bitable/v1/apps/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records"
        self._token = ""
        self._expires_at = 0.0

    @staticmethod
    def _request(
        method: str, path: str, *, body: dict | None = None, token: str = "", params: dict | None = None,
    ) -> dict:
        url = _OPEN_API + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(url, method=method, headers=headers, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(req, timeout=_HTTP_TIMEOUT) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            raise FeishuError(f"飞书 HTTP 请求失败（{exc.code}）") from None
        except (URLError, OSError):
            raise FeishuError("飞书连接失败或请求超时") from None
        except (ValueError, UnicodeError):
            raise FeishuError("飞书响应无法解析") from None

        code = result.get("code") if isinstance(result, dict) else None
        if type(code) is not int:
            raise FeishuError("飞书响应缺少有效业务状态码")
        if code != 0:
            raise FeishuError(f"飞书接口失败（业务码 {code}）")
        return result

    def _access_token(self) -> str:
        now = monotonic()
        if now >= self._expires_at:
            result = self._request("POST", "/auth/v3/tenant_access_token/internal", body={
                "app_id": self._app_id, "app_secret": self._app_secret,
            })
            token = result.get("tenant_access_token")
            expires_in = result.get("expire")
            if not isinstance(token, str) or not token or type(expires_in) is not int or expires_in <= 0:
                raise FeishuError("飞书响应缺少有效租户凭证")
            self._token = token
            self._expires_at = now + max(0, expires_in - 60)
        return self._token

    def _app_data(self, method: str, path: str, *, body: dict | None = None, params: dict | None = None) -> dict:
        result = self._request(method, path, body=body, params=params, token=self._access_token())
        data = result.get("data")
        if not isinstance(data, dict):
            raise FeishuError("飞书响应缺少数据")
        return data

    def _call(self, method: str, path: str = "", *, body: dict | None = None, params: dict | None = None) -> dict:
        return self._app_data(method, self._records_path + path, body=body, params=params)

    def send_chat_text(self, chat_id: str, text: str) -> str:
        data = self._app_data(
            "POST", "/im/v1/messages", params={"receive_id_type": "chat_id"},
            body={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        )
        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise FeishuError("飞书群消息响应缺少消息 ID")
        return message_id

    @staticmethod
    def _record(record: object) -> dict:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("record_id"), str)
            or not record["record_id"]
            or not isinstance(record.get("fields"), dict)
        ):
            raise FeishuError("飞书响应缺少有效记录")
        return record

    def records(self) -> list[dict]:
        records = []
        seen_tokens: set[str] = set()
        params: dict[str, int | str] = {"page_size": 500}
        while True:
            data = self._call("GET", params=params)
            items = data.get("items")
            if not isinstance(items, list) or type(data.get("has_more")) is not bool:
                raise FeishuError("飞书响应缺少分页数据")
            records.extend(self._record(item) for item in items)
            if not data["has_more"]:
                return records
            token = data.get("page_token")
            if not isinstance(token, str) or not token or token in seen_tokens:
                raise FeishuError("飞书响应的分页凭证无效")
            seen_tokens.add(token)
            params["page_token"] = token

    def get(self, record_id: str) -> dict:
        return self._record(self._call("GET", f"/{quote(record_id, safe='')}").get("record"))

    def create(self, fields: dict, *, client_token: str | None = None) -> dict:
        params = {"client_token": client_token} if client_token else None
        return self._record(self._call("POST", body={"fields": fields}, params=params).get("record"))

    def update(self, record_id: str, fields: dict) -> None:
        self._call("PUT", f"/{quote(record_id, safe='')}", body={"fields": fields})


class FeishuCliClient(FeishuClient):
    """Use the deployment user's CLI keychain and Base v3 app permissions."""

    def __init__(self, cli_path: str, app_token: str, table_id: str):
        self._cli_path = cli_path
        self._records_path = f"/base/v3/bases/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records"

    def _app_data(self, method: str, path: str, *, body: dict | None = None, params: dict | None = None) -> dict:
        args = [self._cli_path, "api", method, "/open-apis" + path, "--as", "bot"]
        if params:
            args.extend(("--params", json.dumps(params)))
        if body is not None:
            args.extend(("--data", "-"))
        try:
            result = subprocess.run(
                args, input=json.dumps(body, ensure_ascii=False) if body is not None else None,
                capture_output=True, encoding="utf-8", timeout=30, check=False,
                env=dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1", LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1"),
            )
            envelope = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
            raise FeishuError("飞书 CLI 请求失败或响应无法解析") from None
        if not isinstance(envelope, dict):
            raise FeishuError("飞书 CLI 响应格式错误")
        if result.returncode or envelope.get("ok") is not True:
            error = envelope.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            suffix = f"（业务码 {code}）" if type(code) is int else ""
            raise FeishuError(f"飞书 CLI 请求失败{suffix}")
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise FeishuError("飞书 CLI 响应缺少数据")
        return data

    @staticmethod
    def _matrix(data: dict) -> list[dict]:
        ids, fields, rows = (data.get(name) for name in ("record_id_list", "fields", "data"))
        if not all(isinstance(values, list) for values in (ids, fields, rows)) or len(ids) != len(rows):
            raise FeishuError("飞书 Base 响应缺少有效记录矩阵")
        if (
            any(not isinstance(field, str) or not field for field in fields)
            or len(set(fields)) != len(fields)
            or any(not isinstance(row, list) or len(row) != len(fields) for row in rows)
        ):
            raise FeishuError("飞书 Base 响应的字段和记录无法对齐")
        return [
            FeishuClient._record({"record_id": record_id, "fields": dict(zip(fields, row, strict=True))})
            for record_id, row in zip(ids, rows, strict=True)
        ]

    def records(self) -> list[dict]:
        records = []
        offset = 0
        while True:
            data = self._call("GET", params={"limit": 200, "offset": offset})
            page = self._matrix(data)
            records.extend(page)
            if data.get("has_more") is False:
                return records
            next_offset = data.get("next_offset", offset + len(page))
            if data.get("has_more") is not True or type(next_offset) is not int or next_offset <= offset:
                raise FeishuError("飞书 Base 响应的分页偏移无效")
            offset = next_offset

    def get(self, record_id: str) -> dict:
        rows = self._matrix(self._call("POST", "/batch_get", body={"record_id_list": [record_id]}))
        if len(rows) != 1 or rows[0]["record_id"] != record_id:
            raise FeishuError("飞书 Base 未返回指定记录")
        return rows[0]

    @staticmethod
    def _cells(fields: dict) -> dict:
        return {key: [value] if key == STATUS else value for key, value in fields.items()}

    def create(self, fields: dict, *, client_token: str | None = None) -> dict:
        # Base v3 can acknowledge a create without a top-level record ID.
        task_key = cell_text(fields, TASK_KEY)
        if not task_key:
            raise FeishuError("创建补录记录需要任务键")
        data = self._call("POST", body=self._cells(fields))
        if isinstance(data.get("record_id"), str) and data["record_id"]:
            return self._record({"record_id": data["record_id"], "fields": fields})
        criteria = {"logic": "and", "conditions": [[TASK_KEY, "==", task_key]]}
        result = self._call("GET", params={"limit": 2, "offset": 0, "filter": json.dumps(criteria, ensure_ascii=False)})
        matches = self._matrix(result)
        if len(matches) != 1 or result.get("has_more") is not False or cell_text(matches[0]["fields"], TASK_KEY) != task_key:
            raise FeishuError("已请求创建，但暂未能唯一确认飞书补录记录")
        return matches[0]

    def update(self, record_id: str, fields: dict) -> None:
        self._call("PATCH", f"/{quote(record_id, safe='')}", body=self._cells(fields))


def client_from_settings(settings, *, table_id: str | None = None) -> FeishuClient:
    table_id = settings.FEISHU_TABLE_ID if table_id is None else table_id
    if getattr(settings, "FEISHU_CLI_PATH", ""):
        return FeishuCliClient(settings.FEISHU_CLI_PATH, settings.FEISHU_BASE_TOKEN, table_id)
    return FeishuClient(
        settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET, settings.FEISHU_BASE_TOKEN, table_id,
    )


@dataclass(frozen=True)
class Submission:
    kind: str
    key: str
    values: dict
    not_applicable: bool = False


def cell_text(fields: Mapping, field: str) -> str:
    """读取普通文本、富文本和单选值。"""
    value = fields.get(field)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text", value.get("name"))
        return text.strip() if isinstance(text, str) else ""
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0].strip()
    if isinstance(value, list) and all(isinstance(part, dict) and isinstance(part.get("text"), str) for part in value):
        return "".join(part["text"] for part in value).strip()
    return ""


def task_key(kind: str, key: str) -> str:
    identity = json.dumps([kind, key], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def ticket_fields(kind: str, key: str, fields: Iterable[str], periods: Iterable[str]) -> dict:
    missing = set(fields)
    ticket = {
        TASK_KEY: task_key(kind, key), KIND: kind, BUSINESS_KEY: key, STATUS: PENDING,
        CONTEXT: ", ".join(sorted(missing)), PERIODS: ", ".join(sorted(set(periods))),
    }
    supported = _FORM_FIELDS.get(kind, ())
    if missing and missing.issubset(supported):
        ticket[INPUT] = json.dumps({field: "" for field in supported if field in missing}, ensure_ascii=False, indent=2)
    return ticket


def _require_fields(values: dict, required: Iterable[str], optional: Iterable[str] = ()) -> None:
    required = set(required)
    missing = required - values.keys()
    unknown = values.keys() - required - set(optional)
    if missing:
        raise ValueError(f"补录数据缺少字段 {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError("补录数据含有该任务不支持的字段")


def _string(value: object, field: str, limit: int) -> str:
    if isinstance(value, str):
        value = value.strip()
        if value and len(value) <= limit and not any(ord(char) < 32 or ord(char) == 127 for char in value):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                pass
            else:
                return value
    raise ValueError(f"{field} 必须是 1 到 {limit} 字的单行文本")


def _text_fields(values: dict, limits: Mapping[str, int]) -> dict[str, str]:
    return {field: _string(values[field], field, limit) for field, limit in limits.items()}


def _energy(values: dict) -> dict:
    values = _text_fields(values, {field: 16 for field in values})
    for field, allowed in (("energy_type", ALLOWED_ENERGY_TYPES), ("vehicle_type", ALLOWED_VEHICLE_TYPES)):
        allowed = allowed - {"占位"}
        if field in values and values[field] not in allowed:
            raise ValueError(f"{field} 必须为 {'、'.join(sorted(allowed))} 之一")
    return values


def _ads_proportion(proportion: object) -> dict:
    if type(proportion) in (int, float):
        proportion = str(proportion)
    proportion = _string(proportion, "ads_proportion", 16)
    if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,2})?%?", proportion) or Decimal(proportion.rstrip("%")) > 100:
        raise ValueError("ads_proportion 必须是 0 到 100 的百分比，最多保留两位小数")
    return {"ads_proportion_raw": proportion, "ads_proportion_pct": Decimal(proportion.rstrip("%"))}


def _ads(values: dict) -> dict:
    normalized = _text_fields(values, {field: 64 for field in ("ads_supplier", "ads_supplier_mode") if field in values})
    for field, value in normalized.items():
        if value in {"未知", "占位", "不适用"}:
            raise ValueError(f"{field} 须填写实际值")
    if "ads_proportion" in values:
        normalized.update(_ads_proportion(values["ads_proportion"]))
    if "listed_at" not in values:
        return normalized
    raw_date = _string(values["listed_at"], "listed_at", 32)
    try:
        listed = normalize_listed_at(raw_date)
        if listed["listed_at_date"].year < 1000:
            raise ValueError
    except ValueError:
        raise ValueError("listed_at 必须是有效上市日期或月份，例如 2026-07-01 或 2026年7月") from None
    return {**normalized, **listed}


def _area(key: str, values: dict) -> dict:
    _require_fields(values, _AREA_TEXT_FIELDS, ("city_short_name",))
    if "city_short_name" in values and _string(values["city_short_name"], "city_short_name", 32) != key:
        raise ValueError("city_short_name 必须与任务业务键一致")
    area = _text_fields(values, _AREA_TEXT_FIELDS)
    if not re.fullmatch(r"[1-9][0-9]{5}", area["city_code"]):
        raise ValueError("city_code 必须为六位行政区划代码")
    if area["province_code"] != area["city_code"][:2] + "0000":
        raise ValueError("province_code 必须与 city_code 的省级代码一致")
    if area["city_level"] not in ALLOWED_CITY_LEVELS or area["province_type"] not in ALLOWED_PROVINCE_TYPES:
        raise ValueError("city_level 或 province_type 超出允许值")
    if _REGIONS.get(area["region_code"]) != area["region_name"]:
        raise ValueError("region_code 与 region_name 不匹配")
    return {**area, "city_short_name": key}


def _subtype(values: dict) -> dict:
    _require_fields(values, ("subtype_code", "type_name"))
    values = _text_fields(values, {"subtype_code": 16, "type_name": 32})
    if values["type_name"] not in ALLOWED_VEHICLE_TYPES - {"占位"}:
        raise ValueError("type_name 必须为轿车、SUV、MPV 或其他")
    return values


def _ownership(values: dict, expected_fields: Iterable[str]) -> dict:
    required = set(expected_fields)
    if not required or not required.issubset({"company_code", "brand_code"}):
        raise ValueError("该归属问题需要管理员修正权威映射")
    _require_fields(values, required)
    ownership = _text_fields(values, {field: limit for field, limit in (("company_code", 32), ("brand_code", 64)) if field in values})
    if ownership.get("brand_code", "").upper() == "OTHER":
        raise ValueError("brand_code 必须指向实际品牌")
    if ownership.get("company_code", "").upper() == "OTHER":
        raise ValueError("company_code 必须指向已确认的公司")
    return ownership


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("补录数据含有重复字段")
    return value


def submission(record: Mapping, *, kind: str, key: str, expected_fields: Iterable[str] = ()) -> Submission:
    fields = record.get("fields")
    if (
        not isinstance(fields, dict)
        or cell_text(fields, TASK_KEY) != task_key(kind, key)
        or cell_text(fields, KIND) != kind
        or cell_text(fields, BUSINESS_KEY) != key
    ):
        raise ValueError("任务标识与本地任务不一致")
    if cell_text(fields, STATUS) != SUBMITTED:
        raise ValueError("任务状态应为已提交")
    if kind not in _KEY_LENGTHS or _string(key, BUSINESS_KEY, _KEY_LENGTHS[kind]) != key:
        raise ValueError("任务业务键或维度类型无效")

    raw_input = cell_text(fields, INPUT)
    if not raw_input or len(raw_input) > 4000:
        raise ValueError("补录数据须填写不超过 4000 字的 JSON 对象")
    try:
        values = json.loads(raw_input, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("补录数据须为有效 JSON 对象") from None
    if not isinstance(values, dict):
        raise ValueError("补录数据须为有效 JSON 对象")

    if kind == "ads" and values.get("not_applicable") is True:
        _require_fields(values, ("not_applicable",))
        return Submission(kind, key, {}, not_applicable=True)
    if kind == "area":
        normalized = _area(key, values)
    elif kind == "ownership":
        normalized = _ownership(values, expected_fields)
    elif kind in ("energy", "ads"):
        allowed = _FORM_FIELDS[kind]
        required = set(expected_fields) or set(allowed)
        if not required.issubset(allowed):
            raise ValueError("任务缺失字段与该维度类型不一致")
        _require_fields(values, required)
        normalized = _energy(values) if kind == "energy" else _ads(values)
    else:
        normalized = _subtype(values)
    return Submission(kind, key, normalized)


def apply_submission(item: Submission) -> None:
    if item.not_applicable:
        return
    with engine.begin() as conn:
        apply_feishu_dimension(conn, item.kind, item.key, item.values)
