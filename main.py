"""Acme ledger QA API. Records are latest-by-update order snapshots."""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
IST = ZoneInfo('Asia/Kolkata')
D = Decimal


class Question(BaseModel):
    question: str


def date_of(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(IST).date()


@lru_cache(maxsize=1)
def get_data():
    rates = {k: D(str(v)) for k,v in json.loads((BASE/'rates.json').read_text())['usd_per_unit'].items()}
    latest = {}
    with (BASE/'orders.jsonl').open(encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line)
            stamp = datetime.fromisoformat(r['updated_at'].replace('Z', '+00:00'))
            key = r['id']
            if key not in latest or stamp > latest[key][0]:
                latest[key] = stamp, r
    orders = []
    for stamp, r in latest.values():
        r['date'] = date_of(r['created_at'])
        r['updated_date'] = date_of(r['updated_at'])
        r['usd'] = D(str(r['amount'])) * rates[r['currency']]
        r['qty'] = int(r['qty'])
        orders.append(r)
    return orders

MONTHS = {x.lower():i for i,x in enumerate(['','January','February','March','April','May','June','July','August','September','October','November','December']) if x}
MONTHS.update({k[:3]:v for k,v in list(MONTHS.items())})
REGIONS = ('north','south','east','west','central')
PRODUCTS = ('rice cooker','air fryer','juicer','grinder','toaster','blender','mixer','kettle')


def money(x):
    return str(D(x).quantize(D('.01'), rounding=ROUND_HALF_UP))


def fnum(x):
    return str(D(str(x)).quantize(D('.02'), rounding=ROUND_HALF_UP))


def parse_date_filters(q):
    years = re.findall(r'\b20(?:2[0-9]|3[0-9])\b', q)
    yr = int(years[0]) if years else None
    mons = [v for k,v in MONTHS.items() if re.search(r'\b'+re.escape(k)+r'\b',q)]
    month = mons[0] if mons else None
    # YYYY-MM and MM/YYYY
    match = re.search(r'\b(20\d\d)[-/](0?[1-9]|1[0-2])\b', q)
    if match: yr,month = int(match.group(1)),int(match.group(2))
    match = re.search(r'\b(0?[1-9]|1[0-2])[-/](20\d\d)\b', q)
    if match: month,yr = int(match.group(1)),int(match.group(2))
    quarters = re.search(r'\bq([1-4])\b|\b(?:quarter)\s*([1-4])\b|\b(first|second|third|fourth) quarter\b',q)
    quarter = None
    if quarters:
        qt = next((g for g in quarters.groups() if g),None)
        quarter = int(qt) if qt.isdigit() else ['first','second','third','fourth'].index(qt)+1
    return yr, month, quarter


def select(q, status=None):
    orders = get_data()
    yr, month, quarter = parse_date_filters(q)
    region = next((r for r in REGIONS if re.search(r'\b'+r+r'\b',q)),None)
    product = next((p for p in PRODUCTS if re.search(r'\b'+re.escape(p)+r's?\b',q)),None)
    customer = re.search(r'\bc\d{4}\b',q)
    order = re.search(r'\bord[- ]?\d+\b',q)
    if status is None:
        if re.search(r'\brefund(?:ed|s)?\b|\breturned\b',q): status='refunded'
        elif re.search(r'\bvoid(?:ed)?\b|\bcancel(?:led|ed|lation|lations)?\b',q): status='void'
        elif re.search(r'\bpaid\b',q): status='paid'
        elif re.search(r'\b(?:revenue|sales|sold|earned|gross merchandise|gmv)\b',q): status='paid'
    use_updated = bool(re.search(r'\b(?:updated|modified|refunded on|refund date)\b',q))
    result = []
    for r in orders:
        date = r['updated_date'] if use_updated else r['date']
        if yr is not None and date.year != yr: continue
        if month is not None and date.month != month: continue
        if quarter is not None and (date.month-1)//3+1 != quarter: continue
        if region and r['region'].lower()!=region: continue
        if product and r['product'].lower()!=product: continue
        if customer and r['customer'].lower()!=customer.group(): continue
        if order and r['id'].lower().replace('-','') != order.group().replace('-','').replace(' ',''): continue
        if status and r['status']!=status: continue
        result.append(r)
    return result


def grouped(rows,field,measure):
    groups=defaultdict(list)
    for r in rows: groups[r[field]].append(r)
    return {k:measure_rows(v,measure) for k,v in groups.items()}


def measure_rows(rows,kind):
    if kind == 'revenue': return sum((r['usd'] for r in rows),D(0))
    if kind == 'quantity': return sum(r['qty'] for r in rows)
    if kind == 'customers': return len({r['customer'] for r in rows})
    if kind == 'orders': return len(rows)
    if kind == 'aov': return sum((r['usd'] for r in rows),D(0))/D(len(rows)) if rows else D(0)
    return len(rows)


def determine_measure(q):
    # Recognize both 'average paid order value' and phrasing such as
    # 'On average, how many US dollars is a paid South order worth?'
    if re.search(r'\baov\b', q): return 'aov'
    if re.search(r'\b(?:average|mean|avg)\b|\bon average\b', q):
        if re.search(r'\b(?:order|orders|transaction|transactions|purchase|purchases|sale|sales)\b', q) and re.search(r'\b(?:value|worth|amount|revenue|usd|dollars|money|spend|spent)\b', q):
            return 'aov'
    if re.search(r'\b(?:units|items|quantity|quantities|pieces)\b',q): return 'quantity'
    if re.search(r'\b(?:customers|buyers|clients)\b',q) and re.search(r'\b(?:how many|count|number|unique|distinct|most|top|highest|fewest|least)\b',q): return 'customers'
    if re.search(r'\b(?:revenue|sales|amount|value|usd|dollars|money|earnings|gmv|total refunded|refund value|refund amount)\b',q): return 'revenue'
    if re.search(r'\b(?:how many|count|number|orders|transactions|refunds|purchases)\b',q): return 'orders'
    return 'revenue'


def solve(question):
    q = question.strip().lower().replace('–','-')
    if not q: return 'Please provide a question.'
    measure=determine_measure(q)
    if 'refund rate' in q or 'refund percentage' in q or 'percentage of orders' in q and 'refund' in q:
        allr = select(q,status=None)
        # Explicitly bypass inferred refunded status.
        allr = [r for r in allr] if not re.search(r'\brefund',q) else select(re.sub(r'\brefund(?:ed|s)?\b','',q),status=None)
        if not allr: return '0'
        n=sum(r['status']=='refunded' for r in allr)
        return money(D(n)*100/D(len(allr)))+'%'
    if 'conversion rate' in q: return 'Conversion rate cannot be determined from orders alone.'
    # For "orders" questions default to all order statuses unless explicitly filtered.
    explicit_status=bool(re.search(r'\b(?:paid|refunded|refunds?|void|cancelled|canceled)\b',q))
    if measure == 'quantity' and re.search(r'\b(?:sold|sales)\b',q) and not explicit_status:
        status = 'paid'
    else:
        status = None
    if status is None:
        status = None if measure in ('orders','customers','quantity') and not explicit_status else 'paid' if measure in ('revenue','aov') and not re.search(r'\brefund(?:ed|s)?\b|\bvoid\b',q) else None
    rows=select(q,status)
    if re.search(r'\b(?:net revenue|net sales)\b',q):
        # paid snapshots represent currently retained revenue.
        rows=select(q,'paid')
    group = None
    for pattern,field in [(r'\b(?:by|per|across|each|every)\s+region\b','region'),(r'\b(?:by|per|across|each|every)\s+product\b','product'),(r'\b(?:by|per|across|each|every)\s+customer\b','customer'),(r'\b(?:which|what)\s+(?:region|product|customer)\b','guess')]:
        if re.search(pattern,q): group=field;break
    if group=='guess':
        group=next((x for x in ['region','product','customer'] if re.search(r'\b'+x+r'\b',q)),None)
    if re.search(r'\b(?:top|best|highest|most|lowest|least|worst|leading|largest|smallest|rank)\b',q):
        if not group:
            group=next((x for x in ['product','region','customer'] if re.search(r'\b'+x+r's?\b',q)),None)
    if re.search(r'\b(?:month by month|monthly|by month|per month|each month)\b',q): group='month'
    if re.search(r'\b(?:by status|per status)\b',q): group='status'
    if group:
        if group=='month':
            g=defaultdict(list)
            for r in rows: g[r['date'].strftime('%Y-%m')].append(r)
            vals={k:measure_rows(v,measure) for k,v in g.items()}
        else: vals=grouped(rows,group,measure)
        if not vals: return 'No matching orders.'
        ascending=bool(re.search(r'\b(?:lowest|least|fewest|smallest|worst|bottom)\b',q))
        ranked=sorted(vals.items(),key=lambda kv:(kv[1],kv[0].lower()),reverse=not ascending)
        if re.search(r'\b(?:which|what|top|best|highest|most|lowest|least|fewest|smallest|worst|leading|largest|rank|bottom)\b',q):
            n=re.search(r'\b(?:top|bottom|first)\s+(\d+)\b',q)
            take=int(n.group(1)) if n else 1
            # Identification questions expect the entity name, not a label/value pair.
            # Provide the numeric metric only when explicitly requested as well.
            wants_value = bool(re.search(
                r'\b(?:how much|what (?:was|is) (?:the )?(?:revenue|amount|value|total)|'
                r'and (?:its|their|the) (?:revenue|amount|value|sales)|with (?:its|the) (?:revenue|amount))\b', q
            ))
            if not wants_value:
                return ', '.join(k for k, _ in ranked[:take])
            return ', '.join(f'{k}: {money(v) if measure in ("revenue","aov") else v}' for k,v in ranked[:take])
        return ', '.join(f'{k}: {money(v) if measure in ("revenue","aov") else v}' for k,v in sorted(vals.items()))
    value=measure_rows(rows,measure)
    return money(value) if measure in ('revenue','aov') else str(value)


app=FastAPI(title='Acme Ledger Agent')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['*'],allow_headers=['*'])

@app.get('/')
def root(): return {'status':'ok','endpoint':'POST /answer','orders':len(get_data())}

@app.post('/answer')
def answer(payload:Question): return {'answer':solve(payload.question)}
