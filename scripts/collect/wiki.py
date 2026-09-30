"""위키백과 일별 조회수 (주 1회 수집) — 해외 인지도 [대용 지표]

GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{문서}/daily/{시작}/{끝}
키 불필요. Wikimedia 정책상 연락처가 있는 User-Agent를 붙인다.
Google Trends는 공식 무료 API가 없고 비공식 경로는 클라우드 IP에서 자주 막혀서, 자동 수집은 이것으로 대신하고
Trends는 manual/google_trends.csv로 월 1회 수동 입력한다.
"""
from __future__ import annotations

import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import CollectError, append_obs, config, http_get, now_kst, read_obs  # noqa: E402

NAME = "wiki"
API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{a}/daily/{s}/{e}"
UA = {"User-Agent": "kbeauty-rerating-tracker/1.0 (student research; github.com/rhddyd12345678-blip)"}


def run() -> tuple[int, str]:
    cfg = config()
    have: dict[str, str] = {}
    for r in read_obs(NAME):
        have[r["구분"]] = max(have.get(r["구분"], ""), r["기준일"])
    end = (now_kst().date() - timedelta(days=1)).strftime("%Y%m%d")
    rows, errs = [], []
    for label, article in cfg["wikipedia"]["en"].items():
        s = (date.fromisoformat(have[label]) - timedelta(days=3)).strftime("%Y%m%d") if label in have \
            else cfg["backfill_start"] + "01"
        r = http_get(API.format(a=urllib.parse.quote(article, safe=""), s=s + "00", e=end + "00"), headers=UA)
        if r.status_code == 404:
            errs.append(f"{label}: 문서 없음/조회수 없음")
            continue
        if r.status_code != 200:
            errs.append(f"{label}: HTTP {r.status_code}")
            continue
        for it in r.json().get("items", []):
            t = it["timestamp"]
            rows.append({"기준일": f"{t[:4]}-{t[4:6]}-{t[6:8]}", "축": "플라이휠", "지표": "위키 조회수", "구분": label,
                         "값": it["views"], "단위": "회", "출처": "Wikimedia Pageviews API (영문 위키백과)"})
    if not rows:
        raise CollectError("; ".join(errs) or "받은 값 없음")
    return append_obs(NAME, rows), (f"실패: {'; '.join(errs)}" if errs else f"~{max(r['기준일'] for r in rows)}")


if __name__ == "__main__":
    print(run())
