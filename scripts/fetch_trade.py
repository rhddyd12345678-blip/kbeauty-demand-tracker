"""관세청 수출입무역통계(품목별) 월별 수집 → data/trade.json

두 가지 경로:
 1) 공공데이터포털 '관세청_품목별 수출입실적(GW)' API (환경변수 DATA_GO_KR_KEY 필요)
    https://www.data.go.kr/data/15101609/openapi.do
    GET https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList
      요청: serviceKey(필수), strtYymm(필수, YYYYMM), endYymm(필수, YYYYMM), hsSgn(옵션, HS 2/4/6/10단위)
      응답(XML): response/header/{resultCode,resultMsg}, response/body/items/item/
                 {year(기간 'YYYY.MM' 또는 '총계'), hsCode, statKor(품목명), expDlr·impDlr(달러),
                  expWgt·impWgt(KG), balPayments(무역수지, 달러)}
 2) 키가 없으면 raw/관세청_*.xlsx 를 ingest_excel.py 로 읽는 수동 경로로 폴백
    (unipass.customs.go.kr → 무역통계 → 수출입실적(품목별) → 엑셀 저장 → raw/ 에 복사)

추적 HS 코드는 HS_CODES 에서 관리. 조회기간 제한에 대비해 12개월 단위로 나눠 요청.
처음엔 START_YEAR 부터 백필, 이후엔 최근 REFRESH_MONTHS 개월만 다시 받음(매월 15일경 정정분 반영).
사용: DATA_GO_KR_KEY=... python scripts/fetch_trade.py
"""
from __future__ import annotations

import os
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date

import requests

from common import load_json, save_json, merge_long, update_meta, to_number

HS_CODES = {
    "3304": "미용·메이크업·기초화장품(3304)",
    "330499": "기타 기초화장품(3304.99)",
    "330410": "입술화장용(3304.10)",
    "330420": "눈화장용(3304.20)",
    "330430": "매니큐어·페디큐어(3304.30)",
    "300249": "보툴리눔 톡신 등(3002.49)",
    "300490": "기타 의약품(3004.90)",
    "901890": "그 밖의 의료기기(9018.90)",
}

API = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
START_YEAR = 2019
REFRESH_MONTHS = 24


class ApiError(Exception):
    pass


def service_key() -> str | None:
    key = (os.environ.get("DATA_GO_KR_KEY") or "").strip()
    # 포털의 '인코딩' 키를 넣었으면 디코딩 (requests 가 다시 인코딩하므로 이중 인코딩 방지)
    return (urllib.parse.unquote(key) if "%" in key else key) or None


def month_chunks(start: str, end: str, size: int = 12) -> list[tuple[str, str]]:
    """'YYYYMM' 구간을 size 개월 단위로 분할."""
    a = int(start[:4]) * 12 + int(start[4:]) - 1
    b = int(end[:4]) * 12 + int(end[4:]) - 1
    out = []
    while a <= b:
        c = min(a + size - 1, b)
        out.append((f"{a // 12}{a % 12 + 1:02d}", f"{c // 12}{c % 12 + 1:02d}"))
        a = c + 1
    return out


def call(key: str, hs: str, start: str, end: str) -> list[ET.Element]:
    params = {"serviceKey": key, "strtYymm": start, "endYymm": end, "hsSgn": hs}
    r = None
    for attempt in range(3):
        try:
            r = requests.get(API, params=params, timeout=60)
            break
        except requests.RequestException as e:
            if attempt == 2:
                raise ApiError(f"요청 실패: {e}")
            time.sleep(3)
    text = r.text.strip()
    if r.status_code != 200:
        raise ApiError(f"HTTP {r.status_code}: {text[:200]}")
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        raise ApiError(f"XML 아님: {text[:200]}")
    # 게이트웨이 오류 (인증키 미등록 등)
    auth = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg")
    if auth:
        raise ApiError(f"게이트웨이 오류: {auth} ({root.findtext('.//returnReasonCode')})")
    code = root.findtext(".//header/resultCode")
    if code not in (None, "00", "0", "000"):
        raise ApiError(f"resultCode={code} {root.findtext('.//header/resultMsg')}")
    return list(root.iter("item"))


def aggregate(hs: str, items: list[ET.Element]) -> list[dict]:
    """월별 1행으로 집계. 요청 HS와 같은 코드 행이 있으면 그것을, 없으면 가장 하위 단위 행의 합계를 사용."""
    by_period: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        ym = (it.findtext("year") or "").strip()
        if not ym[:4].isdigit():  # '총계' 행 제외
            continue
        by_period[ym.replace(".", "-")[:7]].append({
            "hs": (it.findtext("hsCode") or "").strip().split(".")[0],
            "exp": to_number(it.findtext("expDlr")) or 0,
            "imp": to_number(it.findtext("impDlr")) or 0,
            "expw": to_number(it.findtext("expWgt")) or 0,
            "impw": to_number(it.findtext("impWgt")) or 0,
        })
    rows = []
    for period, lst in sorted(by_period.items()):
        exact = [x for x in lst if x["hs"] == hs]
        if exact:
            use = exact[:1]
        else:
            depth = max(len(x["hs"]) for x in lst)
            use = [x for x in lst if len(x["hs"]) == depth]
        exp, imp = sum(x["exp"] for x in use), sum(x["imp"] for x in use)
        rows.append({
            "period": period,
            "freq": "M",
            "hs": hs,
            "item": HS_CODES.get(hs, hs),
            "export_ton": round(sum(x["expw"] for x in use) / 1000, 1),   # KG → 톤
            "export_usd_k": round(exp / 1000, 1),                          # 달러 → 천 달러
            "import_ton": round(sum(x["impw"] for x in use) / 1000, 1),
            "import_usd_k": round(imp / 1000, 1),
            "balance_usd_k": round((exp - imp) / 1000, 1),
            "source": "data.go.kr",
            "n_lines": len(use),
        })
    return rows


def main() -> None:
    key = service_key()
    if not key:
        print("[skip] DATA_GO_KR_KEY 없음 → raw/관세청_*.xlsx 수동 경로(ingest_excel.py) 사용")
        update_meta("trade", "manual", "API 키 없음, 엑셀 ingest 사용")
        return
    existing = load_json("trade.json", [])
    have_api = {r["hs"] for r in existing if r.get("source") == "data.go.kr"}
    today = date.today()
    now_idx = today.year * 12 + today.month - 1
    end = f"{today.year}{today.month:02d}"
    new, errors = [], []
    for hs in HS_CODES:
        if hs in have_api:
            s_idx = now_idx - (REFRESH_MONTHS - 1)
            start = f"{s_idx // 12}{s_idx % 12 + 1:02d}"
        else:
            start = f"{START_YEAR}01"
        items: list[ET.Element] = []
        try:
            for s, e in month_chunks(start, end):
                items += call(key, hs, s, e)
                time.sleep(0.3)
        except ApiError as ex:
            errors.append(f"{hs}: {ex}")
            print(f"[warn] {hs}: {ex}")
            if "게이트웨이" in str(ex) or "HTTP 401" in str(ex) or "HTTP 403" in str(ex):
                break  # 인증 문제면 나머지도 실패하므로 중단
            continue
        rows = aggregate(hs, items)
        if rows:
            L = rows[-1]
            print(f"{hs} {HS_CODES[hs]}: 원자료 {len(items)}행 → {len(rows)}개월 ({rows[0]['period']}~{L['period']}), "
                  f"월별 사용 행수 {sorted({r['n_lines'] for r in rows})}, 최근월 수출 {L['export_usd_k'] / 1000:,.1f}백만$")
        else:
            print(f"{hs}: 데이터 없음 (원자료 {len(items)}행)")
        for r in rows:
            r.pop("n_lines", None)
        new += rows
    if new:
        save_json("trade.json", merge_long(existing, new, ("hs", "freq", "period")))
        latest = max(r["period"] for r in new)
        update_meta("trade", "ok" if not errors else "partial",
                    f"API {len(new)}행, ~{latest}" + (f" / 실패 {len(errors)}건" if errors else ""))
    else:
        update_meta("trade", "error", (errors[0] if errors else "응답 없음")[:120])


if __name__ == "__main__":
    main()
