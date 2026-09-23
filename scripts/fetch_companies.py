"""ODM 3사 + 메디컬 에스테틱 3사 실적·밸류에이션·주가 → data/companies.json

소스 (키 불필요)
 - 재무 (연간 5년 + 추정 3년, 분기 5개 + 추정 3개): 네이버 증권 기업분석(WiseReport) 주요재무정보
   실패 시 m.stock.naver.com 재무 API로 폴백. 지난 실행에서 받은 과거 기간은 유지(누적)하고 추정치만 덮어씀.
 - 현재가·PER·PBR·컨센서스 목표가: m.stock.naver.com integration
 - 일별 종가 (2021~) + KOSPI/KOSDAQ 지수: api.stock.naver.com chart
사용: python scripts/fetch_companies.py
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from common import load_json, save_json, update_meta, to_number, now_kst, KST

COMPANIES = [
    {"code": "192820", "name": "코스맥스", "group": "ODM"},
    {"code": "161890", "name": "한국콜마", "group": "ODM"},
    {"code": "241710", "name": "코스메카코리아", "group": "ODM"},
    {"code": "145020", "name": "휴젤", "group": "메디컬 에스테틱"},
    {"code": "214450", "name": "파마리서치", "group": "메디컬 에스테틱"},
    {"code": "086900", "name": "메디톡스", "group": "메디컬 에스테틱"},
]
INDICES = ["KOSPI", "KOSDAQ"]
PRICE_FROM = "20210101"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

# WiseReport 행 이름 → 저장 키
ROWS = {
    "매출액": "rev", "영업이익(발표기준)": "op", "당기순이익": "ni", "당기순이익(지배)": "ni_ctrl",
    "영업이익률": "opm", "순이익률": "npm", "ROE(%)": "roe", "부채비율": "debt_ratio",
    "EPS(원)": "eps", "PER(배)": "per", "BPS(원)": "bps", "PBR(배)": "pbr", "현금DPS(원)": "dps",
    "현금배당수익률": "div_yield", "CAPEX": "capex", "FCF": "fcf", "발행주식수(보통주)": "shares",
}
# 모바일 API 행 이름 → 저장 키 (폴백용)
M_ROWS = {"매출액": "rev", "영업이익": "op", "당기순이익": "ni", "지배주주순이익": "ni_ctrl", "영업이익률": "opm",
          "순이익률": "npm", "ROE": "roe", "부채비율": "debt_ratio", "EPS": "eps", "PER": "per", "BPS": "bps",
          "PBR": "pbr", "주당배당금": "dps"}


def wisereport(code: str) -> dict[str, dict]:
    """{'Y': {period: {key: val, 'e': bool}}, 'Q': {...}}"""
    s = requests.Session()
    s.headers.update(UA)
    ref = f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}"
    page = s.get(ref, timeout=20).text
    enc = re.search(r"encparam\s*:\s*'([^']+)'", page).group(1)
    cid = re.search(r"id\s*:\s*'([^']+)'", page).group(1)
    out = {}
    for fq in "YQ":
        html = s.get("https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx",
                     params={"cmp_cd": code, "fin_typ": 0, "freq_typ": fq, "encparam": enc, "id": cid},
                     headers={"Referer": ref}, timeout=20).text
        table = BeautifulSoup(html, "lxml").select("table")[-1]
        trs = table.select("tr")
        heads = [th.get_text(" ", strip=True) for th in trs[1].select("th")]
        periods = []
        for h in heads:
            m = re.match(r"(\d{4})/(\d{2})", h)
            periods.append((f"{m.group(1)}-{m.group(2)}", "(E)" in h) if m else (None, False))
        data = {p: {"e": e} for p, e in periods if p}
        for tr in trs[2:]:
            cells = [c.get_text(" ", strip=True) for c in tr.select("th,td")]
            key = ROWS.get(cells[0])
            if not key:
                continue
            for (p, _), v in zip(periods, cells[1:]):
                if p:
                    data[p][key] = to_number(v)
        if not any(len(v) > 3 for v in data.values()):
            raise ValueError(f"{fq} 표 비어있음")
        out[fq] = data
    return out


def mobile_finance(code: str) -> dict[str, dict]:
    out = {}
    for fq, path in (("Y", "annual"), ("Q", "quarter")):
        j = requests.get(f"https://m.stock.naver.com/api/stock/{code}/finance/{path}", headers=UA, timeout=20).json()
        fi = j["financeInfo"]
        data = {}
        for t in fi["trTitleList"]:
            data[t["key"][:4] + "-" + t["key"][4:6]] = {"e": t["isConsensus"] == "Y"}
        for row in fi["rowList"]:
            key = M_ROWS.get(row["title"])
            if not key:
                continue
            for k, c in row["columns"].items():
                data[k[:4] + "-" + k[4:6]][key] = to_number(c["value"])
        out[fq] = data
    return out


def integration(code: str) -> dict:
    j = requests.get(f"https://m.stock.naver.com/api/stock/{code}/integration", headers=UA, timeout=20).json()
    info = {t["code"]: t["value"] for t in j.get("totalInfos", [])}
    num = lambda k: to_number(re.sub(r"[^\d.\-]", "", info.get(k, "")) or None)
    cons = j.get("consensusInfo") or {}
    return {
        "per_ttm": num("per"), "eps_ttm": num("eps"), "per_fwd_naver": num("cnsPer"), "pbr": num("pbr"),
        "bps": num("bps"), "div_yield": num("dividendYieldRatio"), "foreign_rate": num("foreignRate"),
        "high52": num("highPriceOf52Weeks"), "low52": num("lowPriceOf52Weeks"),
        "target_price": to_number(cons.get("priceTargetMean")), "recomm": to_number(cons.get("recommMean")),
        "cons_date": cons.get("createDate"),
    }


def prices(kind: str, code: str) -> list[list]:
    end = datetime.now(KST).strftime("%Y%m%d") + "2359"
    r = requests.get(f"https://api.stock.naver.com/chart/domestic/{kind}/{code}/day",
                     params={"startDateTime": PRICE_FROM + "0000", "endDateTime": end}, headers=UA, timeout=30)
    r.raise_for_status()
    return [[f"{d['localDate'][:4]}-{d['localDate'][4:6]}-{d['localDate'][6:]}", d["closePrice"]] for d in r.json()]


def merge_fin(old: dict, new: dict) -> dict:
    """과거 실적은 누적 보관, 새로 받은 기간(실적·추정)은 덮어씀. 이미 실적이 된 기간의 옛 추정치는 제거."""
    out = {p: v for p, v in (old or {}).items() if not v.get("e")}
    out.update(new)
    return dict(sorted(out.items()))


def ret(px: list[list], days: int | None = None, since: str | None = None):
    if not px:
        return None
    last_d, last = px[-1]
    if since:
        base = next((c for d, c in reversed(px) if d < since), None)
    else:
        cut = (datetime.fromisoformat(last_d) - timedelta(days=days)).strftime("%Y-%m-%d")
        base = next((c for d, c in reversed(px) if d <= cut), None)
    return round((last / base - 1) * 100, 1) if base else None


def derive(c: dict) -> dict:
    """비교표용 핵심 지표 계산."""
    Y, Q, px = c["annual"], c["quarter"], c["prices"]
    price = px[-1][1] if px else None
    ya = [p for p, v in Y.items() if not v.get("e") and v.get("rev") is not None]
    ye = [p for p, v in Y.items() if v.get("e") and v.get("rev") is not None]
    qa = [p for p, v in Q.items() if not v.get("e") and v.get("rev") is not None]
    last_q = qa[-1] if qa else None
    yoy_q = None
    if last_q:
        y, m = last_q.split("-")
        yoy_q = f"{int(y) - 1}-{m}"

    def g(a, b):
        return round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) and b > 0 else None

    fy1 = ye[0] if ye else None
    fy2 = ye[1] if len(ye) > 1 else None
    shares = next((Y[p].get("shares") for p in reversed(ya) if Y[p].get("shares")), None)
    d = {
        "price": price, "date": px[-1][0] if px else None,
        "mcap": round(price * shares / 1e8) if price and shares else None,  # 억원
        "r1d": round((px[-1][1] / px[-2][1] - 1) * 100, 2) if len(px) > 1 else None,
        "r1m": ret(px, 30), "r3m": ret(px, 91), "r1y": ret(px, 365),
        "ytd": ret(px, since=f"{px[-1][0][:4]}-01-01") if px else None,
        "last_q": last_q, "fy1": fy1, "fy2": fy2, "last_fy": ya[-1] if ya else None,
    }
    if last_q:
        cq, pq = Q[last_q], Q.get(yoy_q, {})
        d.update(q_rev=cq.get("rev"), q_op=cq.get("op"), q_opm=cq.get("opm"),
                 q_rev_yoy=g(cq.get("rev"), pq.get("rev")), q_op_yoy=g(cq.get("op"), pq.get("op")))
        # 최근 4분기 합산 (TTM)
        last4 = qa[-4:]
        if len(last4) == 4 and all(Q[p].get("ni_ctrl") is not None for p in last4) and d["mcap"]:
            ttm_ni = sum(Q[p]["ni_ctrl"] for p in last4)
            d["per_ttm"] = round(d["mcap"] / ttm_ni, 1) if ttm_ni > 0 else None
        if len(last4) == 4 and all(Q[p].get("rev") is not None and Q[p].get("op") is not None for p in last4):
            rev4, op4 = sum(Q[p]["rev"] for p in last4), sum(Q[p]["op"] for p in last4)
            d["opm_ttm"] = round(op4 / rev4 * 100, 1) if rev4 else None
    for tag, p in (("fy1", fy1), ("fy2", fy2)):
        if not p:
            continue
        v = Y[p]
        d[f"{tag}_rev"], d[f"{tag}_op"], d[f"{tag}_opm"], d[f"{tag}_roe"] = v.get("rev"), v.get("op"), v.get("opm"), v.get("roe")
        d[f"{tag}_per"] = round(price / v["eps"], 1) if price and v.get("eps") and v["eps"] > 0 else None
        d[f"{tag}_pbr"] = round(price / v["bps"], 2) if price and v.get("bps") else None
    if fy1 and ya:
        d["fy1_rev_g"] = g(Y[fy1].get("rev"), Y[ya[-1]].get("rev"))
        d["fy1_op_g"] = g(Y[fy1].get("op"), Y[ya[-1]].get("op"))
    if fy1 and fy2:
        d["fy2_op_g"] = g(Y[fy2].get("op"), Y[fy1].get("op"))
        # EPS 성장률 기반 PEG (FY1 PER / FY2 EPS 성장률)
        e1, e2 = Y[fy1].get("eps"), Y[fy2].get("eps")
        eg = g(e2, e1)
        d["peg"] = round(d["fy1_per"] / eg, 2) if d.get("fy1_per") and eg and eg > 0 else None
    return d


def main() -> None:
    old = {c["code"]: c for c in load_json("companies.json", {}).get("companies", [])}
    out, errs = [], []
    for meta in COMPANIES:
        code = meta["code"]
        prev = old.get(code, {})
        c = {**meta, "annual": prev.get("annual", {}), "quarter": prev.get("quarter", {}),
             "prices": prev.get("prices", []), "info": prev.get("info", {})}
        try:
            fin, src = wisereport(code), "wisereport"
        except Exception as e:
            print(f"[warn] {meta['name']} wisereport: {e} → 모바일 API 폴백")
            try:
                fin, src = mobile_finance(code), "naver-mobile"
            except Exception as e2:
                fin, src = None, None
                errs.append(f"{meta['name']} 재무")
                print(f"[warn] {meta['name']} finance: {e2}")
        if fin:
            c["annual"], c["quarter"], c["fin_src"] = merge_fin(c["annual"], fin["Y"]), merge_fin(c["quarter"], fin["Q"]), src
        try:
            c["info"] = integration(code)
        except Exception as e:
            errs.append(f"{meta['name']} 시세")
            print(f"[warn] {meta['name']} integration: {e}")
        try:
            c["prices"] = prices("item", code)
        except Exception as e:
            errs.append(f"{meta['name']} 주가")
            print(f"[warn] {meta['name']} prices: {e}")
        c["kpi"] = derive(c)
        out.append(c)
        k = c["kpi"]
        print(f"{meta['name']}: {k.get('price')}원 시총 {k.get('mcap')}억 FY1 PER {k.get('fy1_per')} OPM(최근분기) {k.get('q_opm')}")
    idx = load_json("companies.json", {}).get("indices", {})
    for i in INDICES:
        try:
            idx[i] = prices("index", i)
        except Exception as e:
            print(f"[warn] index {i}: {e}")
    save_json("companies.json", {"updated": now_kst(), "companies": out, "indices": idx})
    update_meta("companies", "ok" if not errs else "partial", ", ".join(errs) or f"{len(out)}개사 갱신")


if __name__ == "__main__":
    main()
