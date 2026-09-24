"""Google News RSS로 화장품 전방 수요 관련 뉴스 수집 → data/news.json

- API 키 불필요. 키워드는 아래 KEYWORDS 에서 관리 (주제 태그별).
- 기존 기사와 링크 기준으로 병합, 최근 MAX_DAYS 일만 보관.
- 영문 제목(한글 비율 10% 미만)은 deep-translator로 한국어 번역 → title_ko (GoogleTranslator 우선,
  구글이 요청 제한/차단이면 MyMemoryTranslator로 대체. title_ko_src 에 사용 엔진 기록).
  직역투를 줄이는 전·후처리 규칙은 scripts/title_ko.py (규칙 버전이 오르면 기존 번역도 재번역).
  이미 title_ko 가 있으면 건너뛰고, 1회 최대 TRANSLATE_LIMIT 건. 실패해도 경고만 찍고 수집은 계속.
사용: python scripts/fetch_news.py            (수집 + 번역)
      python scripts/fetch_news.py --translate-only   (수집 없이 기존 기사 번역만)
"""
from __future__ import annotations

import argparse
import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from common import load_json, save_json, update_meta, KST
import title_ko as TK

# 주제 태그 → 검색어 목록 (구글 뉴스 검색 문법 사용 가능)
KEYWORDS: dict[str, list[str]] = {
    "수출·전방수요": ["화장품 수출", "K뷰티 수출", "화장품 수출액 관세청", "K-beauty exports"],
    "ODM": ["코스맥스", "한국콜마", "코스메카코리아", "화장품 ODM 증설"],
    "메디컬 에스테틱": ["스킨부스터", "리쥬란", "파마리서치", "휴젤", "메디톡스", "클래시스", "보툴리눔 톡신 수출", "PDRN 화장품"],
    "브랜드·채널": ["아마존 K뷰티", "올리브영 매출", "에이피알", "달바글로벌", "K뷰티 브랜드 미국"],
    "규제·정책": ["화장품 관세", "MoCRA 화장품", "중국 화장품 규제", "화장품법 개정"],
    "해외": ["K-beauty Amazon", "Korean skincare US market", "K-beauty Japan", "K-beauty Europe"],
}

MAX_DAYS = 120
TRANSLATE_LIMIT = 100    # 1회 번역 최대 건수
TRANSLATE_SLEEP = 0.4    # 요청 간 대기(초)
TRANSLATE_MAX_TRIES = 3  # 같은 기사 번역 재시도 한도 (실행을 넘어 누적)
STOP_AFTER_FAILS = 5     # 연속 실패 시 이번 실행의 번역 중단 (차단·요청 제한 상황)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def rss_url(q: str) -> str:
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=ko&gl=KR&ceid=KR:ko"


def fetch(q: str) -> list[dict]:
    r = requests.get(rss_url(q), headers=UA, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title") or "")
        # 구글 뉴스 제목은 " - 매체명" 접미사가 붙음
        src_el = it.find("source")
        source = (src_el.text if src_el is not None else "") or ""
        title = re.sub(r"\s+-\s+" + re.escape(source) + r"$", "", title) if source else title
        pub = it.findtext("pubDate")
        try:
            dt = parsedate_to_datetime(pub).astimezone(KST)
        except Exception:
            dt = datetime.now(KST)
        out.append({
            "title": title.strip(),
            "link": it.findtext("link") or "",
            "source": source,
            "published": dt.strftime("%Y-%m-%d %H:%M"),
            "query": q,
        })
    return out


def is_english(title: str) -> bool:
    """한글이 거의 없고 영문자가 충분한 제목. (중국어·일본어 제목은 제외)"""
    hangul = len(re.findall(r"[가-힣]", title))
    cjk = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]", title))
    latin = len(re.findall(r"[A-Za-z]", title))
    return latin >= 8 and latin / (latin + hangul + cjk) >= 0.9


def translate_titles(rows: list[dict], limit: int = TRANSLATE_LIMIT) -> tuple[int, int]:
    """영문 제목 → title_ko. 구글 우선, 막히면 MyMemory. 전·후처리는 title_ko.py.
    대상 순서: ① 번역 없는 기사 ② 번역 규칙 버전이 낮은 기사(재번역) ③ MyMemory 번역을 구글로 교체(구글이 될 때만).
    (성공, 실패) 반환. 어떤 오류도 밖으로 던지지 않음."""
    # 정리: 영문이 아닌데 번역이 붙은 기사(중국어 제목 등)는 번역 제거
    for a in rows:
        if a.get("title_ko") and not is_english(a.get("title", "")):
            for k in ("title_ko", "title_ko_raw", "title_ko_src", "title_ko_ver", "title_ko_tries"):
                a.pop(k, None)
    en = [a for a in rows if is_english(a.get("title", "")) and a.get("title_ko_tries", 0) < TRANSLATE_MAX_TRIES]
    new = [(a, "all") for a in en if not a.get("title_ko")]
    stale = [(a, "all") for a in en if a.get("title_ko") and a.get("title_ko_ver", 1) < TK.VERSION]
    upgrade = [(a, "google") for a in en if a.get("title_ko") and a.get("title_ko_ver", 1) >= TK.VERSION
               and a.get("title_ko_src") != "google"]
    todo = (new + stale + upgrade)[:limit]
    if not todo:
        return 0, 0
    # 후처리 규칙만 바뀐 경우: 저장된 번역기 원본(title_ko_raw)에 다시 적용 (번역 API 호출 없음)
    for a in rows:
        if a.get("title_ko_raw"):
            a["title_ko"] = TK.post(a["title_ko_raw"])
    for a, _ in stale:  # 재번역이 실패해도 새 후처리 규칙은 적용해 둠
        a["title_ko"] = TK.post(a.get("title_ko_raw") or a["title_ko"])
    try:
        from deep_translator import GoogleTranslator, MyMemoryTranslator
        engines = [("google", GoogleTranslator(source="en", target="ko")),
                   ("mymemory", MyMemoryTranslator(source="en-US", target="ko-KR"))]
    except Exception as e:  # 패키지 없음 등
        print(f"[warn] 번역기 초기화 실패, 번역 건너뜀: {e}")
        return 0, len(new)
    disabled: set[str] = set()   # 이번 실행에서 차단된 엔진 (계속 두드리지 않음)
    ok = fail = streak = 0
    used = {"google": 0, "mymemory": 0}
    for a, mode in todo:
        cands = [(n, t) for n, t in engines if n not in disabled and (mode == "all" or n == mode)]
        if not cands:
            continue  # 구글 교체 대상인데 구글이 막힌 경우 등: 실패로 세지 않음
        src = TK.pre(a["title"])
        last_err = None
        for name, tr in cands:
            try:
                raw = (tr.translate(src) or "").strip()
                # MyMemory는 한도 초과 시 오류 대신 경고문을 결과로 돌려줌
                if not raw or "MYMEMORY WARNING" in raw.upper() or not re.search(r"[가-힣]", raw):
                    raise ValueError(f"번역 결과 이상: {raw[:40]}")
                a["title_ko"], a["title_ko_raw"] = TK.post(raw), raw
                a["title_ko_src"], a["title_ko_ver"] = name, TK.VERSION
                used[name] += 1
                last_err = None
                break
            except Exception as e:
                last_err = e
                if type(e).__name__ == "TooManyRequests" or "MYMEMORY WARNING" in str(e).upper():
                    disabled.add(name)
                    print(f"[warn] {name} 요청 제한/차단 → 이번 실행에서 {name} 사용 중단")
        if last_err is None:
            a.pop("title_ko_tries", None)
            ok += 1
            streak = 0
        else:
            e = last_err
            # 요청 제한·차단(429)은 기사 문제가 아니므로 재시도 횟수를 깎지 않음
            if type(e).__name__ not in ("TooManyRequests", "RequestError", "ConnectionError", "Timeout"):
                a["title_ko_tries"] = a.get("title_ko_tries", 0) + 1
            if mode == "all":
                fail += 1
                streak += 1
                print(f"[warn] 번역 실패 ({type(e).__name__}): {a['title'][:60]}")
            if streak >= STOP_AFTER_FAILS or len(disabled) == len(engines):
                print(f"[warn] 번역 중단 (연속 실패 {streak}건, 차단 엔진 {sorted(disabled)}) → 나머지는 다음 실행에서")
                break
        time.sleep(TRANSLATE_SLEEP)
    print(f"번역 대상: 신규 {len(new)}, 재번역 {len(stale)}, 구글 교체 후보 {len(upgrade)} / 엔진별 성공: {used}")
    return ok, fail


def run_translate(rows: list[dict]) -> str:
    try:
        ok, fail = translate_titles(rows)
    except Exception as e:  # 방어: 번역 때문에 수집이 실패하면 안 됨
        print(f"[warn] 번역 단계 오류: {e}")
        ok, fail = 0, 0
    have = sum(1 for a in rows if a.get("title_ko"))
    en = sum(1 for a in rows if is_english(a.get("title", "")))
    print(f"translate: +{ok}, 실패 {fail}, 번역 보유 {have}/{en} (영문 제목)")
    return f"번역 +{ok}, 실패 {fail}, 영문 {have}/{en}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--translate-only", action="store_true", help="수집 없이 기존 기사 제목 번역만")
    args = ap.parse_args()
    if args.translate_only:
        rows = load_json("news.json", [])
        note = run_translate(rows)
        save_json("news.json", rows)
        update_meta("news_translate", "ok", note)
        return
    existing = {a["link"]: a for a in load_json("news.json", [])}
    added = 0
    for tag, queries in KEYWORDS.items():
        for q in queries:
            try:
                items = fetch(q)
            except Exception as e:  # 한 검색어 실패해도 계속
                print(f"[warn] {q}: {e}")
                continue
            for a in items:
                rec = existing.get(a["link"])
                if rec:
                    rec.setdefault("tags", [])
                    if tag not in rec["tags"]:
                        rec["tags"].append(tag)
                else:
                    a["tags"] = [tag]
                    existing[a["link"]] = a
                    added += 1
            time.sleep(1.0)  # 예의상 간격
    cutoff = (datetime.now(KST) - timedelta(days=MAX_DAYS)).strftime("%Y-%m-%d")
    rows = [a for a in existing.values() if a["published"][:10] >= cutoff]
    rows.sort(key=lambda a: a["published"], reverse=True)
    save_json("news.json", rows)  # 번역 전에 먼저 저장 (번역 중 문제가 생겨도 수집분은 보존)
    note = run_translate(rows)
    save_json("news.json", rows)
    update_meta("news_translate", "ok", note)
    update_meta("news", "ok", f"신규 {added}건, 보관 {len(rows)}건")
    print(f"news: +{added}, total {len(rows)}")


if __name__ == "__main__":
    main()
