"""뉴스 (일) — Google News RSS, 키워드 그룹은 config/tracker.yml의 news.groups

GET https://news.google.com/rss/search?q={키워드}+when:7d&hl=ko&gl=KR&ceid=KR:ko   (영문 키워드는 hl=en-US&gl=US)
검색어당 최근 기사 최대 100건만 주므로 과거 백필은 불가 — 수집 시작일부터 쌓인다.

저장: data/news_items.csv (링크 기준 1행, 과거 행은 지우지 않음). 영문 제목은 한국어 번역을 병기:
  1순위 Google 웹 번역(translate.googleapis.com gtx, 비공식·무료) → 실패 시 MyMemory(공식 무료 API, 일 약 5만 자)
  (deep-translator의 Google 경로는 2026-09 기준 429로 막혀 직접 호출). MyMemory로 번역된 제목은 다음 실행에서 Google로 교체 시도.
번역 앞뒤로 기존 트래커의 규칙(scripts/lib/title_ko.py: 관용구 전처리, 회사명·K뷰티 표기·제목체 후처리)을 적용.
AI 요약은 하지 않는다. 입점 기사 건수(플라이휠 '오프라인 진출')는 build에서 이 파일로 센다.
"""
from __future__ import annotations

import csv
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import title_ko  # noqa: E402  (기존 트래커의 번역 전·후처리 규칙)
from lib.core import DATA, KST, CollectError, config, http_get, today  # noqa: E402

NAME = "news"
ITEMS = DATA / "news_items.csv"
FIELDS = ["날짜", "그룹", "키워드", "제목", "제목_ko", "제목_ko_원문", "번역", "매체", "링크", "수집일"]
HANGUL = re.compile(r"[가-힣]")
TRANSLATE_LIMIT = 80  # 한 번 실행에 번역할 최대 제목 수


def rss(q: str) -> list[dict]:
    ko = bool(HANGUL.search(q))
    loc = "hl=ko&gl=KR&ceid=KR:ko" if ko else "hl=en-US&gl=US&ceid=US:en"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q + ' when:7d')}&{loc}"
    r = http_get(url, timeout=30)
    if r.status_code != 200:
        raise CollectError(f"HTTP {r.status_code}")
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(f" - {src}"):
            title = title[: -len(src) - 3].strip()
        try:
            d = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            d = today()
        out.append({"날짜": d, "제목": title, "매체": src, "링크": (it.findtext("link") or "").strip()})
    return out


def google(text: str) -> str:
    """Google 웹 번역(비공식·무료). 문장 조각을 이어 붙인다."""
    r = http_get("https://translate.googleapis.com/translate_a/single",
                 params={"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": text}, tries=1, timeout=20)
    if r.status_code != 200:
        raise CollectError(f"HTTP {r.status_code}")
    return "".join(seg[0] for seg in r.json()[0] if seg and seg[0]).strip()


def mymemory(text: str) -> str:
    r = http_get("https://api.mymemory.translated.net/get", params={"q": text, "langpair": "en|ko"}, tries=1, timeout=20)
    t = (r.json().get("responseData") or {}).get("translatedText") or ""
    if r.status_code != 200 or "MYMEMORY WARNING" in t.upper():
        raise CollectError("MyMemory 한도/오류")
    return t.strip()


def translate(texts: list[str]) -> list[tuple[str, str]]:
    out, google_ok = [], True
    for t in texts:
        res, how = "", "실패"
        if google_ok:
            try:
                res, how = google(t), "Google"
            except Exception:
                google_ok = False  # 막히면 이번 실행에선 더 두드리지 않는다
        if not HANGUL.search(res or ""):
            try:
                res, how = mymemory(t), "MyMemory"
            except Exception:
                res, how = "", "실패"
        out.append((res if HANGUL.search(res or "") else "", how if HANGUL.search(res or "") else "실패"))
        time.sleep(1.2)  # Google 웹 번역은 연속 요청이 많으면 429
    return out


def load() -> dict[str, dict]:
    if not ITEMS.exists():
        return {}
    with ITEMS.open(encoding="utf-8-sig", newline="") as fh:
        return {r["링크"]: r for r in csv.DictReader(fh)}


def run() -> tuple[int, str]:
    cfg = config()
    items = load()
    before = len(items)
    errs = []
    seen_title = {re.sub(r"\W+", "", r["제목"].lower()): k for k, r in items.items()}
    for group, kws in cfg["news"]["groups"].items():
        for kw in kws:
            try:
                got = rss(kw)
            except (CollectError, ET.ParseError) as e:
                errs.append(f"{kw}: {e}")
                continue
            for x in got:
                norm = re.sub(r"\W+", "", x["제목"].lower())
                key = x["링크"] if x["링크"] in items else seen_title.get(norm, x["링크"])
                if key in items:
                    r = items[key]
                    gs = set(filter(None, r["그룹"].split(";"))) | {group}
                    ks = set(filter(None, r["키워드"].split(";"))) | {kw}
                    r["그룹"], r["키워드"] = ";".join(sorted(gs)), ";".join(sorted(ks))
                else:
                    items[key] = {**x, "그룹": group, "키워드": kw, "제목_ko": "", "번역": "", "수집일": today()}
                    seen_title[norm] = key
            time.sleep(1)
    if len(items) == before and errs:
        raise CollectError("; ".join(errs[:3]))
    # 번역 없는 영문 제목 먼저, 남는 몫으로 MyMemory 번역을 Google 번역으로 교체
    en = [r for r in items.values() if not HANGUL.search(r["제목"])]
    todo = sorted([r for r in en if not r["제목_ko"]], key=lambda r: r["날짜"], reverse=True)
    todo += sorted([r for r in en if r["번역"] == "MyMemory"], key=lambda r: r["날짜"], reverse=True)
    todo = todo[:TRANSLATE_LIMIT]
    for r, (ko, how) in zip(todo, translate([title_ko.pre(r["제목"]) for r in todo])):
        if ko and not (how == "MyMemory" and r["번역"] == "MyMemory"):
            r["제목_ko_원문"], r["번역"] = ko, how
        elif not r["제목_ko"]:
            r["번역"] = "실패"
    # 후처리 규칙은 번역기 원문에 매번 다시 적용 (규칙만 바꾸면 API 호출 없이 반영)
    for r in items.values():
        if r.get("제목_ko_원문"):
            r["제목_ko"] = title_ko.post(r["제목_ko_원문"])
    with ITEMS.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(({k: r.get(k, "") for k in FIELDS} for r in items.values()), key=lambda r: r["날짜"], reverse=True))
    how = {}
    for r in todo:
        how[r["번역"]] = how.get(r["번역"], 0) + 1
    return len(items) - before, f"누적 {len(items)}건, 번역 {how or 0}" + (f", 실패 {len(errs)}: {errs[0]}" if errs else "")


if __name__ == "__main__":
    print(run())
