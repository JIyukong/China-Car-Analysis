"""Apply an explicitly reviewed local Feishu research manifest incrementally."""
import json
from pathlib import Path
from sqlalchemy import text
from base_infra.config import get_settings
from pipelines.dim.manual_refresh import apply_feishu_dimension
from scheduler.monthly_publish import _publication_transaction
from scheduler import feishu

def run(state):
    path=Path(__file__).resolve().parents[1]/'data/manual_dimensions/ownership_research.json'
    manifest=json.loads(path.read_text(encoding='utf-8'))
    client=feishu.client_from_settings(get_settings())
    with state.lock('feishu',timeout=10),state.lock('warehouse',timeout=10):
        records={r['record_id']:r for r in client.records()}
        updates={}
        receipts=[]
        with _publication_transaction() as conn:
            for company in manifest['companies']:
                found=conn.execute(text('SELECT company_code,company_name,full_name FROM dim_company WHERE company_code=:code OR company_name=:name'),company).mappings().all()
                if found:
                    if len(found)!=1 or found[0]['company_code']!=company['code'] or found[0]['company_name']!=company['name'] or found[0]['full_name']!=company['full']:
                        raise ValueError('Company registry conflict: '+company['code'])
                else:
                    conn.execute(text("INSERT INTO dim_company(company_code,company_name,full_name,company_type,country,is_listed) VALUES (:code,:name,:full,:type,'CN',:listed)"),company)
            for item in manifest['items']:
                record=records.get(item['record_id'])
                if not record or feishu.cell_text(record['fields'],feishu.STATUS)!=feishu.PENDING:
                    continue
                if feishu.cell_text(record['fields'],feishu.BUSINESS_KEY)!=item['key'] or feishu.cell_text(record['fields'],feishu.KIND)!='ownership':
                    raise ValueError('Feishu identity changed')
                fields={feishu.INPUT:json.dumps(item['values'],ensure_ascii=False),feishu.FEEDBACK:item['evidence']}
                if item['action']=='apply':
                    apply_feishu_dimension(conn,'ownership',item['key'],item['values'])
                    fields[feishu.STATUS]=feishu.APPLIED
                    receipts.append((item,'applied'))
                elif item['action']=='city_replaced':
                    model=conn.execute(text("SELECT id FROM dim_city_model WHERE source='16888_city' AND source_model_name=:key AND review_status='confirmed'"),item).scalar_one()
                    if model!=item['values']['city_model_id']:
                        raise ValueError('City reference changed')
                    fields[feishu.STATUS]=feishu.APPLIED
                    receipts.append((item,'not_applicable'))
                updates[item['record_id']]=fields
        client.batch_update(updates)
        with state.transaction() as conn:
            for item,result in receipts:
                conn.execute('UPDATE feishu_ticket SET result=?,reason=? WHERE record_id=? AND kind=? AND entity=?',
                    (result,item['evidence'],item['record_id'],'ownership',item['key']))
        print(json.dumps({'updated':len(updates),'resolved':len(receipts),'researched_pending':len(updates)-len(receipts)}))
    return True
