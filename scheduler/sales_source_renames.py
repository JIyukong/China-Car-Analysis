"""Reviewed 16888 display-name changes; never infer identity from sales alone.

Evidence: docs/evidence/sales-source-renames-2026-11-26.json.
Keys are scoped to exact model/manufacturer pairs, not manufacturer labels alone.
"""

RENAMES = {
    ('AION i60', '广汽埃安'): ('AION i60', '埃安'),
    ('Aion LX', '广汽埃安'): ('Aion LX', '埃安'),
    ('AION N60', '广汽埃安'): ('AION N60', '埃安'),
    ('AION RT', '广汽埃安'): ('AION RT', '埃安'),
    ('AION S', '广汽埃安'): ('AION S', '埃安'),
    ('AION UT', '广汽埃安'): ('AION UT', '埃安'),
    ('AION V', '广汽埃安'): ('AION V', '埃安'),
    ('AION Y', '广汽埃安'): ('AION Y', '埃安'),
    ('哈弗大狗', '长城汽车'): ('长城大狗', '长城汽车'),
    ('魏牌 高山', '长城汽车'): ('魏牌 高山新能源', '长城汽车'),
    ('欧拉5', '长城新能源'): ('欧拉5 EV', '长城汽车'),
    ('欧拉好猫', '长城新能源'): ('欧拉好猫', '长城汽车'),
    ('海豹07 DM-i', '比亚迪'): ('海豹07', '比亚迪'),
    ('领克Z20', '领克'): ('领克20', '领克'),
    ('问道V9', '北汽新能源'): ('极狐问道V9', '北汽新能源'),
    ('e爱丽舍', '东风新能源'): ('e爱丽舍', '神龙汽车'),
    ('红旗国耀', '一汽红旗'): ('红旗金葵花国耀', '一汽红旗'),
}


def canonical_key(key):
    return next((old for old, new in RENAMES.items() if new == key), key)


def rename_plan(existing, expected):
    plan = []
    for old in sorted(existing.keys() - expected.keys()):
        new = RENAMES.get(old)
        if new not in expected:
            continue
        if new in existing:
            raise ValueError(f'改名前后业务键并存，禁止自动合并: {old!r}, {new!r}')
        plan.append((old, new))
    return plan


def dimension_expressions():
    """Parameterized SQL expressions for resolving new source labels to old DIM identity."""
    model, oem, params = [], [], {}
    for i, (old, new) in enumerate(RENAMES.items()):
        params.update({f'rm{i}': new[0], f'ro{i}': new[1], f'cm{i}': old[0], f'co{i}': old[1]})
        condition = f'TRIM(s.model_name_raw)=:rm{i} AND TRIM(s.manufacturer_name_raw)=:ro{i}'
        model.append(f'WHEN {condition} THEN :cm{i}')
        oem.append(f'WHEN {condition} THEN :co{i}')
    return (f"CASE {' '.join(model)} ELSE TRIM(s.model_name_raw) END",
            f"CASE {' '.join(oem)} ELSE TRIM(s.manufacturer_name_raw) END", params)
