"""아마존 US Beauty > Skin Care 베스트셀러 Top50 스냅샷 → data/amazon_top50.json

방식 (기존 ~/kbeauty 분석과 동일): Wayback Machine 경유.
 1) web.archive.org/save 로 오늘자 스냅샷 생성 요청 (실패해도 계속)
 2) CDX API 로 아직 수집 안 한 스냅샷 타임스탬프를 찾아 `id_` 원본 HTML 파싱
 (아마존 직접 크롤링은 봇 차단이 심해 CI 에서 불안정)

브랜드 → 한국생산/한국브랜드 매핑은 raw/brand_master.xlsx 사용. 미분류 브랜드는 -1 로 저장되고
data/amazon_unmapped.json 에 모임 → brand_master 에 행 추가 후 재실행하면 재분류됨.
"""
from __future__ import annotations

import html as htmlmod
import re
import time

import openpyxl
import requests

from common import RAW, load_json, save_json, merge_long, update_meta

URL = "amazon.com/Best-Sellers-Beauty-Personal-Care-Skin-Care-Products/zgbs/beauty/11060451"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
MAX_NEW_SNAPSHOTS = 5  # 한 번 실행에 새로 파싱할 최대 스냅샷 수


def parse(raw: str) -> list[dict]:
    h = htmlmod.unescape(raw)
    pairs = re.findall(r'"id":"(B[A-Z0-9]{9})".{0,200}?"render\.zg\.rank":"(\d+)"', h)
    rank_map = {int(r): a for a, r in pairs}
    titles: dict[str, str] = {}
    for asin, alt in re.findall(r'/dp/(B[A-Z0-9]{9})[^>]*>.{0,600}?alt="([^"]{15,300})"', h, re.S):
        titles.setdefault(asin, alt.strip())
    for alt, asin in re.findall(r'alt="([^"]{15,300})".{0,600}?/dp/(B[A-Z0-9]{9})', h, re.S):
        titles.setdefault(asin, alt.strip())
    return [{"rank": r, "asin": rank_map[r], "title": titles.get(rank_map[r], "")} for r in sorted(rank_map)][:50]


def guess_brand(title: str) -> str:
    t = re.split(r"[,|(\[]|\s[-–—]\s", title)[0].strip()
    return " ".join(t.split()[:3])


def load_brand_master() -> dict[str, tuple[int, int]]:
    p = RAW / "brand_master.xlsx"
    if not p.exists():
        return {}
    ws = openpyxl.load_workbook(p, read_only=True, data_only=True)["brand_master"]
    out = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0 or not row[0]:
            continue
        out[str(row[0]).strip()] = (int(row[1] or 0), int(row[2] or 0))
    return out


def classify(title: str, master: dict) -> tuple[str, int, int]:
    g = guess_brand(title)
    gl, tl = g.lower(), title.lower()
    for name, (mik, kb) in master.items():
        base = name.split(" (")[0].lower()
        if gl == name.lower() or gl.startswith(base) or re.search(r"\b" + re.escape(base) + r"\b", tl):
            return name, mik, kb
    return g, -1, -1


def cdx_timestamps() -> list[str]:
    r = requests.get("https://web.archive.org/cdx/search/cdx",
                     params={"url": URL, "output": "text", "filter": "statuscode:200", "limit": 5000},
                     headers=UA, timeout=60)
    r.raise_for_status()
    return sorted({line.split()[1] for line in r.text.splitlines() if line.strip()})


def main() -> None:
    try:
        requests.get("https://web.archive.org/save/https://www." + URL, headers=UA, timeout=90)
        time.sleep(15)
    except Exception as e:
        print(f"[warn] save 요청 실패: {e}")

    existing = load_json("amazon_top50.json", [])
    have_dates = {r["date"] for r in existing}
    try:
        ts_all = cdx_timestamps()
    except Exception as e:
        update_meta("amazon", "fail", f"CDX 조회 실패: {e}")
        return
    todo = [ts for ts in ts_all if f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" not in have_dates][-MAX_NEW_SNAPSHOTS:]
    print(f"스냅샷 {len(ts_all)}개 중 신규 {len(todo)}개")

    master = load_brand_master()
    unmapped = set(load_json("amazon_unmapped.json", []))
    new_rows, done = [], []
    for ts in todo:
        date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        try:
            raw = requests.get(f"https://web.archive.org/web/{ts}id_/https://www.{URL}", headers=UA, timeout=90).text
        except Exception as e:
            print(f"[warn] {ts}: {e}")
            continue
        items = parse(raw)
        if len(items) < 10:
            print(f"{date}: {len(items)}개 (부실, 제외)")
            continue
        for it in items:
            brand, mik, kb = classify(it["title"], master)
            if mik == -1:
                unmapped.add(brand)
            new_rows.append({"date": date, "rank": it["rank"], "asin": it["asin"], "brand": brand,
                             "made_in_korea": mik, "korean_brand": kb, "title": it["title"][:120],
                             "source": f"wayback:{ts}"})
        done.append(date)
        print(f"{date}: {len(items)}개")
        time.sleep(3)

    if new_rows:
        save_json("amazon_top50.json", merge_long(existing, new_rows, ("date", "rank")))
    save_json("amazon_unmapped.json", sorted(unmapped))
    update_meta("amazon", "ok", f"신규 {len(done)}개 스냅샷 {done}, 미분류 브랜드 {len(unmapped)}")


if __name__ == "__main__":
    main()
