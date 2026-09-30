"""뉴스 탭 — data/news_items.csv → 최신순 목록 (AI 요약 없음)."""
from __future__ import annotations

import csv

from lib.core import DATA, load_status

LIMIT = 1500


def build(cfg) -> dict:
    f = DATA / "news_items.csv"
    groups = list(cfg["news"]["groups"])
    if not f.exists():
        st = load_status().get("news", {})
        return {"groups": groups, "items": [], "empty": st.get("메모", "뉴스 수집 전")}
    with f.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda r: (r["날짜"], r["수집일"]), reverse=True)
    items = [{"date": r["날짜"], "title": r["제목"], "title_ko": r["제목_ko"], "source": r["매체"], "link": r["링크"],
              "groups": [g for g in r["그룹"].split(";") if g]} for r in rows[:LIMIT]]
    return {"groups": groups, "items": items, "total": len(rows)}
