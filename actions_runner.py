# ============================================================
#  actions_runner.py - GitHub Actions 전용 실행 스크립트
#  GitHub 서버에서 매일 자동 실행됨
# ============================================================

import urllib.request
import xml.etree.ElementTree as ET
import json, os, csv, base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')

# GitHub Actions 환경변수에서 키 읽기
API_KEY      = os.environ.get('API_KEY', '7531ffbca8d17bcd8ab5e68286ae0715ef85da1787095ba0c835c539fa1e06ff')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_USER  = 'yslee031216-dot'
GITHUB_REPO  = 'pork-price'
COMPANY_NAME = '대산에프앤비(육가공)'
BASE         = 'http://data.ekape.or.kr/openapi-data/service/user/grade'
CSV_FILE     = 'price_data.csv'

HOLIDAYS_FALLBACK = {
    '20250101','20250128','20250129','20250130',
    '20250301','20250505','20250506',
    '20250606','20250815','20251003','20251009','20251225',
    '20260101','20260216','20260217','20260218',
    '20260301','20260505','20260525','20260606',
    '20260603','20260815','20260817','20261009','20261225',
}

SLAUGHTER_HOUSES = {
    'c_0320': ('도드람',   '수도권'),
    'c_0302': ('협신식품', '수도권'),
    'c_1301': ('삼성식품', '수도권'),
    'c_0323': ('농협부천', '수도권'),
    'c_1005': ('김해축공', '영남권'),
    'c_0202': ('부경축공', '영남권'),
    'c_1201': ('신흥산업', '영남권'),
    'c_0905': ('농협고령', '영남권'),
    'c_0809': ('농협나주', '호남권'),
    'c_1401': ('삼호축산', '호남권'),
}

def is_workday(dt, holidays):
    return dt.weekday() < 5 and dt.strftime('%Y%m%d') not in holidays

def avg_of(lst):
    v = [x for x in lst if x and x > 0]
    return int(sum(v) / len(v)) if v else 0

def load_holidays():
    holidays = set(HOLIDAYS_FALLBACK)
    today = datetime.now(KST).replace(tzinfo=None)
    for year in [today.year, today.year - 1]:
        url = (f'http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo'
               f'?serviceKey={API_KEY}&solYear={year}&numOfRows=50&_type=json')
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            items = data['response']['body']['items']['item']
            if isinstance(items, dict): items = [items]
            for it in items:
                holidays.add(str(it['locdate']))
        except:
            pass
    return holidays

def calc_price(items_data, min_places, min_head):
    valid     = {k: v for k, v in items_data.items()
                 if k in SLAUGHTER_HOUSES and v[1] > 0 and v[0] > 0}
    total_cnt = sum(v[1] for v in valid.values())
    place_cnt = len(valid)
    return place_cnt >= min_places and total_cnt >= min_head

def fetch_day(date_str):
    url = (f'{BASE}/auct/pigGrade?serviceKey={API_KEY}'
           f'&startYmd={date_str}&endYmd={date_str}'
           f'&skinYn=Y&numOfRows=100&pageNo=1')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            root = ET.fromstring(resp.read().decode('utf-8'))
        if (root.findtext('.//resultCode') or '') not in ('00', '0000', ''):
            return 0, None, None
        for it in root.findall('.//item'):
            if (it.findtext('gradeNm') or '') == '등외제외':
                v = it.findtext('c_1101eTotAmt')
                national = int(v) if v and v.strip() else 0
                items_data = {}
                for code in SLAUGHTER_HOUSES:
                    amt = it.findtext(f'{code}Amt')
                    cnt = it.findtext(f'{code}Cnt')
                    if amt and cnt:
                        try:
                            items_data[code] = (int(amt), int(cnt))
                        except:
                            pass
                farmer_price = national if calc_price(items_data, 4, 300) else None
                dealer_price = national if calc_price(items_data, 5, 300) else None
                return national, farmer_price, dealer_price
    except:
        pass
    return 0, None, None

def load_csv():
    if not os.path.exists(CSV_FILE):
        return {}
    result = {}
    with open(CSV_FILE, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if row['date'] and row['price']:
                result[row['date']] = {
                    'price':  int(row['price']),
                    'farmer': int(row['farmer']) if row.get('farmer') and str(row['farmer']).strip() else None,
                    'dealer': int(row['dealer']) if row.get('dealer') and str(row['dealer']).strip() else None,
                }
    return result

def save_csv(data):
    with open(CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'price', 'farmer', 'dealer'])
        writer.writeheader()
        for d in sorted(data.keys()):
            r = data[d]
            writer.writerow({
                'date':   d,
                'price':  r['price'],
                'farmer': r['farmer'] if r['farmer'] else '',
                'dealer': r['dealer'] if r['dealer'] else '',
            })

def collect(today, holidays):
    yesterday = today - timedelta(days=1)
    cached    = load_csv()
    print(f'캐시: {len(cached)}건')

    start_date  = datetime(today.year - 3, 1, 1)  # 2023년부터
    recheck_from = today - timedelta(days=7)  # 최근 7일은 항상 재수집(확정치 갱신)
    # 오후 6시 이전이면 오늘 제외, 이후면 오늘 포함
    if today.hour >= 18:
        collect_until = today
    else:
        collect_until = today - timedelta(days=1)
    needed = []
    cur = start_date
    while cur <= collect_until:
        d = cur.strftime('%Y%m%d')
        if is_workday(cur, holidays):
            if d not in cached or cur >= recheck_from:
                needed.append(d)
        cur += timedelta(days=1)

    if needed:
        print(f'신규 수집: {len(needed)}건')
        for i, d in enumerate(needed):
            national, farmer, dealer = fetch_day(d)
            if national > 0:
                cached[d] = {'price': national, 'farmer': farmer, 'dealer': dealer}
            if (i + 1) % 20 == 0:
                print(f'  {i+1}/{len(needed)} 완료...')
        save_csv(cached)
        print(f'CSV 저장 완료')
    else:
        print('추가 수집 없음')

    return cached

def _build_yearly_chart(data, today):
    """연도별 월평균 계산 (1월~12월)"""
    years = [today.year - 3, today.year - 2, today.year - 1, today.year]
    result = {}
    for y in years:
        monthly = []
        for m in range(1, 13):
            ym = f'{y}{m:02d}'
            prices = [v['price'] if isinstance(v, dict) else v
                      for k, v in data.items() if k.startswith(ym)]
            avg = int(sum(prices) / len(prices)) if prices else None
            # 올해 미래 월은 None
            if y == today.year and m > today.month:
                avg = None
            monthly.append(avg)
        result[str(y)] = monthly
    # 전체 평균 (각 월별 평균)
    all_avg = []
    for m_idx in range(12):
        vals = [result[str(y)][m_idx] for y in years if result[str(y)][m_idx]]
        all_avg.append(int(sum(vals)/len(vals)) if vals else None)
    result['avg'] = all_avg
    return result

def build_stats(data, today, holidays):
    yesterday = today - timedelta(days=1)

    this_year_str = str(today.year)
    last_year_str = str(today.year - 1)
    year_2024_str = str(today.year - 2)
    year_2023_str = str(today.year - 3)

    def get_price(d): return d['price'] if isinstance(d, dict) else d
    def get_farmer(d):
        v = d.get('farmer') if isinstance(d, dict) else None
        return int(v) if v and str(v).strip() else None
    def get_dealer(d):
        v = d.get('dealer') if isinstance(d, dict) else None
        return int(v) if v and str(v).strip() else None

    this_year     = {k: get_price(v) for k, v in data.items() if k.startswith(this_year_str)}
    last_year     = {k: get_price(v) for k, v in data.items() if k.startswith(last_year_str)}
    year_2024     = {k: get_price(v) for k, v in data.items() if k.startswith(year_2024_str)}
    year_2023     = {k: get_price(v) for k, v in data.items() if k.startswith(year_2023_str)}
    all_data      = {**last_year, **this_year}
    this_year_raw = {k: v for k, v in data.items() if k.startswith(this_year_str)}

    sd = sorted(this_year.keys())
    if not sd: return None

    today_str = today.strftime('%Y%m%d')
    yd_str = yesterday.strftime('%Y%m%d')
    # 오늘 데이터 있으면 오늘, 없으면 어제
    dd = today_str if today_str in this_year else (yd_str if yd_str in this_year else sd[-1])
    da     = this_year[dd]
    dt_dd  = datetime.strptime(dd, '%Y%m%d')

    dd_raw    = this_year_raw.get(dd, {})
    farmer_da = get_farmer(dd_raw)
    dealer_da = get_dealer(dd_raw)
    both_same = bool(farmer_da and dealer_da and farmer_da == dealer_da)

    idx     = sd.index(dd)
    prev_dd = sd[idx - 1] if idx > 0 else ''
    pa      = this_year[prev_dd] if prev_dd else 0
    diff    = da - pa
    dpct    = round(diff / pa * 100, 1) if pa else 0

    today_dow     = today.weekday()
    this_week_mon = today - timedelta(days=today_dow)
    week_prices   = []
    c = this_week_mon
    while c <= today:
        d = c.strftime('%Y%m%d')
        raw = data.get(d, {})
        fv  = raw.get('farmer') if isinstance(raw, dict) else None
        fv  = int(fv) if fv and str(fv).strip() else None
        if d in this_year and fv:
            week_prices.append(this_year[d])
        c += timedelta(days=1)
    wa = avg_of(week_prices)

    prev_mon = this_week_mon - timedelta(days=7)
    prev_fri = this_week_mon - timedelta(days=3)
    prev_week_prices = []
    c = prev_mon
    while c <= prev_fri:
        d = c.strftime('%Y%m%d')
        raw = data.get(d, {})
        fv  = raw.get('farmer') if isinstance(raw, dict) else None
        fv  = int(fv) if fv and str(fv).strip() else None
        if d in all_data and fv:
            prev_week_prices.append(all_data[d])
        c += timedelta(days=1)
    pwa       = avg_of(prev_week_prices)
    week_diff = wa - pwa
    week_dpct = round(week_diff / pwa * 100, 1) if pwa else 0

    lym = f'{today.year-1}{dd[4:6]}'
    lya = avg_of([v for k, v in last_year.items() if k.startswith(lym)])
    yd  = da - lya
    yp  = round(yd / lya * 100, 1) if lya else 0

    all_sd   = sorted(all_data.keys())
    dd_idx   = all_sd.index(dd) if dd in all_sd else len(all_sd) - 1
    recent10 = all_sd[max(0, dd_idx - 9): dd_idx + 1]
    wl, wt, wly_list = [], [], []
    for d in recent10:
        dt  = datetime.strptime(d, '%Y%m%d')
        lyd = f'{dt.year-1}{d[4:]}'
        wl.append(f'{d[4:6]}/{d[6:8]}')
        wt.append(all_data.get(d))
        wly_list.append(last_year.get(lyd))

    today_dow2 = today.weekday()
    this_mon2  = today - timedelta(days=today_dow2)
    wk_labels, wk_avg, wk_ly_avg = [], [], []
    for i in range(7, -1, -1):
        w_mon = this_mon2 - timedelta(weeks=i)
        w_fri = w_mon + timedelta(days=4)
        if w_fri > dt_dd:
            w_fri = dt_dd
        ly_w_mon = w_mon.replace(year=w_mon.year - 1)
        ly_w_fri = w_fri.replace(year=w_fri.year - 1)

        w_prices  = [all_data[(w_mon + timedelta(d)).strftime('%Y%m%d')]
                     for d in range(5)
                     if (w_mon + timedelta(d)).strftime('%Y%m%d') in all_data
                     and (w_mon + timedelta(d)) <= w_fri]
        ly_prices = [last_year[(ly_w_mon + timedelta(d)).strftime('%Y%m%d')]
                     for d in range(5)
                     if (ly_w_mon + timedelta(d)).strftime('%Y%m%d') in last_year
                     and (ly_w_mon + timedelta(d)) <= ly_w_fri]

        wk_labels.append(f'{w_mon.month}/{w_mon.day}주')
        wk_avg.append(avg_of(w_prices) or None)
        wk_ly_avg.append(avg_of(ly_prices) or None)

    ml, mt, mly_list = [], [], []
    for i in range(11, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        lbl   = f"{str(y)[2:]}.{m:02d}"
        ym    = f'{y}{m:02d}'
        ly_ym = f'{y-1}{m:02d}'
        src   = this_year if y == today.year else last_year
        ml.append(lbl)
        mt.append(avg_of([v for k, v in src.items() if k.startswith(ym)]) or None)
        mly_list.append(avg_of([v for k, v in last_year.items() if k.startswith(ly_ym)]) or None)

    cmp_labels, cmp_this, cmp_last = [], [], []
    for m in range(1, 13):  # 1월~12월 전체
        ym    = f'{today.year}{m:02d}'
        ly_ym = f'{today.year-1}{m:02d}'
        cmp_labels.append(f'{m}월')
        # 올해: 현재월 이후는 None (공란)
        if m <= today.month:
            cmp_this.append(avg_of([v for k, v in this_year.items() if k.startswith(ym)]) or None)
        else:
            cmp_this.append(None)
        cmp_last.append(avg_of([v for k, v in last_year.items() if k.startswith(ly_ym)]) or None)

    # 연간 평균 계산
    this_year_avg = avg_of([v for v in cmp_this if v])
    last_year_avg = avg_of([v for v in cmp_last if v])

    return dict(
        dd=f'{dd[4:6]}월 {dd[6:8]}일',
        prev_dd=f'{prev_dd[4:6]}월 {prev_dd[6:8]}일' if prev_dd else '',
        da=da, farmer_da=farmer_da, dealer_da=dealer_da, both_same=both_same,
        diff=diff, dpct=dpct,
        wa=wa, pwa=pwa, week_diff=week_diff, week_dpct=week_dpct,
        lya=lya, yd=yd, yp=yp,
        wl=wl, wt=wt, wly=wly_list,
        wk_labels=wk_labels, wk_avg=wk_avg, wk_ly_avg=wk_ly_avg,
        ml=ml, mt=mt, mly=mly_list,
        cmp_labels=cmp_labels, cmp_this=cmp_this, cmp_last=cmp_last,
        this_year_avg=this_year_avg, last_year_avg=last_year_avg,
        yearly_chart=_build_yearly_chart(data, today),
        this_year=today.year, last_year=today.year - 1,
    )

def jn(lst):
    return '[' + ','.join('null' if v is None else str(v) for v in lst) + ']'

def js(lst):
    return '[' + ','.join('null' if v is None else f'"{v}"' for v in lst) + ']'

def arrow(v, p):
    if v > 0: return f'<span class="up">▲ {abs(v):,}원 (+{p}%)</span>'
    if v < 0: return f'<span class="dn">▼ {abs(v):,}원 ({p}%)</span>'
    return '<span class="eq">변동없음</span>'

def make_html(s, today, data):
    dk = f'{today.year}년 {today.month}월 {today.day}일'
    dc = 'up' if s['diff']>0 else ('dn' if s['diff']<0 else 'eq')
    yc = 'up' if s['yd']>0   else ('dn' if s['yd']<0   else 'eq')
    ds = '+' if s['diff']>0 else ''
    ys = '+' if s['yd']>0   else ''

    def _pv(v):
        p = v['price'] if isinstance(v, dict) else v
        return str(p) if p is not None else 'null'
    def _fv(v):
        p = v.get('farmer') if isinstance(v, dict) else None
        return str(p) if p is not None else 'null'
    def _dv(v):
        p = v.get('dealer') if isinstance(v, dict) else None
        return str(p) if p is not None else 'null'

    price_js  = '{' + ','.join(f'"{k}":{_pv(v)}' for k, v in sorted(data.items())) + '}'
    import json as _json
    yearly_chart_js = _json.dumps(s['yearly_chart'])
    farmer_js = '{' + ','.join(f'"{k}":{_fv(v)}' for k, v in sorted(data.items())) + '}'
    dealer_js = '{' + ','.join(f'"{k}":{_dv(v)}' for k, v in sorted(data.items())) + '}'

    cmp_diff = [(a - b) if (a and b) else None
                for a, b in zip(s['cmp_this'], s['cmp_last'])]

    # 배너 결정
    if s['both_same']:
        banner = ''
    elif not s['farmer_da'] and not s['dealer_da']:
        banner = '<div style="background:#fff8e8;border:.5px solid #f0d080;border-radius:10px;padding:11px 18px;margin-bottom:14px;font-size:13px;color:#7a6000">⚠️ 오늘은 인정단가 없음 (도축장 수 또는 두수 조건 미달)</div>'
    elif s['farmer_da'] and s['dealer_da'] and s['farmer_da'] != s['dealer_da']:
        banner = f'<div style="background:#e8f4ff;border:.5px solid #a0c8f0;border-radius:10px;padding:11px 18px;margin-bottom:14px;font-size:13px;color:#1a5c8a">ℹ️ 농가 인정단가: <b>{s["farmer_da"]:,}원</b> / 거래처 인정단가: <b>{s["dealer_da"]:,}원</b></div>'
    elif s['farmer_da'] and not s['dealer_da']:
        banner = f'<div style="background:#e8f4ff;border:.5px solid #a0c8f0;border-radius:10px;padding:11px 18px;margin-bottom:14px;font-size:13px;color:#1a5c8a">ℹ️ 농가 인정단가: <b>{s["farmer_da"]:,}원</b> / 거래처: 해당없음</div>'
    else:
        banner = ''

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{COMPANY_NAME} 경락단가 — {dk}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f0ede8;color:#2c2c2a}}
.wrap{{max-width:780px;margin:0 auto;padding:28px 16px 60px}}
.header{{background:linear-gradient(135deg,#0a5c46,#0f7a5f);border-radius:16px;padding:28px 32px;margin-bottom:20px;position:relative;overflow:hidden}}
.header::after{{content:'🐷';position:absolute;right:24px;top:50%;transform:translateY(-50%);font-size:80px;opacity:.12}}
.header .co{{font-size:13px;color:rgba(255,255,255,.6);margin-bottom:6px}}
.header h1{{font-size:22px;font-weight:600;color:#fff;margin-bottom:5px}}
.header .dt{{font-size:13px;color:rgba(255,255,255,.55)}}
.header .src{{font-size:11px;color:rgba(255,255,255,.4);margin-top:4px}}
.metrics{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:16px}}
@media(min-width:520px){{.metrics{{grid-template-columns:repeat(5,1fr)}}}}
.metric{{background:#fff;border-radius:12px;padding:18px 16px;border:.5px solid #d3d1c7}}
.metric .lbl{{font-size:12px;color:#999;margin-bottom:8px}}
.metric .val{{font-size:22px;font-weight:600;color:#1a1a18;letter-spacing:-.5px;line-height:1}}
.metric .unit{{font-size:13px;font-weight:400}}
.metric .sub{{font-size:12px;margin-top:6px}}
.up{{color:#e24b4a}}.dn{{color:#378add}}.eq{{color:#999}}
.card{{background:#fff;border-radius:14px;border:.5px solid #d3d1c7;padding:22px;margin-bottom:14px}}
.card-head{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px}}
.card-head h2{{font-size:14px;font-weight:600;color:#444}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#888}}
.leg{{display:flex;align-items:center;gap:5px}}
.dot{{width:10px;height:10px;border-radius:2px}}
.chart-wrap{{position:relative;width:100%;height:240px}}
.footer{{text-align:center;font-size:12px;color:#aaa;margin-top:32px;line-height:1.8}}
input[type=date]{{padding:7px 10px;border:.5px solid #d3d1c7;border-radius:8px;font-size:13px;background:#fff;color:#2c2c2a}}
.btn-primary{{padding:7px 18px;background:#0f7a5f;color:#fff;border:none;border-radius:8px;font-size:13px;cursor:pointer}}
.btn-secondary{{padding:7px 14px;background:#f0ede8;color:#666;border:.5px solid #d3d1c7;border-radius:8px;font-size:13px;cursor:pointer}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="co">{COMPANY_NAME}</div>
    <h1>🐷 돼지 경락단가 일일 리포트</h1>
    <div class="dt">{dk} 생성 · {s['dd']} 기준</div>
    <div class="src">출처: 축산물품질평가원 · 탕박 등외제외 전국 평균 (ekapepia.com 동일 기준)</div>
  </div>

  {banner}

  <div class="metrics">
    <div class="metric">
      <div class="lbl">전일 경락단가 (전국)</div>
      <div class="val">{s['da']:,}<span class="unit">원</span></div>
      <div class="sub" style="color:#999">{s['dd']} · 탕박 등외제외</div>
    </div>
    <div class="metric">
      <div class="lbl">전영업일 대비 ({s['prev_dd']})</div>
      <div class="val {dc}">{ds}{s['diff']:,}<span class="unit">원</span></div>
      <div class="sub">{arrow(s['diff'], s['dpct'])}</div>
    </div>
    <div class="metric">
      <div class="lbl">이번 주 평균</div>
      <div class="val">{f"{s['wa']:,}" if s['wa'] else '—'}<span class="unit">{'원' if s['wa'] else ''}</span></div>
      <div class="sub">{arrow(s['week_diff'], s['week_dpct']) if s['wa'] else '<span style="color:#999">데이터 없음</span>'}</div>
    </div>
    <div class="metric">
      <div class="lbl">전주 평균</div>
      <div class="val">{f"{s['pwa']:,}" if s['pwa'] else '—'}<span class="unit">{'원' if s['pwa'] else ''}</span></div>
      <div class="sub" style="color:#999">{'지난주 기준' if s['pwa'] else '데이터 없음'}</div>
    </div>
    <div class="metric">
      <div class="lbl">전년 동월 대비</div>
      <div class="val {yc}">{ys}{s['yd']:,}<span class="unit">원</span></div>
      <div class="sub">{arrow(s['yd'], s['yp'])}</div>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h2>📅 날짜별 단가 조회</h2></div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;align-items:center">
      <input type="date" id="startDate">
      <span style="color:#999;font-size:13px">~</span>
      <input type="date" id="endDate">
      <button class="btn-primary" onclick="queryRange()">조회</button>
      <button class="btn-secondary" onclick="clearQuery()">초기화</button>
    </div>
    <div id="queryResult" style="display:none">
      <div id="queryAvg" style="background:#f0ede8;border-radius:10px;padding:14px 18px;margin-bottom:12px;font-size:14px;font-weight:500"></div>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="border-bottom:1.5px solid #d3d1c7">
            <th style="text-align:left;padding:8px 10px;color:#999;font-weight:500">날짜</th>
            <th style="text-align:right;padding:8px 10px;color:#999;font-weight:500">전국평균</th>
            <th style="text-align:right;padding:8px 10px;color:#999;font-weight:500">농가단가</th>
            <th style="text-align:right;padding:8px 10px;color:#999;font-weight:500">거래처단가</th>
            <th style="text-align:right;padding:8px 10px;color:#999;font-weight:500">전일 대비</th>
          </tr>
        </thead>
        <tbody id="queryTable"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-head">
      <h2>주간 경락단가 추이 (원/kg)</h2>
      <div class="legend">
        <span class="leg"><span class="dot" style="background:#0f7a5f"></span>최근</span>
        <span class="leg"><span class="dot" style="background:#378add"></span>전년 동기</span>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="wc"></canvas></div>
  </div>

  <div class="card">
    <div class="card-head">
      <h2>주별 경락단가 추이 — 최근 8주 (원/kg)</h2>
      <div class="legend">
        <span class="leg"><span class="dot" style="background:#0f7a5f"></span>{s['this_year']}년</span>
        <span class="leg"><span class="dot" style="background:#378add"></span>{s['last_year']}년</span>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="wkc"></canvas></div>
  </div>

  <div class="card">
    <div class="card-head">
      <h2>월별 경락단가 추이 — 올해 (원/kg)</h2>
      <div class="legend">
        <span class="leg"><span class="dot" style="background:#0f7a5f"></span>{s['this_year']}년</span>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="mc_this"></canvas></div>
    <div style="margin-top:20px">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="border-bottom:1.5px solid #d3d1c7;background:#f8f7f5">
            <th style="text-align:left;padding:8px 12px;color:#666;font-weight:600">월</th>
            <th style="text-align:right;padding:8px 12px;color:#0f7a5f;font-weight:600">평균단가</th>
            <th style="text-align:right;padding:8px 12px;color:#666;font-weight:600">전월 대비</th>
            <th style="text-align:right;padding:8px 12px;color:#666;font-weight:600">증감률</th>
          </tr>
        </thead>
        <tbody id="monthlyTableBody"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-head">
      <h2>전년 대비 경락단가 추이 (원/kg)</h2>
      <div class="legend">
        <span class="leg"><span class="dot" style="background:#378add"></span>{s['last_year']}년 <b style="color:#378add;margin-left:4px">{f"{s['last_year_avg']:,}원" if s['last_year_avg'] else '-'}</b></span>
        <span class="leg"><span class="dot" style="background:#0f7a5f"></span>{s['this_year']}년 <b style="color:#0f7a5f;margin-left:4px">{f"{s['this_year_avg']:,}원" if s['this_year_avg'] else '-'}</b></span>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="mc_cmp"></canvas></div>
    <div style="margin-top:20px">
      <table id="cmpTable" style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="border-bottom:1.5px solid #d3d1c7;background:#f8f7f5">
            <th style="text-align:left;padding:8px 12px;color:#666;font-weight:600">월</th>
            <th style="text-align:right;padding:8px 12px;color:#378add;font-weight:600">{s['last_year']}년</th>
            <th style="text-align:right;padding:8px 12px;color:#0f7a5f;font-weight:600">{s['this_year']}년</th>
            <th style="text-align:right;padding:8px 12px;color:#666;font-weight:600">전년 대비</th>
          </tr>
        </thead>
        <tbody id="cmpTableBody"></tbody>
        <tfoot>
          <tr style="border-top:1.5px solid #d3d1c7;background:#f8f7f5;font-weight:600">
            <td style="padding:8px 12px">연평균</td>
            <td style="padding:8px 12px;text-align:right;color:#378add">{f"{s['last_year_avg']:,}원" if s['last_year_avg'] else '—'}</td>
            <td style="padding:8px 12px;text-align:right;color:#0f7a5f">{f"{s['this_year_avg']:,}원" if s['this_year_avg'] else '—'}</td>
            <td style="padding:8px 12px;text-align:right">{"<span style='color:#e24b4a'>▲ " + f"{s['this_year_avg']-s['last_year_avg']:,}원</span>" if s['this_year_avg'] and s['last_year_avg'] and s['this_year_avg']>s['last_year_avg'] else "<span style='color:#378add'>▼ " + f"{abs(s['this_year_avg']-s['last_year_avg']):,}원</span>" if s['this_year_avg'] and s['last_year_avg'] else '—'}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>




  <div class="card">
    <div class="card-head">
      <h2>연도별 경락단가 추이 (원/kg)</h2>
      <div class="legend">
        <span class="leg"><span class="dot" style="background:#d4a017"></span>{today.year-3}년</span>
        <span class="leg"><span class="dot" style="background:#888"></span>{today.year-2}년</span>
        <span class="leg"><span class="dot" style="background:#378add"></span>{today.year-1}년</span>
        <span class="leg"><span class="dot" style="background:#0f7a5f"></span>{today.year}년</span>
        <span class="leg"><span class="dot" style="background:#aaa;border:1px dashed #666"></span>평균</span>
      </div>
    </div>
    <div class="chart-wrap" style="height:280px"><canvas id="yearly_chart"></canvas></div>
  </div>

  <div class="footer">
    데이터 출처: 축산물품질평가원 공공데이터 API · 탕박 등외제외 전국 평균<br>
    생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  </div>
</div>

<script>
const priceData  = {price_js};
const farmerData = {farmer_js};
const dealerData = {dealer_js};

const baseOpt = {{
  responsive:true, maintainAspectRatio:false,
  plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y?ctx.parsed.y.toLocaleString()+'원/kg':''}}}}}},
  layout:{{padding:{{left:8,right:8}}}},
  scales:{{
    x:{{grid:{{color:'rgba(0,0,0,0.04)'}},ticks:{{color:'#999',font:{{size:11}}}},offset:true}},
    y:{{grid:{{color:'rgba(0,0,0,0.04)'}},suggestedMin:4000,suggestedMax:8000,
       ticks:{{color:'#999',font:{{size:11}},callback:v=>v?v.toLocaleString():''}}}}
  }}
}};

new Chart(document.getElementById('wc'),{{type:'line',data:{{
  labels:{js(s['wl'])},
  datasets:[
    {{label:'최근',data:{jn(s['wt'])},borderColor:'#0f7a5f',backgroundColor:'#0f7a5f18',pointRadius:5,pointBackgroundColor:'#0f7a5f',tension:0.35,fill:true}},
    {{label:'전년',data:{jn(s['wly'])},borderColor:'#378add',pointRadius:3,tension:0.35,fill:false,borderDash:[2,3],borderWidth:1.5}}
  ]}},options:baseOpt}});

new Chart(document.getElementById('wkc'),{{type:'line',data:{{
  labels:{js(s['wk_labels'])},
  datasets:[
    {{label:'{s['this_year']}년',data:{jn(s['wk_avg'])},borderColor:'#0f7a5f',backgroundColor:'#0f7a5f18',pointRadius:5,pointBackgroundColor:'#0f7a5f',tension:0.35,fill:true}},
    {{label:'{s['last_year']}년',data:{jn(s['wk_ly_avg'])},borderColor:'#378add',pointRadius:3,tension:0.35,fill:false,borderDash:[3,3],borderWidth:1.5}}
  ]}},options:baseOpt}});

// 월별 추이 표 채우기
(function(){{
  const labels={js(s['ml'])};
  const data={jn(s['mt'])};
  let rows='';
  for(let i=0;i<data.length;i++){{
    const val=data[i];
    const prev=i>0?data[i-1]:null;
    const diff=(val&&prev)?val-prev:null;
    const pct=(diff!==null&&prev)?Math.round(Math.abs(diff)/prev*1000)/10:null;
    const valStr=val?val.toLocaleString()+'원':'<span style="color:#ccc">—</span>';
    const diffStr=diff===null?'<span style="color:#ccc">—</span>':diff>0?`<span style="color:#e24b4a">▲ ${{Math.abs(diff).toLocaleString()}}원</span>`:diff<0?`<span style="color:#378add">▼ ${{Math.abs(diff).toLocaleString()}}원</span>`:'<span style="color:#999">변동없음</span>';
    const pctStr=pct===null?'<span style="color:#ccc">—</span>':diff>0?`<span style="color:#e24b4a">+${{pct}}%</span>`:diff<0?`<span style="color:#378add">-${{pct}}%</span>`:'<span style="color:#999">0%</span>';
    rows+=`<tr style="border-bottom:.5px solid #f0ede8"><td style="padding:8px 12px;font-weight:500">${{labels[i]}}</td><td style="padding:8px 12px;text-align:right;color:#0f7a5f;font-weight:500">${{valStr}}</td><td style="padding:8px 12px;text-align:right">${{diffStr}}</td><td style="padding:8px 12px;text-align:right">${{pctStr}}</td></tr>`;
  }}
  document.getElementById('monthlyTableBody').innerHTML=rows;
}})();

new Chart(document.getElementById('mc_this'),{{
  type:'bar',
  data:{{
    labels:{js(s['ml'])},
    datasets:[{{label:'{s['this_year']}년',data:{jn(s['mt'])},backgroundColor:'#0f7a5fcc',borderRadius:5}}]
  }},
  options:{{
    ...baseOpt,
    animation:{{
      onComplete: function(){{
        const chart=this;
        const ctx=chart.ctx;
        ctx.save();
        ctx.font='bold 10px Apple SD Gothic Neo,Malgun Gothic,sans-serif';
        ctx.textAlign='center';
        ctx.textBaseline='middle';
        const meta=chart.getDatasetMeta(0);
        meta.data.forEach((bar,j)=>{{
          const val=chart.data.datasets[0].data[j];
          if(!val)return;
          ctx.fillStyle='#0f7a5f';
          ctx.fillText(val.toLocaleString(),bar.x,bar.y-6);
        }});
        ctx.restore();
      }}
    }}
  }}
}});

const cmpThis={jn(s['cmp_this'])};
const cmpLast={jn(s['cmp_last'])};
const cmpDiff={jn(cmp_diff)};
// 전년대비 표 채우기
(function(){{
  const labels=['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];
  let rows='';
  for(let i=0;i<12;i++){{
    const ly=cmpLast[i];
    const ty=cmpThis[i];
    const diff=(ty&&ly)?ty-ly:null;
    const pct=(diff!==null&&ly)?Math.round(Math.abs(diff)/ly*1000)/10:null;
    const pctStr=pct!==null?` (${{diff>=0?'+':'-'}}${{pct}}%)`:'';
    const diffStr=diff===null?'<span style="color:#ccc">—</span>':diff>0?`<span style="color:#e24b4a">▲ ${{Math.abs(diff).toLocaleString()}}원${{pctStr}}</span>`:diff<0?`<span style="color:#378add">▼ ${{Math.abs(diff).toLocaleString()}}원${{pctStr}}</span>`:'<span style="color:#999">변동없음</span>';
    const lyStr=ly?ly.toLocaleString()+'원':'<span style="color:#ccc">—</span>';
    const tyStr=ty?ty.toLocaleString()+'원':'<span style="color:#ccc">—</span>';
    rows+=`<tr style="border-bottom:.5px solid #f0ede8"><td style="padding:8px 12px;font-weight:500">${{labels[i]}}</td><td style="padding:8px 12px;text-align:right;color:#378add">${{lyStr}}</td><td style="padding:8px 12px;text-align:right;color:#0f7a5f">${{tyStr}}</td><td style="padding:8px 12px;text-align:right">${{diffStr}}</td></tr>`;
  }}
  document.getElementById('cmpTableBody').innerHTML=rows;
}})();
new Chart(document.getElementById('mc_cmp'),{{
  type:'bar',
  data:{{
    labels:{js(s['cmp_labels'])},
    datasets:[
      {{label:'{s['last_year']}년',data:cmpLast,backgroundColor:'#378add99',borderRadius:5}},
      {{label:'{s['this_year']}년',data:cmpThis,backgroundColor:'#0f7a5fcc',borderRadius:5}}
    ]
  }},
  options:{{
    ...baseOpt,
    plugins:{{
      ...baseOpt.plugins,
      tooltip:{{
        callbacks:{{
          label:ctx=>ctx.parsed.y?ctx.parsed.y.toLocaleString()+'원/kg':'',
          afterBody:items=>{{
            const i=items[0].dataIndex;
            const d=cmpDiff[i];
            if(d===null)return'';
            return['전년 대비: '+(d>=0?'+':'')+d.toLocaleString()+'원'];
          }}
        }}
      }}
    }},
    animation:{{
      onComplete: function(){{
        const chart=this;
        const ctx=chart.ctx;
        ctx.save();
        ctx.font='bold 10px Apple SD Gothic Neo,Malgun Gothic,sans-serif';
        ctx.textAlign='center';
        ctx.textBaseline='middle';
        chart.data.datasets.forEach((dataset,i)=>{{
          const meta=chart.getDatasetMeta(i);
          meta.data.forEach((bar,j)=>{{
            const val=dataset.data[j];
            if(!val)return;
            const barHeight=bar.base-bar.y;
            if(barHeight<16)return;
            ctx.fillStyle='rgba(255,255,255,0.9)';
            ctx.fillText(val.toLocaleString(),bar.x,bar.y+barHeight/2);
          }});
        }});
        ctx.restore();
      }}
    }}
  }}
}});

function queryRange(){{
  const s=document.getElementById('startDate').value.replace(/-/g,'');
  const e=document.getElementById('endDate').value.replace(/-/g,'')||s;
  if(!s){{alert('시작일을 선택해주세요.');return;}}
  const keys=Object.keys(priceData).filter(k=>k>=s&&k<=e).sort();
  if(keys.length===0){{alert('해당 기간에 데이터가 없습니다.\\n(주말·공휴일·미인정일은 제외됩니다)');return;}}
  const tbody=document.getElementById('queryTable');
  tbody.innerHTML='';
  let total=0,cnt=0;
  const allKeys=Object.keys(priceData).sort();
  keys.forEach((k,i)=>{{
    const price=priceData[k];
    const farmer=farmerData[k];
    const dealer=dealerData[k];
    const prevK=i>0?keys[i-1]:allKeys[allKeys.indexOf(k)-1];
    const diff=prevK?price-priceData[prevK]:null;
    const diffStr=diff===null?'-':diff>0?`<span style="color:#e24b4a">▲ ${{Math.abs(diff).toLocaleString()}}원</span>`:diff<0?`<span style="color:#378add">▼ ${{Math.abs(diff).toLocaleString()}}원</span>`:'<span style="color:#999">변동없음</span>';
    const farmerStr=farmer?farmer.toLocaleString()+'원':'해당없음';
    const dealerStr=dealer?dealer.toLocaleString()+'원':'해당없음';
    const highlight=(farmer&&dealer&&farmer!==dealer)?'background:#fff8e8':'';
    tbody.innerHTML+=`<tr style="border-bottom:.5px solid #f0ede8;${{highlight}}">
      <td style="padding:9px 10px">${{k.slice(4,6)}}/${{k.slice(6,8)}}</td>
      <td style="padding:9px 10px;text-align:right">${{price.toLocaleString()}}원</td>
      <td style="padding:9px 10px;text-align:right;font-weight:500;color:${{farmer?'#0f7a5f':'#bbb'}}">${{farmerStr}}</td>
      <td style="padding:9px 10px;text-align:right;color:${{dealer&&dealer!==farmer?'#e24b4a':dealer?'#999':'#bbb'}}">${{dealerStr}}</td>
      <td style="padding:9px 10px;text-align:right">${{diffStr}}</td>
    </tr>`;
    if(farmer){{total+=farmer;cnt++;}}
  }});
  const avg=cnt?Math.round(total/cnt):null;
  const avgEl=document.getElementById('queryAvg');
  if(keys.length===1){{
    const k=keys[0];const f=farmerData[k];const d=dealerData[k];
    let html=`📌 ${{k.slice(4,6)}}월 ${{k.slice(6,8)}}일 농가 인정단가: `;
    html+=f?`<span style="color:#0f7a5f;font-size:18px">${{f.toLocaleString()}}원</span>`:`<span style="color:#bbb">해당없음</span>`;
    if(d&&d!==f)html+=` / 거래처: <span style="color:#378add">${{d.toLocaleString()}}원</span>`;
    else if(!d&&f)html+=` / 거래처: <span style="color:#bbb">해당없음</span>`;
    avgEl.innerHTML=html;
  }}else{{
    let html=avg?`📊 기간 평균 (농가 인정단가 기준): <span style="color:#0f7a5f;font-size:16px;font-weight:500">${{avg.toLocaleString()}}원</span>`:`📊 기간 내 인정단가 없음`;
    html+=` <span style="color:#999;font-size:12px">(${{keys[0].slice(4,6)}}/${{keys[0].slice(6,8)}}~${{keys[keys.length-1].slice(4,6)}}/${{keys[keys.length-1].slice(6,8)}}, ${{keys.length}}일)</span>`;
    avgEl.innerHTML=html;
  }}
  document.getElementById('queryResult').style.display='block';
}}


// 연도별 추이 차트
(function(){{
  const yc = {yearly_chart_js};
  const months = ['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];
  const colors = {{
    '{s['this_year']-3}': '#d4a017',
    '{s['this_year']-2}': '#888888',
    '{s['this_year']-1}': '#378add',
    '{s['this_year']}':   '#0f7a5f',
    'avg': '#aaaaaa'
  }};
  const dashes = {{
    '{s['this_year']-3}': [],
    '{s['this_year']-2}': [],
    '{s['this_year']-1}': [],
    '{s['this_year']}':   [],
    'avg': [5,3]
  }};
  const datasets = Object.keys(yc).map(y => ({{
    label: y==='avg'?'평균':y+'년',
    data: yc[y],
    borderColor: colors[y]||'#999',
    backgroundColor: 'transparent',
    borderDash: dashes[y]||[],
    borderWidth: y==='{s['this_year']}'?2.5:1.5,
    pointRadius: y==='{s['this_year']}'?4:3,
    pointBackgroundColor: colors[y]||'#999',
    tension: 0.3,
    fill: false
  }}));
  new Chart(document.getElementById('yearly_chart'), {{
    type: 'line',
    data: {{ labels: months, datasets: datasets }},
    options: {{
      ...baseOpt,
      plugins: {{
        legend: {{
          display: false
        }},
        tooltip: {{
          callbacks: {{
            label: ctx => ctx.parsed.y ? ctx.dataset.label + ': ' + ctx.parsed.y.toLocaleString() + '원/kg' : ''
          }}
        }}
      }}
    }}
  }});
}})();

function clearQuery(){{
  document.getElementById('startDate').value='';
  document.getElementById('endDate').value='';
  document.getElementById('queryResult').style.display='none';
}}
</script>
</body>
</html>"""

def main():
    today = datetime.now(KST).replace(tzinfo=None)  # KST 기준 시각으로 변환 (이후 로직은 naive datetime 그대로 사용)
    print(f'경락단가 자동 업데이트 - {today.strftime("%Y-%m-%d %H:%M")} (KST)')

    holidays = load_holidays()
    data     = collect(today, holidays)

    if not data:
        print('데이터 없음')
        return

    stats = build_stats(data, today, holidays)
    if not stats:
        print('통계 계산 실패')
        return

    print(f'전일 단가: {stats["da"]:,}원')

    html  = make_html(stats, today, data)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print('index.html 생성 완료')

if __name__ == '__main__':
    main()
