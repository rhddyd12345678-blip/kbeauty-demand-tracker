"""기존 kbeauty-demand-tracker 데이터 → 신규 긴 형식으로 1회 이관.

기존 저장소는 읽기만 한다(git show <ref>:<path>, 작업 폴더·원격은 건드리지 않음). 여러 번 돌려도 중복 없이 합쳐진다.
  1. 아마존: 신규에 없는 스냅샷(2022-11-11 Wayback 50행) → data/obs/amazon.csv, data/amazon_titles.csv
  2. 뉴스: 기사 2,841건 → data/news_items.csv (링크 기준 중복 제거, 추출 요약은 옮기지 않음)
  3. Comtrade: 미국·일본 수입 국가별(3304, 미국 3002.49) → data/obs/comtrade.csv (한국 수출 상대국은 관세청과 중복이라 제외)
  4. 지수: KOSPI·KOSDAQ 일별(네이버 차트) → data/obs/index.csv
  5. 환율: ECB(frankfurter) 원/달러·위안·100엔 → data/obs/fx.csv (ECOS가 있는 날짜는 화면에서 ECOS 우선)
  6. 컨센서스: WiseReport 연간 추정 EPS 현재값 → manual/consensus_eps.csv (12개월 선행 = FY1·FY2 월수 가중, 1회)
  7. 컨센서스 이력: 기존 저장소 git 이력의 companies.json 스냅샷(날짜별 마지막 커밋) → data/obs/consensus_hist.csv
     (연간 추정 EPS·목표주가, 2026-09-23~). 이후 갱신은 없고 manual/consensus_eps.csv 월 1회 수동 입력으로 이어진다.
관세청 품목별 합계(trade.json)·네이버·주가·재무는 신규가 같은 값을 공식 소스로 더 길게 갖고 있어 옮기지 않는다(비교표 참고).

사용: python scripts/migrate_legacy.py [기존 저장소 경로=~/kbeauty/kbeauty-demand-tracker] [ref=origin/main]
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect.amazon import FIELDS as TFIELDS, TITLES, load_titles  # noqa: E402
from collect.comtrade import PARTNERS, SOURCE as CT_SOURCE  # noqa: E402
from collect.news import FIELDS as NFIELDS, ITEMS, load as load_news  # noqa: E402
from lib.core import MANUAL, append_obs, now_kst, read_obs, set_status  # noqa: E402

OLD = Path(sys.argv[1] if len(sys.argv) > 1 else "~/kbeauty/kbeauty-demand-tracker").expanduser()
REF = sys.argv[2] if len(sys.argv) > 2 else "origin/main"
TAG = "기존 트래커 이관"


def old_json(path: str):
    out = subprocess.run(["git", "-C", str(OLD), "show", f"{REF}:{path}"], capture_output=True, check=True).stdout
    return json.loads(out.decode("utf-8"))


def amazon() -> str:
    rows = old_json("data/amazon_top50.json")
    have = {r["기준일"] for r in read_obs("amazon")}
    new = [r for r in rows if r["date"] not in have]
    titles = load_titles()
    obs = []
    for r in new:
        obs.append({"기준일": r["date"], "축": "플라이휠", "지표": "Top50 순위", "구분": f"스킨케어|{r['asin']}", "값": int(r["rank"]),
                    "단위": "위", "출처": f"Wayback 스냅샷({TAG}, {r.get('source', '')})"})
        t = titles.setdefault(r["asin"], {"asin": r["asin"], "title": "", "brand": "", "byline": "", "first_seen": r["date"]})
        t["title"] = t.get("title") or r.get("title", "")
        t["brand"] = t.get("brand") or r.get("brand", "")
        t["first_seen"] = min(t.get("first_seen") or r["date"], r["date"])
    with TITLES.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TFIELDS)
        w.writeheader()
        w.writerows(sorted(({k: v.get(k, "") for k in TFIELDS} for v in titles.values()), key=lambda x: x["asin"]))
    return f"아마존 스냅샷 {len({r['date'] for r in new})}개, +{append_obs('amazon', obs)}행"


def news() -> str:
    old = old_json("data/news.json")
    items = load_news()
    for r in items.values():  # 기존 번역에도 후처리 규칙이 다시 적용되게 원문 칸 채우기
        if r.get("제목_ko") and not r.get("제목_ko_원문"):
            r["제목_ko_원문"] = r["제목_ko"]
    added = 0
    for x in old:
        link = x.get("link") or x.get("url")
        if not link or link in items:
            continue
        ko = x.get("title_ko_raw") or x.get("title_ko") or ""
        items[link] = {"날짜": (x.get("published") or "")[:10], "그룹": ";".join(x.get("tags") or []), "키워드": x.get("query", ""),
                       "제목": x.get("title", ""), "제목_ko": x.get("title_ko", ""), "제목_ko_원문": ko,
                       "번역": x.get("title_ko_src", "") if ko else "", "매체": x.get("source", ""), "링크": link, "수집일": TAG}
        added += 1
    with ITEMS.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=NFIELDS)
        w.writeheader()
        w.writerows(sorted(({k: r.get(k, "") for k in NFIELDS} for r in items.values()), key=lambda r: r["날짜"], reverse=True))
    return f"뉴스 +{added}건(누적 {len(items)}건, 추출 요약 제외)"


def comtrade() -> str:
    cm = old_json("data/comtrade.json")
    names = {"842|M": "미국", "392|M": "일본"}
    rows = []
    for key, rep_name in names.items():
        for p, by_cmd in cm.get(key, {}).items():
            for cmd, parts in (by_cmd or {}).items():
                for pc, v in parts.items():
                    if v is None:
                        continue
                    rows.append({"기준일": f"{p[:4]}-{p[4:]}", "축": "수입시장", "지표": "수입액",
                                 "구분": f"{rep_name}|{cmd}|{pc}|{PARTNERS.get(pc, pc)}", "값": float(v), "단위": "USD",
                                 "출처": CT_SOURCE})
    return f"Comtrade +{append_obs('comtrade', rows)}행 (한국 수출 상대국 시리즈는 제외)"


def index_and_consensus() -> str:
    co = old_json("data/companies.json")
    rows = []
    for label, pts in co.get("indices", {}).items():
        for d, v in pts:
            rows.append({"기준일": d, "축": "밸류에이션", "지표": "지수 종가", "구분": label, "값": float(v), "단위": "pt",
                         "출처": f"네이버 차트({TAG})"})
    n_idx = append_obs("index", rows)
    # 컨센서스 현재값 → 수동 CSV (이미 같은 기업·기준월이 있으면 건드리지 않음)
    f = MANUAL / "consensus_eps.csv"
    with f.open(encoding="utf-8-sig", newline="") as fh:
        cur = list(csv.DictReader(fh))
    have = {(r["기업"], r["기준월"]) for r in cur}
    now = now_kst()
    ym = f"{now.year}-{now.month:02d}"
    w1 = (12 - now.month) / 12  # 남은 올해 몫
    added = []
    for c in co.get("companies", []):
        ann = c.get("annual", {})
        fy1, fy2 = ann.get(f"{now.year}-12", {}), ann.get(f"{now.year + 1}-12", {})
        e1, e2 = fy1.get("eps"), fy2.get("eps")
        if e1 is None or e2 is None or (c["name"], ym) in have:
            continue
        fwd = w1 * e1 + (1 - w1) * e2
        added.append({"기업": c["name"], "기준월": ym, "선행12개월EPS": round(fwd, 1),
                      "출처": f"WiseReport 컨센서스({TAG}, {co.get('updated', '')[:10]})",
                      "메모": f"FY{now.year}E {e1:,.0f} × {w1:.2f} + FY{now.year + 1}E {e2:,.0f} × {1 - w1:.2f}"})
    if added:
        with f.open("a", encoding="utf-8", newline="") as fh:
            csv.DictWriter(fh, fieldnames=["기업", "기준월", "선행12개월EPS", "출처", "메모"]).writerows(added)
    return f"지수 +{n_idx}행, 컨센서스 {len(added)}개사({ym})"


def fx() -> str:
    fx = old_json("data/fx.json")
    names = {"usdkrw": "원/달러", "cnykrw": "원/위안", "jpykrw": "원/100엔"}
    rows = []
    for k, nm in names.items():
        for d, v in zip(fx["dates"], fx[k]):
            if v is not None:
                rows.append({"기준일": d, "축": "환율", "지표": "환율", "구분": nm, "값": float(v), "단위": "원",
                             "출처": f"ECB 기준환율(frankfurter, {TAG})"})
    return f"환율 +{append_obs('fx', rows)}행"


def consensus_history() -> str:
    log = subprocess.run(["git", "-C", str(OLD), "log", "--format=%H", REF, "--", "data/companies.json"],
                         capture_output=True, text=True, check=True).stdout.split()
    by_day: dict[str, dict] = {}
    for h in log:  # 최신 커밋부터 → 날짜별 첫 번째(=그날 마지막) 스냅샷만
        out = subprocess.run(["git", "-C", str(OLD), "show", f"{h}:data/companies.json"], capture_output=True)
        if out.returncode:
            continue
        d = json.loads(out.stdout.decode("utf-8"))
        day = (d.get("updated") or "")[:10]
        if day and day not in by_day:
            by_day[day] = d
    rows = []
    for day, d in by_day.items():
        for c in d.get("companies", []):
            for fy, v in (c.get("annual") or {}).items():
                if v.get("e") and v.get("eps") is not None:
                    rows.append({"기준일": day, "축": "밸류에이션", "지표": "컨센서스 EPS", "구분": f"{c['name']}|FY{fy[:4]}E",
                                 "값": float(v["eps"]), "단위": "원", "출처": f"WiseReport 컨센서스({TAG})"})
            tp = (c.get("info") or {}).get("target_price")
            if tp:
                rows.append({"기준일": day, "축": "밸류에이션", "지표": "컨센서스 목표주가", "구분": c["name"], "값": float(tp),
                             "단위": "원", "출처": f"WiseReport 컨센서스({TAG})"})
    return f"컨센서스 이력 {len(by_day)}일({min(by_day)}~{max(by_day)}), +{append_obs('consensus_hist', rows)}행"


if __name__ == "__main__":
    notes = [amazon(), news(), comtrade(), index_and_consensus(), fx(), consensus_history()]
    set_status("migrate_legacy", True, " / ".join(notes))
    print("\n".join(notes))
