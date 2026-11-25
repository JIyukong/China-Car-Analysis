"""Independent vendor adapters. Never forward-fill prices or deduplicate OHLC."""
from datetime import date
from decimal import Decimal
from importlib import import_module
from urllib.parse import urlparse

from scheduler.stock_history import decimal, near, save_json


def sina_assignment(body):
    # The provider appends a second compressed payload inside a JS comment.
    # Passing that comment to the decoder corrupts the real history stream.
    return body.split('=',1)[1].split(';',1)[0].strip()


def parse_sina_factors(payload, market):
    import pandas as pd
    records=payload['data']
    frame=pd.DataFrame(records).rename(columns={'d':'date','f':'hfq_factor','c':'cash'})
    columns=['date','hfq_factor']+(['cash'] if market=='HK' else [])
    if frame.empty or any(c not in frame or frame[c].isna().any() for c in columns):
        raise ValueError('Missing explicit HFQ factor fields')
    return frame[columns]


def sina_frame(symbol, market, *, factors=False):
    """Decode the native response before AKShare's price filling/deduplication."""
    import akshare as ak
    import pandas as pd
    import requests
    from akshare.utils import demjson
    function = ak.stock_zh_a_daily if market=='A' else ak.stock_hk_daily
    runtime = vars(import_module(function.__module__))
    url_name = ('zh_sina_a_stock_hfq_url' if market=='A' else 'hk_sina_stock_hist_hfq_url') if factors else (
        'zh_sina_a_stock_hist_url' if market=='A' else 'hk_sina_stock_hist_url')
    response = requests.get(runtime[url_name].format(symbol), timeout=15)
    response.raise_for_status()
    text = sina_assignment(response.text)
    if factors:
        return parse_sina_factors(demjson.decode(text),market)
    decoder = runtime['py_mini_racer'].MiniRacer()
    try:
        decoder.eval(runtime['hk_js_decode'])
        records = decoder.call('d',demjson.decode(text))
    finally:
        if hasattr(decoder,'close'):
            decoder.close()
    return pd.DataFrame(records)


def normalize(frame, vendor, market):
    names = {'日期':'trade_date', 'date':'trade_date', '开盘':'open', '最高':'high', '最低':'low',
             '收盘':'close', '成交量':'volume', '成交额':'amount'}
    result = []
    for original in frame.rename(columns=names).to_dict('records'):
        day = original['trade_date']
        day = date.fromisoformat(str(day)[:10])
        row = {'trade_date': day}
        for name in ('open','high','low','close','volume','amount'):
            value = original.get(name)
            row[name] = None if value is None or str(value).lower() in ('nan','none','nat') else decimal(value)
        # Tencent calls volume in board lots "amount", not monetary turnover.
        if vendor == 'tx':
            row['volume'] = row['amount'] * (100 if market=='A' else 1) if row['amount'] is not None else None
            row['amount'] = None
        elif vendor == 'em' and market == 'A' and row['volume'] is not None:
            row['volume'] *= 100
        if row['volume'] is not None:
            if row['volume'] != row['volume'].to_integral_value():
                raise ValueError('Non-integral share volume')
            row['volume'] = int(row['volume'])
        result.append(row)
    if len({r['trade_date'] for r in result}) != len(result):
        raise ValueError('Duplicate provider date')
    return result


def factor_prices(raw, factors, market):
    """Calculate Sina's own reinvestment basis without synthesizing trading days."""
    records = sorted(factors.to_dict('records'), key=lambda r: str(r['date']))
    if not records:
        raise ValueError('Missing HFQ factors; never assume identity')
    if len({str(r['date'])[:10] for r in records}) != len(records):
        raise ValueError('Duplicate HFQ factor effective date')
    output = []
    for row in raw:
        applicable = [r for r in records if str(r['date'])[:10] <= row['trade_date'].isoformat()]
        if not applicable:
            raise ValueError(f'No HFQ factor effective at {row["trade_date"]}')
        record = applicable[-1]
        factor = decimal(record['hfq_factor'])
        cash = decimal(record['cash']) if market == 'HK' else Decimal(0)
        if factor <= 0:
            raise ValueError('Invalid HFQ factor')
        output.append({**row, **{k: decimal(row[k])*factor+cash for k in ('open','high','low','close')}})
    return output


def verify_adjustment(raw, adjusted, factors, market, *, basis='sina'):
    calculated = {r['trade_date']: r for r in factor_prices(raw, factors, market)}
    result = []
    for row in adjusted:
        expected = calculated.get(row['trade_date'])
        if expected is None:
            raise ValueError('Missing effective adjustment evidence')
        if basis=='em_tx':
            original=next(r for r in raw if r['trade_date']==row['trade_date'])
            nonidentity=any(not near(original[k],expected[k],'0.0002') for k in ('open','high','low','close'))
            if nonidentity and all(original[k]==row[k] for k in ('open','high','low','close')):
                raise ValueError('Provider returned unadjusted prices for explicit hfq request')
        elif basis!='sina':
            raise ValueError('Unknown adjustment basis')
        elif any(not near(row[k], expected[k], '0.0002') for k in ('open','high','low','close')):
            raise ValueError('HFQ response does not match explicit factors / baseline')
        result.append(row)
    return result


def tencent_frame(symbol,start,end,adjust,*,factors=None):
    """Require the named response branch; never silently select raw day bars."""
    import requests
    import pandas as pd
    from akshare.utils import demjson
    endpoint=('https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get' if symbol.startswith('hk') and adjust=='hfq' else
              'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get')
    response=requests.get(endpoint,
                          params={'param':f'{symbol},day,{start},{end},640,{adjust}'},timeout=15)
    response.raise_for_status()
    body=response.text
    payload=demjson.decode(body[body.find('{'):])
    expected='hfqday' if adjust=='hfq' else 'day'
    rows=payload['data'][symbol].get(expected)
    identity=False
    if rows is None and adjust=='hfq' and factors is not None and symbol.startswith('hk'):
        records=factors.to_dict('records')
        effective=[r for r in records if str(r['date'])[:10]<=end]
        identity=(any(str(r['date'])[:10]<=start for r in effective) and
                  all(decimal(r['hfq_factor'])==1 and decimal(r['cash'])==0 for r in effective))
        if identity:
            rows=payload['data'][symbol].get('day')
    if rows is None:
        raise ValueError(f'Tencent missing explicit {expected} response')
    frame=pd.DataFrame([r[:6] for r in rows],columns=['date','open','close','high','low','amount'])
    frame.attrs['adjustment_evidence']='identity_factors' if identity else expected
    return frame


def ths_frame(symbol,year,adjust):
    """Public yearly bars have explicit dates and unadjusted share volume."""
    import json
    import time
    import requests
    import pandas as pd
    mode='02' if adjust=='hfq' else '00'
    for attempt in range(3):
        version='v4' if attempt==1 else 'v6'
        try:
            response=requests.get(f'https://d.10jqka.com.cn/{version}/line/hs_{symbol}/{mode}/{year}.js',
                                  params={'_':str(time.time_ns())},
                                  headers={'User-Agent':'Mozilla/5.0','Referer':f'https://stockpage.10jqka.com.cn/{symbol}/'},timeout=15)
            response.raise_for_status()
            break
        except (requests.ConnectionError,requests.Timeout):
            if attempt==2:
                raise
        except requests.HTTPError as exc:
            if attempt==2 or exc.response is None or exc.response.status_code not in (502,503,504):
                raise
    prefix=f'quotebridge_{version}_line_hs_{symbol}_{mode}_{year}('
    body=response.text.strip().rstrip(';')
    if not body.startswith(prefix) or not body.endswith(')'):
        raise ValueError('THS missing explicit adjustment wrapper')
    payload=json.loads(body[len(prefix):-1])
    rows=[]
    rejected=[]
    for line in payload['data'].split(';'):
        if not line:
            continue
        values=line.split(',')
        if len(values)<7 or len(values[0])!=8 or not values[0].startswith(str(year)):
            raise ValueError('Invalid THS yearly bar')
        day=date(int(values[0][:4]),int(values[0][4:6]),int(values[0][6:]))
        if any(value=='' for value in values[1:5]):
            rejected.append(day.isoformat())
            continue
        rows.append([day,*values[1:7]])
    frame=pd.DataFrame(rows,columns=['date','open','high','low','close','volume','amount'])
    frame.attrs['rejected_dates']=rejected
    return frame


def collect(stock, first, last, directory, *, requested=None):
    import akshare as ak
    import requests
    from scheduler.jobs.common import build_scheduler_config
    from pipelines.ingest.stock.network_runtime import patched_requests_runtime
    from scheduler.market_proxy import ensure_proxy
    code, market = stock['stock_code'], stock['market']
    if market not in ('A','HK'):
        raise ValueError('Only A/HK providers allowed')
    symbol = code[:6] if market=='A' else code.split('.')[0].zfill(5)
    sina_symbol = code[-2:].lower()+code[:6] if market=='A' else symbol
    output, errors = {}, {}
    target = directory / code
    target.mkdir(parents=True, exist_ok=True)
    config = build_scheduler_config()
    config.request_timeout = 15
    config.direct_domains=(*config.direct_domains,'qq.com','gtimg.cn','sina.com.cn','sina.cn','10jqka.com.cn')
    market_proxy=ensure_proxy()
    original_send = requests.sessions.Session.send
    counter = 0
    def capture(session, request, **kwargs):
        nonlocal counter
        kwargs['timeout'] = 15
        host=urlparse(request.url).hostname or ''
        if market_proxy and (host=='eastmoney.com' or host.endswith('.eastmoney.com')):
            kwargs['proxies']={'http':market_proxy,'https':market_proxy}
        response = original_send(session, request, **kwargs)
        counter += 1
        url = urlparse(request.url)
        save_json(target / f'http-{counter}.json', {'host':url.hostname, 'path':url.path,
                  'status':response.status_code, 'body':response.text})
        return response
    # Only this task uses this process; normal scheduler launches independent children.
    with patched_requests_runtime(config):
        requests.sessions.Session.send = capture
        try:
            try:
                factors = sina_frame(sina_symbol, market, factors=True)
                (target/'factors.json').write_text(factors.to_json(orient='records',date_format='iso'),encoding='utf-8')
            except Exception as exc:
                save_json(target/'failure.json', {'reason':'unverified_factor', 'error':str(exc)})
                return {}, {'factors':str(exc)}
            vendors = ('em','tx','ths','sina') if market=='A' else ('em','tx','sina')
            for vendor in vendors:
                accumulated = {}
                try:
                    sina_raw = sina_frame(sina_symbol,market) if vendor=='sina' else None
                    for year in range(first.year, last.year+1):
                        try:
                            start, end = max(first,date(year,1,1)),min(last,date(year,12,31))
                            if requested is not None:
                                selected = [d for d in requested if d.year==year]
                                if not selected:
                                    continue
                                start,end=min(selected),max(selected)
                            kwargs = dict(start_date=start.strftime('%Y%m%d'),end_date=end.strftime('%Y%m%d'))
                            if vendor=='em':
                                fetch = ak.stock_zh_a_hist if market=='A' else ak.stock_hk_hist
                                options={**kwargs, **({'timeout':15} if market=='A' else {})}
                                raw_frame=fetch(symbol=symbol,adjust='',**options)
                                adjusted_frame=fetch(symbol=symbol,adjust='hfq',**options)
                            elif vendor=='tx':
                                tx_symbol=sina_symbol if market=='A' else 'hk'+symbol
                                raw_frame=tencent_frame(tx_symbol,start.isoformat(),end.isoformat(),'')
                                adjusted_frame=tencent_frame(tx_symbol,start.isoformat(),end.isoformat(),'hfq',factors=factors)
                            elif vendor=='ths':
                                raw_frame=ths_frame(symbol,year,'')
                                adjusted_frame=ths_frame(symbol,year,'hfq')
                                rejected=set(raw_frame.attrs.get('rejected_dates',[])) | set(adjusted_frame.attrs.get('rejected_dates',[]))
                                if rejected:
                                    errors[f'ths:{year}:incomplete_bars']=','.join(sorted(rejected))
                            else:
                                raw_frame=sina_raw
                                adjusted_frame=None
                            (target/f'{vendor}-{year}-raw.json').write_text(raw_frame.to_json(orient='records',date_format='iso'),encoding='utf-8')
                            if raw_frame.empty:
                                continue
                            raw=[r for r in normalize(raw_frame,vendor,market) if start<=r['trade_date']<=end]
                            if vendor=='sina':
                                adjusted=factor_prices(raw,factors,market)
                            else:
                                (target/f'{vendor}-{year}-hfq.json').write_text(adjusted_frame.to_json(orient='records',date_format='iso'),encoding='utf-8')
                                adjusted=[r for r in normalize(adjusted_frame,vendor,market) if start<=r['trade_date']<=end]
                                adjusted=verify_adjustment(raw,adjusted,factors,market,basis='em_tx')
                            if vendor=='tx' and market=='A':
                                # This API rounds some dates to whole lots. It cannot
                                # attest exact shares, even when its value is integral.
                                adjusted=[{**r,'volume':None} for r in adjusted]
                            accumulated.update({r['trade_date']:r for r in adjusted})
                        except Exception as exc:
                            errors[f'{vendor}:{year}']=str(exc)
                    output[vendor]=list(accumulated.values())
                except Exception as exc:
                    errors[vendor]=str(exc)
            canonical={r['trade_date']:r for vendor in ('em','tx') for r in output.get(vendor,[])}
            if 'sina' in output:
                compatible=[{**r,'_price_eligible':r['trade_date'] in canonical and
                            all(near(r[k],canonical[r['trade_date']][k],'0.0002') for k in ('open','high','low','close'))}
                            for r in output['sina']]
                if any(not r['_price_eligible'] for r in compatible):
                    errors['sina_basis']='No matching EM/Tencent HFQ price; Sina retained for trade-field evidence only'
                output['sina']=compatible
            save_json(target/'normalized.json',output)
            save_json(target/'request.json',{'stock':code,'first':first,'last':last,'adjust':'hfq',
                      'basis':'em_tx','corporate_action_reference':'sina','vendors':list(output),'errors':errors})
        finally:
            requests.sessions.Session.send = original_send
    return output,errors
