"""User-approved research exclusions; a reporting bucket, never a legal entity."""
import json
from pathlib import Path

from sqlalchemy import text

COMPANY = {'code': 'OUT_OF_RESEARCH', 'name': '非研究范围车企', 'full': '非研究范围车企（汇总分类，非经营主体）'}
OEMS = (
    'DS(进口)', 'DS汽车', '爱驰汽车', '宝沃汽车', '北汽幻速', '比速汽车', '大乘汽车',
    '电动屋', '电咖汽车', '东南汽车', '东南三菱', '福迪汽车', '福汽新龙马', '观致汽车',
    '国机智骏', '国金汽车', '汉腾汽车', '华人运通', '华泰新能源', '君马汽车',
    '康迪电动汽车集团', '雷丁', '猎豹汽车', '陆风汽车', '起亚(进口)', '曙光汽车',
    '天际汽车', '小虎汽车', '新特汽车', '野马汽车', '御捷新能源', '远航汽车', '云度新能源', '云雀汽车',
)


def research_companies(rows):
    """Filter before ranking, concentration or operating-performance calculations.

    Market totals must be computed from the original unfiltered facts.
    """
    return [row for row in rows if row['company_code'] not in (None, 'OTHER', COMPANY['code'])]


def is_research_bucket(group, name):
    return group == 'company' and name == COMPANY['name']


def run(output):
    from pipelines.dim.manual_refresh import apply_feishu_dimension
    from scheduler.monthly_publish import _publication_transaction
    from scheduler.reviewed_private_companies import register_company
    from scheduler.workflow import State

    report = {'scope': 'user_approved_2026_11_27', 'company': COMPANY, 'oems': []}
    with State().lock('warehouse', timeout=120), _publication_transaction() as conn:
        report['companies_created'] = int(register_company(conn, COMPANY))
        for name in OEMS:
            # Existing validator permits OTHER -> reviewed code, and rejects
            # replacing a confirmed real company or conflicting bridge.
            apply_feishu_dimension(conn, 'ownership', name, {'company_code': COMPANY['code']})
            rows = list(conn.execute(text('''SELECT o.id,c.company_code FROM dim_oem o
                JOIN dim_company c ON c.id=o.company_id WHERE o.oem_name=:name'''),
                {'name': name}).mappings())
            if len(rows) != 1 or rows[0]['company_code'] != COMPANY['code']:
                raise ValueError('排除归属回读不一致: ' + name)
            report['oems'].append({'name': name, 'oem_id': rows[0]['id']})
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
