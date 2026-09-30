"""DART OpenAPI — 분기 재무(연결·별도)와 직원 수

- 고유번호: /api/corpCode.xml (zip) → 종목코드→corp_code 매핑, data/dart_corp_codes.json 캐시
- 재무: /api/fnlttSinglAcntAll.json  corp_code, bsns_year, reprt_code(11013 1Q·11012 반기·11014 3Q·11011 사업), fs_div(CFS|OFS)
    손익 항목은 thstrm_amount가 해당 분기 3개월 값(사업보고서는 연간). 4Q 단독 = 연간 − (1Q+2Q+3Q)은 build에서 계산.
    기준일: '2025Q1'..'2025Q3', 연간은 '2025FY'. 재무상태표 항목은 분기말 잔액('2025Q4' = 사업보고서 기말).
- 직원: /api/empSttus.json  (별도 기준 인원. 분기보고서는 생략하는 회사가 많아 사실상 반기·연간)

연결(CFS)이 없으면(자회사 없는 회사) 별도(OFS)만 저장. 구분 = '회사명|연결' 또는 '회사명|별도'.
이미 받은 (연도, 보고서, 연결/별도)는 건너뛰고, 최근 4개 보고서는 정정공시 반영을 위해 매번 다시 받는다.
"""
from __future__ import annotations

import io
import json
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import ROOT, CollectError, append_obs, config, http_get, need_key, now_kst, read_obs  # noqa: E402

NAME = "dart"
BASE = "https://opendart.fss.or.kr/api"
SOURCE = "DART OpenAPI"
CACHE = ROOT / "data" / "dart_corp_codes.json"
REPORTS = [("11013", "Q1"), ("11012", "Q2"), ("11014", "Q3"), ("11011", "FY")]

# (지표, 재무제표 구분, account_id 후보, account_nm 후보)
ACCOUNTS = [
    ("매출액", "IS", ["ifrs-full_Revenue"], ["매출액", "영업수익", "수익(매출액)", "매출"]),
    ("매출총이익", "IS", ["ifrs-full_GrossProfit"], ["매출총이익", "매출총이익(손실)"]),
    ("판관비", "IS", ["dart_TotalSellingGeneralAdministrativeExpenses", "ifrs-full_SellingGeneralAndAdministrativeExpense"], ["판매비와관리비", "판매비와 관리비"]),
    ("영업이익", "IS", ["dart_OperatingIncomeLoss"], ["영업이익(손실)", "영업이익", "영업손익"]),
    ("당기순이익", "IS", ["ifrs-full_ProfitLoss"], ["당기순이익(손실)", "당기순이익", "분기순이익", "반기순이익"]),
    ("지배주주순이익", "IS", ["ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs_ProfitLossAttributableToOwnersOfParent"],
     ["지배기업의 소유주에게 귀속되는 당기순이익", "지배기업 소유주지분", "지배기업의 소유주지분", "지배기업소유주지분",
      "지배기업 소유주에게 귀속되는 당기순이익(손실)", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", "지배주주지분"]),
    ("재고자산", "BS", ["ifrs-full_Inventories"], ["재고자산"]),
    ("매출채권", "BS", ["dart_ShortTermTradeReceivable", "ifrs-full_CurrentTradeReceivables", "ifrs-full_TradeAndOtherCurrentReceivables"], ["매출채권", "매출채권 및 기타채권", "매출채권및기타채권"]),
    ("계약부채", "BS", ["ifrs-full_CurrentContractLiabilities", "ifrs-full_ContractLiabilities"], ["계약부채", "유동계약부채", "선수금"]),
]


def api(path: str, **params) -> dict:
    r = http_get(f"{BASE}/{path}", params=params)
    d = r.json()
    st = d.get("status")
    if st in ("010", "011", "012", "901"):
        raise CollectError(f"DART 키 오류 {st}: {d.get('message')}")
    if st == "020":
        raise CollectError("DART 일일 한도 초과(020)")
    return d


def corp_codes(key: str, stock_codes: list[str]) -> dict[str, str]:
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    if all(c in cache for c in stock_codes):
        return cache
    r = http_get(f"{BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=120)
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        raise CollectError(f"corpCode 응답이 zip 아님: {r.text[:120]}")
    root = ET.fromstring(z.read(z.namelist()[0]))
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            cache[sc] = el.findtext("corp_code")
    CACHE.write_text(json.dumps({c: cache[c] for c in stock_codes if c in cache}, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


def num(s: str | None) -> float | None:
    s = (s or "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pick(items: list[dict], ind: str, sj: str, ids: list[str], names: list[str]) -> float | None:
    divs = ("IS", "CIS") if sj == "IS" else ("BS",)
    pool = [x for x in items if x.get("sj_div") in divs]
    for aid in ids:
        for x in pool:
            if x.get("account_id") == aid and num(x.get("thstrm_amount")) is not None:
                return num(x["thstrm_amount"])
    for nm in names:
        for x in pool:
            if x.get("account_nm", "").replace(" ", "") == nm.replace(" ", "") and num(x.get("thstrm_amount")) is not None:
                return num(x["thstrm_amount"])
    return None


def employees(items: list[dict]) -> float | None:
    def is_total(x):
        t = f"{x.get('fo_bbm', '')}{x.get('sexdstn', '')}"
        return any(w in t for w in ("합계", "소계", "총계")) or x.get("fo_bbm", "").strip() in ("계", "전체")
    detail = [num(x.get("sm")) for x in items if not is_total(x) and num(x.get("sm")) is not None]
    if detail:
        return sum(detail)
    totals = [num(x.get("sm")) for x in items if num(x.get("sm")) is not None]
    return max(totals) if totals else None


def run() -> tuple[int, str]:
    cfg = config()
    key = need_key("DART_KEY")
    comps = cfg["companies"]
    codes = corp_codes(key, [c["code"] for c in comps])
    now = now_kst()
    y0 = int(cfg["backfill_start"][:4]) - 1  # TTM·YoY 계산용으로 한 해 더
    obs = read_obs(NAME)
    done = {(r["구분"], r["기준일"]) for r in obs if r["지표"] == "매출액"}
    done_emp = {(r["구분"], r["기준일"]) for r in obs if r["지표"] == "직원수"}
    # 최근 4개 보고서는 매번 재확인 (정정공시)
    recent = set()
    y, q = now.year, (now.month - 1) // 3
    for _ in range(5):
        recent.add((y, q))
        q -= 1
        if q < 0:
            y, q = y - 1, 3
    rows, missing, calls = [], [], 0
    for c in comps:
        cc = codes.get(c["code"])
        if not cc:
            missing.append(f"{c['name']}: DART 고유번호 없음")
            continue
        for year in range(y0, now.year + 1):
            for qi, (rc, tag) in enumerate(REPORTS):
                period = f"{year}{tag}"
                if (year, qi) > (now.year, (now.month - 1) // 3):
                    continue
                for fs, fs_nm in (("CFS", "연결"), ("OFS", "별도")):
                    g = f"{c['name']}|{fs_nm}"
                    if (g, period) in done and (year, qi) not in recent:
                        continue
                    d = api("fnlttSinglAcntAll.json", crtfc_key=key, corp_code=cc, bsns_year=str(year), reprt_code=rc, fs_div=fs)
                    calls += 1
                    if d.get("status") != "000":
                        continue  # 013: 해당 보고서 없음(미제출·연결 없음)
                    items = d.get("list", [])
                    for ind, sj, ids, names in ACCOUNTS:
                        if ind == "지배주주순이익" and fs == "OFS":
                            continue
                        v = pick(items, ind, sj, ids, names)
                        if v is None:
                            continue
                        when = period if sj == "IS" else (f"{year}Q4" if tag == "FY" else period)
                        rows.append({"기준일": when, "축": "기업", "지표": ind, "구분": g, "값": v, "단위": "원", "출처": SOURCE})
                    time.sleep(0.15)
                ge = f"{c['name']}|별도"
                emp_period = f"{year}Q4" if tag == "FY" else period
                if (ge, emp_period) in done_emp and (year, qi) not in recent:
                    continue
                d = api("empSttus.json", crtfc_key=key, corp_code=cc, bsns_year=str(year), reprt_code=rc)
                calls += 1
                if d.get("status") == "000":
                    v = employees(d.get("list", []))
                    if v:
                        rows.append({"기준일": emp_period, "축": "기업", "지표": "직원수",
                                     "구분": ge, "값": v, "단위": "명", "출처": SOURCE})
                time.sleep(0.15)
    if not rows:
        raise CollectError("받은 값 없음" + (f" ({missing[0]})" if missing else ""))
    added = append_obs(NAME, rows)
    return added, f"API {calls}회" + (f", {'; '.join(missing)}" if missing else "")


if __name__ == "__main__":
    from lib.core import load_env
    load_env()
    print(run())
