"""飞书补录工单在本地状态库中的持久化收口。

所有针对 feishu_ticket 表的 SQL 只允许出现在本模块，其他模块通过
TicketRepository 的方法访问，避免裸 SQL 散落在多个调度流程里。
"""

from __future__ import annotations

import json

from scheduler.workflow import State, timestamp


class TicketRepository:
    """feishu_ticket 表的唯一访问入口，每次调用独立开启一次事务。"""

    def __init__(self, state: State):
        self._state = state

    def list(self, *, include_ads: bool = False) -> list[dict]:
        """返回工单列表；默认排除 ads（沿用历史补录轮询口径）。"""
        sql = "SELECT * FROM feishu_ticket"
        if not include_ads:
            sql += " WHERE kind <> 'ads'"
        sql += " ORDER BY kind, entity"
        with self._state.transaction() as conn:
            return [dict(row) for row in conn.execute(sql)]

    def not_applicable_keys(self) -> set[tuple[str, str]]:
        """已确认不适用的 (kind, entity)，用于从缺口清单里剔除。"""
        with self._state.transaction() as conn:
            return {
                (row["kind"], row["entity"])
                for row in conn.execute(
                    "SELECT kind, entity FROM feishu_ticket WHERE result='not_applicable'"
                )
            }

    def bind(self, kind: str, entity: str, record_id: str, expected_fields: list[str]) -> None:
        """绑定飞书记录；同一工单重复绑定时保留原通知时间。"""
        with self._state.transaction() as conn:
            conn.execute("""
                INSERT INTO feishu_ticket (kind, entity, record_id, expected) VALUES (?, ?, ?, ?)
                ON CONFLICT(kind,entity) DO UPDATE SET record_id=excluded.record_id, expected=excluded.expected,
                    notified=CASE WHEN record_id=excluded.record_id THEN notified END
            """, (kind, entity, record_id, json.dumps(expected_fields, ensure_ascii=False)))

    def save_result(self, kind: str, entity: str, revision: str, result: str, reason: str) -> None:
        """登记一次补录提交的修订指纹与处理结果。"""
        with self._state.transaction() as conn:
            conn.execute(
                "UPDATE feishu_ticket SET revision=?, result=?, reason=? WHERE kind=? AND entity=?",
                (revision, result, reason, kind, entity),
            )

    def mark_notified(self, record_ids: list[str]) -> None:
        """把已发群通知的工单记上通知时间。"""
        if not record_ids:
            return
        with self._state.transaction() as conn:
            conn.executemany(
                "UPDATE feishu_ticket SET notified=? WHERE record_id=?",
                [(timestamp(), record_id) for record_id in record_ids],
            )

    def mark_auto_confirmed(self, entity: str, evidence: str) -> None:
        """城市车型本地核验通过后，自动登记城市车型工单结果。"""
        with self._state.transaction() as conn:
            conn.execute(
                "UPDATE feishu_ticket SET result='auto_confirmed',reason=? "
                "WHERE kind='city_model' AND entity=? AND (result IS NULL OR result='auto_confirmed')",
                (evidence, entity),
            )

    def save_research_receipt(self, record_id: str, kind: str, entity: str, result: str, reason: str) -> None:
        """归属调研清单处理后回写工单结果。"""
        with self._state.transaction() as conn:
            conn.execute(
                "UPDATE feishu_ticket SET result=?,reason=? WHERE record_id=? AND kind=? AND entity=?",
                (result, reason, record_id, kind, entity),
            )
