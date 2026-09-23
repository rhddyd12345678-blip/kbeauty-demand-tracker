"""Google News RSS로 화장품 전방 수요 관련 뉴스 수집 → data/news.json

- API 키 불필요. 키워드는 아래 KEYWORDS 에서 관리 (주제 태그별).
- 기존 기사와 링크 기준으로 병합, 최근 MAX_DAYS 일만 보관.
사용: python scripts/fetch_news.py
"""
from __future__ import annotations

import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from common import load_json, save_json, update_meta, KST

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


def main() -> None:
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
    save_json("news.json", rows)
    update_meta("news", "ok", f"신규 {added}건, 보관 {len(rows)}건")
    print(f"news: +{added}, total {len(rows)}")


if __name__ == "__main__":
    main()
