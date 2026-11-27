"""Register reviewed private operating entities without attributing minority investors' sales."""
import json
from pathlib import Path

from sqlalchemy import text

COMPANIES = (
    {'code': 'LINKTOUR', 'name': '领途汽车', 'full': '领途汽车有限公司',
     'evidence': 'https://ggzyfw.beijing.gov.cn/ztxxtbr/20250121/4957680.html'},
    {'code': 'HYCAN', 'name': '合创汽车', 'full': '合创汽车科技有限公司',
     'evidence': 'https://static.cninfo.com.cn/finalpage/2024-03-29/1219444071.PDF'},
    {'code': 'HOZON', 'name': '合众汽车', 'full': '合众新能源汽车股份有限公司',
     'evidence': 'https://static.cninfo.com.cn/finalpage/2026-07-10/1225417607.PDF'},
    {'code': 'WM_MOTOR', 'name': '威马汽车', 'full': '威马汽车科技集团有限公司',
     'evidence': 'https://ipc.court.gov.cn/zh-cn/news/view-3129.html'},
)


def register_company(conn, values):
    rows = list(conn.execute(text('''
        SELECT company_code,company_name,full_name,company_type,country,is_listed
        FROM dim_company WHERE company_code=:code OR company_name=:name OR full_name=:full
    '''), values).mappings())
    expected = {'company_code': values['code'], 'company_name': values['name'],
                'full_name': values['full'], 'company_type': '其他', 'country': 'CN', 'is_listed': 0}
    if rows:
        if len(rows) != 1 or dict(rows[0]) != expected:
            raise ValueError(f"公司档案冲突，拒绝覆盖: {values['code']}")
        return False
    conn.execute(text('''INSERT INTO dim_company
        (company_code,company_name,full_name,company_type,country,is_listed)
        VALUES (:code,:name,:full,'其他','CN',0)'''), values)
    return True


def run(output):
    from pipelines.dim.manual_refresh import apply_feishu_dimension
    from scheduler.monthly_publish import _publication_transaction
    from scheduler.workflow import State

    report = {'scope': 'independent_private_operating_entities', 'companies_created': 0, 'oems': []}
    with State().lock('warehouse', timeout=120), _publication_transaction() as conn:
        for company in COMPANIES:
            report['companies_created'] += int(register_company(conn, company))
            # Existing ownership validator rejects replacement of confirmed owners,
            # ambiguous bridges and conflicting historical assignments. No Feishu calls.
            existing = list(conn.execute(text('SELECT id FROM dim_oem WHERE oem_name=:name'), company).scalars())
            if len(existing) != 1:
                raise ValueError(f"主机厂身份不唯一: {company['name']}")
            apply_feishu_dimension(conn, 'ownership', company['name'], {'company_code': company['code']})
            actual = conn.execute(text('''SELECT c.company_code FROM dim_oem o
                JOIN dim_company c ON c.id=o.company_id WHERE o.id=:id'''), {'id': existing[0]}).scalar_one()
            if actual != company['code']:
                raise ValueError('公司归属回读不一致')
            months = list(conn.execute(text('''SELECT DISTINCT stat_month FROM ods_sales_monthly_model
                WHERE source='16888_style' AND TRIM(manufacturer_name_raw)=:name ORDER BY stat_month'''), company).scalars())
            report['oems'].append({**company, 'oem_id': existing[0],
                                   'affected_style_months': [str(month)[:7] for month in months]})
    # Receipt is written only after the transaction committed successfully.
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
