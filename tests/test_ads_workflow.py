from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest

from scheduler.workflow import State


def row(name, **fields):
    return {"record_id": name, "fields": {
        "model_name": name, "ads_proportion": "95%", "ads_supplier": "供应商",
        "ads_supplier_mode": "合作模式", "listed_at": "2026/4/19", **fields,
    }}


def test_sync_applies_only_supplied_models_in_one_transaction(tmp_path):
    from scheduler.ads_workflow import sync

    calls = []

    @contextmanager
    def transaction():
        yield calls

    def apply(conn, key, values):
        conn.append((key, values["ads_proportion_pct"], values["listed_at_date"]))

    assert sync(State(tmp_path), [row("车型 A"), row("车型 B")], transaction, apply) == 2
    assert calls == [("车型 A", Decimal("95"), date(2026, 4, 19)),
                     ("车型 B", Decimal("95"), date(2026, 4, 19))]


@pytest.mark.parametrize("rows", [
    [row("车型 A"), row("车型 B", ads_proportion="101%")],
    [row("车型 A"), row("车型 a")],
])
def test_sync_rejects_bad_input_before_any_database_write(tmp_path, rows):
    from scheduler.ads_workflow import sync

    def unexpected_write():
        pytest.fail("invalid source opened a write transaction")

    with pytest.raises(ValueError):
        sync(State(tmp_path), rows, unexpected_write, None)
