"""네이버 데이터랩 통합검색어 트렌드 API → data/naver_trend.json (열 형식)

환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 필요 (developers.naver.com → 데이터랩 검색어트렌드).
키가 없으면 raw/네이버트렌드_*.xlsx 수동 경로(ingest_excel.py) 사용.

주의: API 값은 조회 그룹 내 최대치를 100으로 한 상대값이라, 엑셀 다운로드 값과 스케일이 다를 수
있음. 그래서 API 결과는 별도 키 접두사 'api:' 로 저장해 섞이지 않게 함.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import requests

from common import load_json, save_json, update_meta

KEYWORDS = ["안티에이징", "얼리 안티에이징", "스킨부스터", "보톡스", "필러"]
AGES = {  # 데이터랩 연령 코드
    "19~24": ["3", "4"], "25~29": ["5"], "30~34": ["6"], "35~39": ["7"],
    "40~44": ["8"], "45~49": ["9"], "50~54": ["10"],
}
API = "https://openapi.naver.com/v1/datalab/search"


def fetch(cid: str, csec: str, age_codes: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    body = {
        "startDate": start, "endDate": end, "timeUnit": "date",
        "keywordGroups": [{"groupName": k, "keywords": [k]} for k in KEYWORDS],
        "ages": age_codes,
    }
    r = requests.post(API, headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec,
                                    "Content-Type": "application/json"},
                      data=json.dumps(body), timeout=30)
    r.raise_for_status()
    out = {}
    for res in r.json()["results"]:
        out[res["title"]] = {d["period"]: d["ratio"] for d in res["data"]}
    return out


def main() -> None:
    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        print("[skip] NAVER_CLIENT_ID/SECRET 없음 → raw 엑셀 수동 경로 사용")
        update_meta("naver_trend", "manual", "API 키 없음, 엑셀 ingest 사용")
        return
    data = load_json("naver_trend.json", {"dates": [], "series": {}})
    table = {k: dict(zip(data["dates"], v)) for k, v in data["series"].items()}
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    for age, codes in AGES.items():
        try:
            res = fetch(cid, csec, codes, start, end)
        except Exception as e:
            print(f"[warn] {age}: {e}")
            continue
        for kw, series in res.items():
            table.setdefault(f"api:{age}|{kw}", {}).update(series)
    dates = sorted({d for s in table.values() for d in s})
    series = {k: [round(s[d], 3) if d in s else None for d in dates] for k, s in sorted(table.items())}
    save_json("naver_trend.json", {"dates": dates, "series": series})
    update_meta("naver_trend", "ok", "API 갱신")


if __name__ == "__main__":
    main()
