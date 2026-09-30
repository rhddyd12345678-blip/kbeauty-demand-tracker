"""주가·시가총액 (일) — 공공데이터포털 '금융위원회_주식시세정보'

GET https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2
  (2026-09 기준 신청 페이지(15094808) 명세가 V2. 옛 주소 /service/GetStockSecuritiesInfoService/getStockPriceInfo는
   V2로 신청한 키로는 '등록되지 않은 서비스키(30)'가 난다)
  serviceKey, resultType=json, likeSrtnCd(단축코드), beginBasDt, endBasDt, numOfRows, pageNo
  item: basDt, srtnCd, itmsNm, clpr(종가), mrktTotAmt(시가총액), lstgStCnt(상장주식수)
데이터는 다음 영업일 오후에 올라온다(T+1). 후행 PER은 build에서 시총 ÷ 최근 4분기 지배주주순이익으로 계산.
"""
from __future__ import annotations

import sys
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import CollectError, MissingKey, append_obs, check_gateway, config, http_get, need_key, now_kst, read_obs  # noqa: E402

NAME = "stocks"
API = "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
SOURCE = "금융위원회 주식시세정보(공공데이터포털)"


def fetch(key: str, code: str, a: str, b: str) -> list[dict]:
    out, page = [], 1
    while True:
        r = http_get(API, params={"serviceKey": key, "resultType": "json", "likeSrtnCd": code,
                                  "beginBasDt": a, "endBasDt": b, "numOfRows": 1000, "pageNo": page})
        check_gateway(r.text)
        if r.status_code != 200 or not r.text.lstrip().startswith("{"):
            raise CollectError(f"HTTP {r.status_code}: {r.text[:120]} — 활용신청 승인 여부 확인")
        body = r.json().get("response", {}).get("body", {})
        items = body.get("items", {}) or {}
        items = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(items, dict):
            items = [items]
        out += [x for x in items if x.get("srtnCd") == code]
        if page * 1000 >= int(body.get("totalCount") or 0):
            return out
        page += 1
        time.sleep(0.2)


def run() -> tuple[int, str]:
    cfg = config()
    k = need_key("DATA_GO_KR_KEY")
    key = urllib.parse.unquote(k) if "%" in k else k
    have: dict[str, str] = {}
    for r in read_obs(NAME):
        g = r["구분"]
        have[g] = max(have.get(g, ""), r["기준일"])
    end = now_kst().strftime("%Y%m%d")
    rows, errs, last = [], [], ""
    for c in cfg["companies"]:
        g = c["name"]
        if g in have:
            a = (date.fromisoformat(have[g]) - timedelta(days=7)).strftime("%Y%m%d")
        else:
            a = cfg["backfill_start"] + "01"
        try:
            items = fetch(key, c["code"], a, end)
        except MissingKey:
            raise
        except CollectError as e:
            errs.append(f"{g}: {e}")
            continue
        for x in items:
            d = f"{x['basDt'][:4]}-{x['basDt'][4:6]}-{x['basDt'][6:]}"
            last = max(last, d)
            for ind, fld, unit in (("종가", "clpr", "원"), ("시가총액", "mrktTotAmt", "원"), ("상장주식수", "lstgStCnt", "주")):
                if x.get(fld) not in (None, ""):
                    rows.append({"기준일": d, "축": "밸류에이션", "지표": ind, "구분": g, "값": float(x[fld]),
                                 "단위": unit, "출처": SOURCE})
    if not rows:
        raise CollectError("; ".join(errs[:2]) or "받은 값 없음")
    added = append_obs(NAME, rows)
    return added, f"~{last}" + (f", 실패 {len(errs)}: {errs[0]}" if errs else "")


if __name__ == "__main__":
    from lib.core import load_env
    load_env()
    print(run())
