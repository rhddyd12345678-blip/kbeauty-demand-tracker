"""관세청 수출입무역통계(품목별) 월별 수집 → data/trade.json

두 가지 경로:
 1) 공공데이터포털 '관세청_품목별 국가별 수출입실적' API (환경변수 DATA_GO_KR_KEY 필요)
    https://www.data.go.kr/data/15101210/openapi.do  (서비스: 품목별 수출입실적)
 2) 키가 없으면 raw/관세청_*.xlsx 를 ingest_excel.py 로 읽는 수동 경로로 폴백
    (unipass.customs.go.kr → 무역통계 → 수출입실적(품목별) → 엑셀 저장 → raw/ 에 복사)

추적 HS 코드는 HS_CODES 에서 관리.
사용: DATA_GO_KR_KEY=... python scripts/fetch_trade.py
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from common import load_json, save_json, merge_long, update_meta, to_number

HS_CODES = {
    "3304": "미용·메이크업·기초화장품",
    "330499": "기타 기초화장품(3304.99)",
    "330420": "눈화장용",
    "330430": "매니큐어·페디큐어",
    "901890": "그 밖의 의료기기(9018.90)",
    "300490": "기타 의약품(3004.90, 톡신 등)",
}

API = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"


def fetch_hs(key: str, hs: str, start: str, end: str) -> list[dict]:
    params = {"serviceKey": key, "strtYymm": start, "endYymm": end, "hsSgn": hs, "cntyCd": ""}
    r = requests.get(API, params=params, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = []
    for it in root.iter("item"):
        ym = it.findtext("year")  # 'YYYY.MM' 또는 '총계'
        if not ym or not ym[:4].isdigit():
            continue
        rows.append({
            "period": ym.replace(".", "-"),
            "freq": "M",
            "hs": hs,
            "item": HS_CODES.get(hs, hs),
            "export_ton": to_number(it.findtext("expWgt")),
            "export_usd_k": (to_number(it.findtext("expDlr")) or 0) / 1000,  # 달러 → 천 달러
            "import_ton": to_number(it.findtext("impWgt")),
            "import_usd_k": (to_number(it.findtext("impDlr")) or 0) / 1000,
            "balance_usd_k": (to_number(it.findtext("balPayments")) or 0) / 1000,
            "source": "data.go.kr",
        })
    return rows


def main() -> None:
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        print("[skip] DATA_GO_KR_KEY 없음 → raw/관세청_*.xlsx 수동 경로(ingest_excel.py) 사용")
        update_meta("trade", "manual", "API 키 없음, 엑셀 ingest 사용")
        return
    now = datetime.now()
    start = f"{now.year - 5}01"
    end = now.strftime("%Y%m")
    new = []
    for hs in HS_CODES:
        try:
            r = fetch_hs(key, hs, start, end)
            print(f"{hs}: {len(r)}행")
            new += r
        except Exception as e:
            print(f"[warn] {hs}: {e}")
    if new:
        save_json("trade.json", merge_long(load_json("trade.json", []), new, ("hs", "freq", "period")))
        update_meta("trade", "ok", f"API {len(new)}행")


if __name__ == "__main__":
    main()
