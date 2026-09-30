"""환율 (일) — 한국은행 ECOS 731Y001 '주요국 통화의 대원화환율' 매매기준율

GET https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr/{시작}/{끝}/731Y001/D/{YYYYMMDD}/{YYYYMMDD}/{항목코드}
ECOS 키가 없거나 실패하면 frankfurter(ECB 기준환율, 키 불필요)로 폴백 — 출처가 달라지므로 출처 칸에 구분해 저장.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import CollectError, append_obs, config, http_get, now_kst, read_obs  # noqa: E402

NAME = "fx"
ECOS = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/731Y001/D/{a}/{b}/{item}"
FRANK = "https://api.frankfurter.dev/v1/{a}..{b}"
FRANK_CCY = {"원/달러": ("USD", 1), "원/유로": ("EUR", 1), "원/100엔": ("JPY", 100), "원/위안": ("CNY", 1)}


def start_date(cfg, source: str) -> str:
    """이어받을 시작일. 저장분이 시작월보다 늦게 시작하면(시작월을 앞당긴 경우) 시작월부터 다시."""
    have = [r["기준일"] for r in read_obs(NAME) if r["출처"].startswith(source)]
    long_start = cfg.get("long_backfill_start", cfg["backfill_start"]) + "01"
    if have and min(have).replace("-", "") <= long_start:
        return (date.fromisoformat(max(have)) - timedelta(days=10)).strftime("%Y%m%d")
    return long_start


def ecos(key: str, a: str, b: str, items: dict) -> list[dict]:
    rows = []
    for name, item in items.items():
        r = http_get(ECOS.format(key=key, a=a, b=b, item=item))
        d = r.json()
        if "StatisticSearch" not in d:
            msg = d.get("RESULT", {}).get("MESSAGE", str(d)[:100])
            if "데이터가 없습니다" in msg:
                continue
            raise CollectError(f"ECOS: {msg}")
        for x in d["StatisticSearch"]["row"]:
            t = x["TIME"]
            rows.append({"기준일": f"{t[:4]}-{t[4:6]}-{t[6:]}", "축": "환율", "지표": "환율", "구분": name,
                         "값": float(x["DATA_VALUE"]), "단위": "원", "출처": "한국은행 ECOS(매매기준율)"})
    return rows


def frankfurter(a: str, b: str) -> list[dict]:
    fa, fb = f"{a[:4]}-{a[4:6]}-{a[6:]}", f"{b[:4]}-{b[4:6]}-{b[6:]}"
    r = http_get(FRANK.format(a=fa, b=fb), params={"base": "KRW", "symbols": "USD,EUR,JPY,CNY"})
    if r.status_code != 200:
        raise CollectError(f"frankfurter HTTP {r.status_code}")
    rows = []
    for day, rates in r.json().get("rates", {}).items():
        for name, (ccy, mult) in FRANK_CCY.items():
            if rates.get(ccy):
                rows.append({"기준일": day, "축": "환율", "지표": "환율", "구분": name, "값": round(mult / rates[ccy], 4),
                             "단위": "원", "출처": "ECB 기준환율(frankfurter, 폴백)"})
    return rows


def run() -> tuple[int, str]:
    cfg = config()
    b = now_kst().strftime("%Y%m%d")
    key = (os.environ.get("ECOS_KEY") or "").strip()
    note = ""
    try:
        if not key:
            raise CollectError("ECOS_KEY 없음")
        rows = ecos(key, start_date(cfg, "한국은행"), b, cfg["fx"])
    except CollectError as e:
        note = f"ECOS 실패({e}) → ECB 폴백. "
        rows = frankfurter(start_date(cfg, "ECB"), b)
    if not rows:
        raise CollectError(note + "신규 값 없음")
    added = append_obs(NAME, rows)
    return added, note + f"~{max(r['기준일'] for r in rows)}"


if __name__ == "__main__":
    from lib.core import load_env
    load_env()
    print(run())
