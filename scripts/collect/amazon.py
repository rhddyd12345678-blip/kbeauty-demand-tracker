"""아마존 US 베스트셀러 Top50 (주) — 카테고리 페이지 직접 읽기

페이지 HTML의 data-client-recs-list 속성(HTML 이스케이프된 JSON)에 ASIN과 render.zg.rank가 들어 있다.
응답은 gzip이라 requests가 자동으로 풀어준다. 제품명은 이미지 alt 텍스트에서.

차단(캡차·503)이나 구조 변경으로 순위를 30개 미만밖에 못 읽으면 실패로 기록하고 끝낸다 — 우회하지 않는다.
그 경우 README '아마존 수동 저장'대로 로컬 Mac에서 같은 수집기를 돌려 커밋한다(로컬에서는 정상 동작 확인).

저장: 지표 'Top50 순위', 구분 '카테고리|ASIN', 값 = 순위. 제품명은 data/amazon_titles.csv (브랜드 판별은 build에서).
목록 HTML에는 30위까지만 제품명이 들어 있어, 처음 보는 ASIN만 상품 페이지에서 제품명·브랜드 표기를 읽어 캐시한다
(한 번에 최대 DETAIL_LIMIT개, Top50 교체가 적어 2주차부터는 몇 건 수준).
"""
from __future__ import annotations

import csv
import os
import html as htmlmod
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import DATA, CollectError, append_obs, config, http_get, today  # noqa: E402

NAME = "amazon"
TITLES = DATA / "amazon_titles.csv"
SOURCE = "아마존 US 베스트셀러 페이지(직접 수집)"
DETAIL_LIMIT = int(os.environ.get("AMAZON_DETAIL_LIMIT") or 40)


def parse(raw: str) -> list[tuple[int, str, str]]:
    h = htmlmod.unescape(raw)
    ranks = {}
    for asin, rank in re.findall(r'"id":"(B[A-Z0-9]{9}|\d{9}[\dX])".{0,300}?"render\.zg\.rank":"(\d+)"', h):
        ranks.setdefault(int(rank), asin)
    titles: dict[str, str] = {}
    for asin, alt in re.findall(r'/dp/([A-Z0-9]{10})[^>]*>.{0,800}?alt="([^"]{10,400})"', h, re.S):
        titles.setdefault(asin, alt.strip())
    for m in re.finditer(r'data-asin="([A-Z0-9]{10})".{0,1500}?line-clamp[^>]*>([^<]{10,400})<', h, re.S):
        titles.setdefault(m.group(1), m.group(2).strip())
    return [(r, ranks[r], titles.get(ranks[r], "")) for r in sorted(ranks) if r <= 50]


FIELDS = ["asin", "title", "brand", "byline", "origin", "first_seen"]


def load_titles() -> dict[str, dict]:
    if not TITLES.exists():
        return {}
    with TITLES.open(encoding="utf-8-sig", newline="") as fh:
        return {r["asin"]: r for r in csv.DictReader(fh)}


def detail(asin: str) -> tuple[str, str, str]:
    """상품 페이지의 제품명, 브랜드 표기('Visit the X Store' / 'Brand: X'), 원산지 표기('Country of Origin', 있을 때만)."""
    r = http_get(f"https://www.amazon.com/dp/{asin}", headers={"Accept-Language": "en-US,en;q=0.9"}, tries=1)
    if r.status_code != 200:
        return "", "", ""
    h = r.text
    t = re.search(r'id="productTitle"[^>]*>\s*([^<]+?)\s*<', h)
    b = re.search(r'<a[^>]*id="bylineInfo"[^>]*>\s*([^<]+?)\s*</a>', h)
    by = htmlmod.unescape(b.group(1)) if b else ""
    by = re.sub(r"^(Visit the |Brand: )|( Store)$", "", by).strip()
    txt = re.sub(r"<[^>]+>", " ", h)
    o = re.search(r"Country of Origin\s*[:\u200f\u200e]*\s*([A-Za-z][A-Za-z ,.()]{1,40}?)\s{2,}", txt)
    origin = re.sub(r"\s+", " ", o.group(1)).strip() if o else ""
    return (htmlmod.unescape(t.group(1)) if t else ""), by, origin


def save_titles(found: dict[str, str]) -> int:
    have = load_titles()
    for a, t in found.items():
        if a not in have:
            have[a] = {"asin": a, "title": t, "byline": "", "first_seen": today()}
        elif t and not have[a].get("title"):
            have[a]["title"] = t
    todo = [a for a in found if not have[a].get("byline")][:DETAIL_LIMIT]
    for a in todo:
        t, by, origin = detail(a)
        if t and not have[a].get("title"):
            have[a]["title"] = t
        have[a]["byline"] = by or "-"
        have[a]["origin"] = origin or "-"
        time.sleep(2.5)
    with TITLES.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(({k: r.get(k, "") for k in FIELDS} for r in have.values()), key=lambda r: r["asin"]))
    return len(todo)


def run() -> tuple[int, str]:
    cfg = config()
    rows, notes, titles, fails = [], [], {}, []
    for cat, url in cfg["amazon"]["categories"].items():
        r = http_get(url, headers={"Accept-Language": "en-US,en;q=0.9", "Accept": "text/html"}, tries=2)
        body = r.text
        if r.status_code != 200 or "captcha" in body.lower()[:20000] or "Robot Check" in body:
            fails.append(f"{cat}: 차단 추정(HTTP {r.status_code}{', 캡차' if 'captcha' in body.lower() else ''})")
            continue
        items = parse(body)
        if len(items) < 30:
            fails.append(f"{cat}: 순위 {len(items)}개만 읽힘(구조 변경 또는 부분 차단)")
            continue
        for rank, asin, title in items:
            rows.append({"기준일": today(), "축": "플라이휠", "지표": "Top50 순위", "구분": f"{cat}|{asin}",
                         "값": rank, "단위": "위", "출처": SOURCE})
            titles[asin] = title
        notes.append(f"{cat} {len(items)}개")
        time.sleep(4)
    if not rows:
        raise CollectError("; ".join(fails) + " — 우회하지 않음. README '아마존 수동 저장' 절차로 전환")
    n_detail = save_titles(titles)
    added = append_obs(NAME, rows)
    notes.append(f"상품 페이지 {n_detail}건")
    return added, ", ".join(notes) + (f" / 실패: {'; '.join(fails)}" if fails else "")


if __name__ == "__main__":
    print(run())
