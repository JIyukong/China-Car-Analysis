"""Bounded A/H stock repairs through the shared scheduler entry point."""

import csv
import json
import re
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def read_dates(path):
    needed = {}
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            code = row['code'].strip()
            if not re.fullmatch(r'(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)', code):
                raise ValueError(f'Only A/HK stocks allowed: {code}')
            day = date.fromisoformat(row['date'])
            if day > today:
                raise ValueError(f'Future repair date: {day}')
            needed.setdefault(code, set()).add(day)
    if not needed:
        raise ValueError('Empty stock repair manifest')
    return {code: frozenset(days) for code, days in needed.items()}


def repair(path):
    # Manual repairs use the same raw/factor evidence and full-history audit.
    from scheduler.stock_history import run
    needed = read_dates(path)
    return run(last=datetime.now(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=1),
               requested=needed)


def quarantine_suspension_snapshot(conn,reference):
    """Mark a reviewed non-trade invalid without deleting or changing its prices."""
    from decimal import Decimal
    from sqlalchemy import text
    query=text('SELECT open,high,low,close,volume,is_valid_ohlc FROM dwd_stock_daily '
               'WHERE stock_code=:code AND trade_date=:date FOR UPDATE')
    row=conn.execute(query,reference).mappings().one_or_none()
    if row is None:
        return
    if (row['volume'] is None or Decimal(str(row['volume']))!=0 or
            any(row[k] is None for k in ('open','high','low','close')) or
            len({Decimal(str(row[k])) for k in ('open','high','low','close')})!=1):
        raise ValueError('Suspension conflicts with a non-snapshot DWD record')
    conn.execute(text('UPDATE dwd_stock_daily SET is_valid_ohlc=0 '
                      'WHERE stock_code=:code AND trade_date=:date'),reference)
    observed=conn.execute(query,reference).mappings().one_or_none()
    if observed is None or observed['is_valid_ohlc']!=0:
        raise RuntimeError('Quarantine readback failed')


def apply_evidence(path):
    """Apply reviewed suspension references and restore omitted optional values."""
    from decimal import Decimal
    from sqlalchemy import text
    from base_infra.db import engine
    from scheduler.market_workflow import STOCK
    from scheduler.workflow import State

    evidence = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    suspensions = evidence.get('suspensions', [])
    restores = evidence.get('restore_amount', [])
    listings = evidence.get('listings', [])
    calendars = evidence.get('calendar', [])
    for row in suspensions + restores:
        if not re.fullmatch(r'(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)', row['code']):
            raise ValueError('Only A/HK stocks allowed')
        date.fromisoformat(row['date'])
    for row in suspensions:
        if not row['source_ref'].startswith('https://') or len(row['source_ref']) > 255:
            raise ValueError('Suspension requires reviewed source URL')
        if not isinstance(row.get('quarantine_existing_dwd',False),bool):
            raise ValueError('quarantine_existing_dwd must be explicit boolean')
    for row in restores:
        value = Decimal(str(row['amount']))
        if not value.is_finite() or value < 0:
            raise ValueError('Invalid amount')
    for row in listings:
        if not re.fullmatch(r'(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)',row['code']):
            raise ValueError('Only A/HK stocks allowed')
        listed = date.fromisoformat(row['listed_date'])
        if row.get('delisted_date') and date.fromisoformat(row['delisted_date']) < listed:
            raise ValueError('Delisting precedes listing')
    for row in calendars:
        date.fromisoformat(row['date'])
        if row['market'] not in ('A','HK') or row['is_open'] not in (0,1):
            raise ValueError('Calendar needs explicit market and open flag')
    for row in listings+calendars:
        if not row['source_ref'].startswith('https://') or len(row['source_ref'])>255:
            raise ValueError('Reference requires reviewed source URL')
    if listings or calendars or suspensions:
        from scheduler.stock_history import ROOT, save_json
        from uuid import uuid4
        save_json(ROOT/'references'/f'{uuid4().hex}.json',evidence)
    with State().lock(STOCK), State().lock('warehouse'), (engine.begin() if suspensions or listings or calendars else nullcontext()) as conn:
        for row in listings:
            current=conn.execute(text("SELECT listed_date,delisted_date FROM dim_stock WHERE stock_code=:code AND market IN ('A','HK') FOR UPDATE"),row).mappings().one_or_none()
            if current is None:
                raise ValueError('Listing references an unknown stock')
            for field in ('listed_date','delisted_date'):
                incoming=row.get(field)
                if current[field] is not None and incoming and str(current[field])!=incoming:
                    raise ValueError('Reference conflicts with existing lifecycle date')
            conn.execute(text('UPDATE dim_stock SET listed_date=:listed_date, '
                              'delisted_date=COALESCE(delisted_date,:delisted_date) WHERE stock_code=:code'),
                         {**row,'delisted_date':row.get('delisted_date')})
        for row in calendars:
            if row['market']=='A':
                conn.execute(text("INSERT INTO dim_trade_calendar (market,trade_date,is_open,note) "
                                  "VALUES ('SSE',:date,:is_open,'reviewed reference') ON DUPLICATE KEY UPDATE "
                                  "is_open=VALUES(is_open),note=VALUES(note)"),row)
            else:
                conn.execute(text("INSERT INTO etl_market_calendar (market,trade_date,is_open,source_ref) "
                                  "VALUES ('HKEX',:date,:is_open,:source_ref) ON DUPLICATE KEY UPDATE "
                                  "is_open=VALUES(is_open),source_ref=VALUES(source_ref)"),row)
        for row in suspensions:
            conn.execute(text('INSERT INTO etl_stock_suspension (stock_code,trade_date,source_ref) '
                              'VALUES (:code,:date,:source_ref) ON DUPLICATE KEY UPDATE '
                              'source_ref=VALUES(source_ref)'), row)
            if row.get('quarantine_existing_dwd') is True:
                quarantine_suspension_snapshot(conn,row)
    if restores:
        # Do not bypass the factor evidence and ODS/DWD transaction gate.
        from scheduler.stock_history import ROOT, save_json
        save_json(ROOT/'optional_restore_evidence.json', {'source':str(Path(path).resolve()),'rows':restores})
    print(json.dumps({'suspensions_reviewed': len(suspensions), 'optional_values_staged': len(restores),
                      'listing_references':len(listings),'calendar_references':len(calendars)}))
    return True
