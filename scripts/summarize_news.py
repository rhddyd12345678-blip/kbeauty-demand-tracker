"""뉴스 기사 요약 → data/news.json 각 기사에 `summary`(문장 리스트), `url`(원문 주소) 필드 추가

흐름: 구글 뉴스 링크 → 원문 URL 디코딩 → 본문 추출(trafilatura) → 요약
 - ANTHROPIC_API_KEY 가 있으면 Claude로 3줄 요약 (영문 기사도 한국어로 요약)
 - 없으면 추출 요약: 리드문 + 제목 키워드·숫자 포함 문장 점수로 3문장 선택
이미 요약된 기사는 건너뛰고, 실패한 기사는 MAX_TRIES 회까지만 재시도.
사용: python scripts/summarize_news.py [--limit 300]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import trafilatura
from bs4 import BeautifulSoup

from common import load_json, save_json, update_meta

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
MAX_TRIES = 2
WORKERS = 6
LLM_MODEL = os.environ.get("NEWS_SUMMARY_MODEL", "claude-opus-5")


# ---------- 1) 구글 뉴스 링크 → 원문 URL
def decode_google(link: str) -> str:
    if "news.google.com" not in link:
        return link
    aid = urllib.parse.urlparse(link).path.split("/")[-1]
    r = requests.get(f"https://news.google.com/rss/articles/{aid}", headers=UA, timeout=20)
    r.raise_for_status()
    el = BeautifulSoup(r.text, "lxml").select_one("[data-n-a-sg]")
    if el is None:
        raise ValueError("google 서명 없음")
    req = [["Fbv4je", json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None,
                                                  None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None,
                                                 0, 0, None, 0], aid, int(el["data-n-a-ts"]), el["data-n-a-sg"]])]]
    r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                      headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                      data="f.req=" + urllib.parse.quote(json.dumps([req])), timeout=20)
    r.raise_for_status()
    body = json.loads(r.text.split("\n\n", 1)[1])
    return json.loads(body[0][2])[1]


# ---------- 2) 본문 추출
def fetch_text(url: str) -> tuple[str, str]:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    html = r.text
    text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    s = BeautifulSoup(html, "lxml")
    m = s.select_one('meta[property="og:description"]') or s.select_one('meta[name="description"]')
    desc = (m.get("content") or "").strip() if m else ""
    return text, desc


# ---------- 3a) 추출 요약 (키 없을 때)
SENT_SPLIT = re.compile(r"(?<=\D[\.\?\!])\s+(?=[가-힣A-Z0-9\"'“‘(\[\-–·▲△◇])")  # "9. 23" 같은 날짜는 분리하지 않음
SENT_END = re.compile(r"[\.\?\!\"'”’)다요]$")  # 소제목·캡션(끝맺음 없는 줄) 제외
NOISE = re.compile(r"(기자|무단\s?전재|재배포|저작권|Copyright|ⓒ|©|구독|좋아요|앱 다운|사진=|사진 제공|기사입력|\[.*?\]$|@|^[#◇▲■▶※])")


def split_sentences(text: str) -> list[str]:
    out = []
    for para in text.split("\n"):
        para = para.strip()
        if len(para) < 20:
            continue
        for s in SENT_SPLIT.split(para):
            s = re.sub(r"^[\-–—·•\s]+", "", s).strip()
            if 25 <= len(s) <= 260 and SENT_END.search(s) and not NOISE.search(s):
                out.append(s)
    return out


def tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}|\d[\d,.]*%?", s)}


def extractive(title: str, text: str, desc: str, n: int = 3) -> list[str]:
    sents = split_sentences(text)
    if not sents:
        return [desc] if desc else []
    tt = tokens(title)
    scored = []
    for i, s in enumerate(sents[:40]):
        tk = tokens(s)
        if tk and len(tk & tt) / len(tk) > 0.8:  # 제목 반복 문장 제외
            continue
        score = len(tt & tk) * 2.0 + (3.0 if i == 0 else 1.5 if i < 3 else 0)
        score += 1.0 if re.search(r"\d", s) else 0  # 숫자(실적·수출액 등) 포함 문장 가점
        score -= 0.02 * i
        scored.append((score, i, s))
    pick = sorted(sorted(scored, reverse=True)[:n], key=lambda x: x[1])
    return [s for _, _, s in pick]


# ---------- 3b) Claude 요약 (ANTHROPIC_API_KEY 있을 때)
_client = None
SYSTEM = ("당신은 화장품·K뷰티·메디컬 에스테틱 산업을 담당하는 증권사 애널리스트의 리서치 보조입니다. "
          "기사 본문을 읽고 투자자가 알아야 할 핵심만 한국어 3문장 이내로 요약합니다. "
          "숫자(매출·수출액·증감률·가격·수량)와 회사명은 기사에 있는 그대로 남기고, 기사에 없는 내용은 추가하지 않습니다. "
          "영문 기사도 한국어로 요약합니다. 각 문장을 줄바꿈으로 구분하고, 머리말이나 글머리표 없이 문장만 출력합니다.")


def llm_summary(title: str, text: str) -> list[str]:
    global _client
    import anthropic
    if _client is None:
        _client = anthropic.Anthropic()
    resp = _client.beta.messages.create(
        model=LLM_MODEL,
        max_tokens=1024,
        system=SYSTEM,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        messages=[{"role": "user", "content": f"제목: {title}\n\n본문:\n{text[:6000]}"}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("refusal")
    out = "".join(b.text for b in resp.content if b.type == "text").strip()
    return [re.sub(r"^[\-\*•·\d\.\)\s]+", "", l).strip() for l in out.split("\n") if l.strip()][:4]


# ---------- 파이프라인
def summarize(a: dict, use_llm: bool) -> dict:
    url = a.get("url") or decode_google(a["link"])
    text, desc = fetch_text(url)
    if len(text) < 80 and not desc:
        raise ValueError("본문 추출 실패")
    if use_llm and len(text) >= 200:
        summ, method = llm_summary(a["title"], text), "claude"
    else:
        summ, method = extractive(a["title"], text, desc), "extract"
    if not summ:
        raise ValueError("요약 문장 없음")
    return {"url": url, "summary": summ, "summary_method": method}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300, help="이번 실행에서 요약할 최대 기사 수 (최신순)")
    ap.add_argument("--redo-extract", action="store_true", help="추출 요약된 기사를 다시 요약 (예: API 키 등록 후)")
    args = ap.parse_args()
    use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))
    news = load_json("news.json", [])
    if args.redo_extract:
        for a in news:
            if a.get("summary_method") == "extract":
                a.pop("summary", None)
                a.pop("summary_method", None)
            a.pop("summary_tries", None)
    todo = [a for a in news if not a.get("summary") and a.get("summary_tries", 0) < MAX_TRIES][:args.limit]
    print(f"요약 대상 {len(todo)}건 (방식: {'claude ' + LLM_MODEL if use_llm else '추출 요약'})")
    ok = fail = 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(summarize, a, use_llm): a for a in todo}
        for f in as_completed(futs):
            a = futs[f]
            try:
                a.update(f.result())
                a.pop("summary_tries", None)
                ok += 1
            except Exception as e:
                a["summary_tries"] = a.get("summary_tries", 0) + 1
                fail += 1
                print(f"[warn] {a['title'][:40]}: {type(e).__name__} {str(e)[:80]}")
    save_json("news.json", news)
    have = sum(1 for a in news if a.get("summary"))
    update_meta("news_summary", "ok" if ok or not todo else "error",
                f"{'Claude' if use_llm else '추출'} 요약 +{ok}, 실패 {fail}, 누적 {have}/{len(news)}")
    print(f"summary: +{ok}, fail {fail}, total {have}/{len(news)}")


if __name__ == "__main__":
    main()
