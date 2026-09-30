"""KOSPI·KOSDAQ 지수 (일) — 공공데이터포털 '금융위원회_지수시세정보'(15094807, 활용신청 필요)

GET https://apis.data.go.kr/1160100/GetMarketIndexInfoService_V2/getStockMarketIndex_V2
  serviceKey, resultType=json, idxNm(코스피|코스닥), beginBasDt, endBasDt, numOfRows, pageNo
신청 전(코드 30)에는 기존 트래커에서 이관한 지수(네이버 차트, 2021~)가 화면에 쓰인다.
"""
from __future__ import annotations

import sys
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import CollectError, append_obs, check_gateway, config, http_get, need_key, now_kst, read_obs  # noqa: E402

NAME = "index"
API = "https://apis.data.go.kr/1160100/GetMarketIndexInfoService_V2/getStockMarketIndex_V2"
SOURCE = "금융위원회 지수시세정보(공공데이터포털)"
INDICES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}


def run() -> tuple[int, str]:
    cfg = config()
    k = need_key("DATA_GO_KR_KEY")
    key = urllib.parse.unquote(k) if "%" in k else k
    have: dict[str, str] = {}
    for r in read_obs(NAME):
        if r["출처"] == SOURCE:
            have[r["구분"]] = max(have.get(r["구분"], ""), r["기준일"])
    end = now_kst().strftime("%Y%m%d")
    rows, last = [], ""
    for label, nm in INDICES.items():
        a = (date.fromisoformat(have[label]) - timedelta(days=7)).strftime("%Y%m%d") if label in have else cfg["backfill_start"] + "01"
        page = 1
        while True:
            r = http_get(API, params={"serviceKey": key, "resultType": "json", "idxNm": nm, "beginBasDt": a, "endBasDt": end,
                                      "numOfRows": 1000, "pageNo": page})
            check_gateway(r.text)
            if r.status_code != 200 or not r.text.lstrip().startswith("{"):
                raise CollectError(f"HTTP {r.status_code}")
            body = r.json().get("response", {}).get("body", {})
            items = (body.get("items") or {}).get("item", []) if isinstance(body.get("items"), dict) else []
            items = [items] if isinstance(items, dict) else items
            for x in items:
                if x.get("idxNm") != nm or not x.get("clpr"):
                    continue
                d = f"{x['basDt'][:4]}-{x['basDt'][4:6]}-{x['basDt'][6:]}"
                last = max(last, d)
                rows.append({"기준일": d, "축": "밸류에이션", "지표": "지수 종가", "구분": label, "값": float(x["clpr"]),
                             "단위": "pt", "출처": SOURCE})
            if page * 1000 >= int(body.get("totalCount") or 0):
                break
            page += 1
            time.sleep(0.2)
    if not rows:
        raise CollectError("받은 값 없음")
    return append_obs(NAME, rows), f"~{last}"


if __name__ == "__main__":
    from lib.core import load_env
    load_env()
    print(run())
