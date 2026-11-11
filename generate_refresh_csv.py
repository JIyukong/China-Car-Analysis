import csv
import os
from datetime import date
from collections import defaultdict

OUTPUT_DIR = r"c:\Users\Administrator\Desktop\TraeProjects\MainProjects\CarApp"

RECORDS = [
    ("鸿蒙智行-问界", "问界M5", "2022-02-25", "首发"),
    ("鸿蒙智行-问界", "问界M5", "2023-04-17", "小改"),
    ("鸿蒙智行-问界", "问界M5", "2024-04-23", "大改"),
    ("鸿蒙智行-问界", "问界M5", "2025-03-04", "小改"),

    ("鸿蒙智行-问界", "问界M7", "2022-07-04", "首发"),
    ("鸿蒙智行-问界", "问界M7", "2023-09-12", "大改"),
    ("鸿蒙智行-问界", "问界M7", "2024-05-31", "小改"),
    ("鸿蒙智行-问界", "问界M7", "2024-08-26", "小改"),

    ("鸿蒙智行-问界", "问界M8", "2025-04-16", "首发"),
    ("鸿蒙智行-问界", "问界M9", "2023-12-26", "首发"),
    ("鸿蒙智行-问界", "问界M9", "2025-03-20", "小改"),

    ("鸿蒙智行-智界", "智界S7", "2023-11-28", "首发"),
    ("鸿蒙智行-智界", "智界S7", "2024-11-26", "大改"),
    ("鸿蒙智行-智界", "智界R7", "2024-09-24", "首发"),
    ("鸿蒙智行-智界", "智界R7", "2024-11-07", "小改"),

    ("鸿蒙智行-享界", "享界S9", "2024-08-06", "首发"),
    ("鸿蒙智行-享界", "享界S9", "2024-11-28", "小改"),
    ("鸿蒙智行-享界", "享界S9", "2025-04-16", "小改"),
    ("鸿蒙智行-享界", "享界S9T", "2025-09-12", "首发"),

    ("鸿蒙智行-尊界", "尊界S800", "2025-05-30", "首发"),

    ("小鹏", "小鹏P7", "2020-04-27", "首发"),
    ("小鹏", "小鹏P7", "2023-03-10", "大改"),
    ("小鹏", "小鹏P7", "2025-08-06", "大改"),
    ("小鹏", "小鹏P7+", "2024-11-07", "首发"),
    ("小鹏", "小鹏P7+", "2026-01-08", "小改"),
    ("小鹏", "小鹏G6", "2023-06-29", "首发"),
    ("小鹏", "小鹏G6", "2026-01-08", "小改"),
    ("小鹏", "小鹏G6", "2026-03-06", "小改"),
    ("小鹏", "小鹏G9", "2022-09-21", "首发"),
    ("小鹏", "小鹏G9", "2023-09-19", "大改"),
    ("小鹏", "小鹏G9", "2026-01-08", "小改"),
    ("小鹏", "小鹏G7", "2025-07-03", "首发"),
    ("小鹏", "小鹏G7", "2026-01-08", "小改"),
    ("小鹏", "小鹏X9", "2024-01-01", "首发"),
    ("小鹏", "小鹏X9", "2025-04-23", "小改"),
    ("小鹏", "小鹏X9", "2025-11-05", "小改"),
    ("小鹏", "小鹏X9", "2026-03-02", "小改"),
    ("小鹏", "小鹏MONA M03", "2024-08-27", "首发"),
    ("小鹏", "小鹏MONA M03", "2026-04-02", "小改"),
    ("小鹏", "小鹏GX", "2026-05-20", "首发"),

    ("理想", "理想L9", "2022-06-21", "首发"),
    ("理想", "理想L9", "2024-03-12", "小改"),
    ("理想", "理想L9", "2026-05-15", "大改"),
    ("理想", "理想L8", "2022-11-10", "首发"),
    ("理想", "理想L8", "2024-03-12", "小改"),
    ("理想", "理想L8", "2026-07-01", "大改"),
    ("理想", "理想L7", "2023-03-01", "首发"),
    ("理想", "理想L7", "2024-03-12", "小改"),
    ("理想", "理想L7", "2026-04-10", "小改"),
    ("理想", "理想L6", "2024-04-18", "首发"),
    ("理想", "理想L6", "2026-07-23", "大改"),
    ("理想", "理想MEGA", "2024-03-01", "首发"),
    ("理想", "理想i8", "2025-07-29", "首发"),
    ("理想", "理想i6", "2025-09-26", "首发"),

    ("蔚来", "蔚来ES6", "2019-06-01", "首发"),
    ("蔚来", "蔚来ES6", "2023-05-24", "大改"),
    ("蔚来", "蔚来ES6", "2025-05-16", "小改"),
    ("蔚来", "蔚来EC6", "2020-09-15", "首发"),
    ("蔚来", "蔚来EC6", "2023-09-11", "大改"),
    ("蔚来", "蔚来EC6", "2025-05-25", "小改"),
    ("蔚来", "蔚来ES8", "2018-06-01", "首发"),
    ("蔚来", "蔚来ES8", "2023-06-24", "大改"),
    ("蔚来", "蔚来ES8", "2025-12-01", "大改"),
    ("蔚来", "蔚来ET5", "2022-09-30", "首发"),
    ("蔚来", "蔚来ET5", "2025-05-25", "小改"),
    ("蔚来", "蔚来ET5T", "2023-06-15", "首发"),
    ("蔚来", "蔚来ET5T", "2025-05-25", "小改"),
    ("蔚来", "蔚来ET7", "2022-03-28", "首发"),
    ("蔚来", "蔚来ET7", "2024-04-16", "小改"),
    ("蔚来", "蔚来ES7", "2022-08-25", "首发"),
    ("蔚来", "蔚来EC7", "2023-06-13", "首发"),
    ("蔚来", "蔚来ET9", "2025-03-29", "首发"),

    ("乐道", "乐道L60", "2024-09-19", "首发"),
    ("乐道", "乐道L90", "2025-08-22", "首发"),
    ("萤火虫", "萤火虫", "2025-04-19", "首发"),

    ("小米", "小米SU7", "2024-03-28", "首发"),
    ("小米", "小米SU7 Ultra", "2025-02-27", "首发"),
    ("小米", "小米YU7", "2025-07-18", "首发"),

    ("极氪", "极氪001", "2021-10-23", "首发"),
    ("极氪", "极氪001", "2024-02-27", "大改"),
    ("极氪", "极氪001", "2024-08-13", "小改"),
    ("极氪", "极氪001", "2025-10-15", "小改"),
    ("极氪", "极氪007", "2023-12-27", "首发"),
    ("极氪", "极氪007", "2024-08-13", "小改"),
    ("极氪", "极氪007GT", "2025-04-23", "首发"),
    ("极氪", "极氪009", "2022-11-01", "首发"),
    ("极氪", "极氪009", "2024-07-19", "大改"),
    ("极氪", "极氪X", "2023-04-12", "首发"),
    ("极氪", "极氪X", "2025-07-01", "小改"),
    ("极氪", "极氪7X", "2024-09-20", "首发"),
    ("极氪", "极氪MIX", "2024-10-23", "首发"),
    ("极氪", "极氪9X", "2025-09-30", "首发"),
    ("极氪", "极氪9X", "2026-07-28", "小改"),
    ("极氪", "极氪8X", "2026-04-17", "首发"),
]


def parse_date(s):
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def add_months(d, n):
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    import calendar
    last_day = calendar.monthrange(y, m)[1]
    day = min(d.day, last_day)
    return date(y, m, day)


def month_range(start, end):
    out = []
    cur = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last:
        out.append("%04d-%02d" % (cur.year, cur.month))
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def write_table1():
    rows = []
    for brand, model, d, t in RECORDS:
        dt = parse_date(d)
        rows.append({
            "品牌": brand,
            "车型": model,
            "上市/改款日期": d,
            "上市/改款年月": "%04d-%02d" % (dt.year, dt.month),
            "类型": t,
        })
    rows.sort(key=lambda r: (r["品牌"], r["车型"], r["上市/改款日期"]))
    path = os.path.join(OUTPUT_DIR, "车型改款明细表.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["品牌", "车型", "上市/改款日期", "上市/改款年月", "类型"])
        w.writeheader()
        w.writerows(rows)
    print("表1已生成:", path, "共", len(rows), "条")
    return path


def narrow_name(model, dt, t, used):
    base = "%s%d%s" % (model, dt.year, t)
    if base not in used:
        used.add(base)
        return base
    cand = "%s%d年%02d月%s" % (model, dt.year, dt.month, t)
    used.add(cand)
    return cand


def build_window_rows():
    grouped = defaultdict(list)
    for brand, model, d, t in RECORDS:
        grouped[(brand, model)].append((parse_date(d), t))

    CUR_END = date(2026, 8, 1)
    window_rows = []

    for (brand, model), events in grouped.items():
        events.sort(key=lambda x: x[0])
        used_names = set()

        for idx, (ev_date, ev_type) in enumerate(events):
            nname = narrow_name(model, ev_date, ev_type, used_names)
            pre_start = add_months(ev_date, -3)
            first_sale_end = add_months(ev_date, 2)
            if idx + 1 < len(events):
                next_pre_start = add_months(events[idx + 1][0], -3)
            else:
                next_pre_start = None

            pre_end = add_months(ev_date, -1)
            if pre_start <= pre_end:
                for ym in month_range(pre_start, pre_end):
                    window_rows.append((brand, model, nname, ym, "改款期"))

            if next_pre_start is not None:
                fs_end = min(first_sale_end, add_months(next_pre_start, -1))
            else:
                fs_end = first_sale_end
            if ev_date <= fs_end:
                for ym in month_range(ev_date, fs_end):
                    window_rows.append((brand, model, nname, ym, "首销期"))

            flat_start = add_months(fs_end, 1)
            if next_pre_start is not None:
                flat_end = add_months(next_pre_start, -1)
            else:
                flat_end = CUR_END
            if flat_start <= flat_end:
                for ym in month_range(flat_start, flat_end):
                    window_rows.append((brand, model, nname, ym, "平销期"))

    window_rows.sort(key=lambda r: (r[0], r[1], r[3], r[2]))
    path = os.path.join(OUTPUT_DIR, "车型销售窗口期表.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["品牌", "车型", "狭义车型名", "年月", "窗口期"])
        w.writerows(window_rows)
    print("表2已生成:", path, "共", len(window_rows), "条")
    return path


if __name__ == "__main__":
    write_table1()
    build_window_rows()
