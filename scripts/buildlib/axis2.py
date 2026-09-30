"""축 2. 플라이휠 — 온라인 → 인지도 → 오프라인 → 수출 → ODM 발주 → 재투자, 그리고 고리 사이의 시차."""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict

import pandas as pd

from lib import brands
from lib.core import DATA

from .axis1 import CUSTOMS_SRC, DART_SRC, REGION_SLOT, REGIONS_8, customs_pivot, fin_table, is_quarters, manual_quarters, region_of
from .common import card, chart, manual, obs, q_date, series, standalone_quarters, ttm

AMZ_SRC = "아마존 US 베스트셀러 Top50 (2022-03~2026-02 Wayback 스냅샷 엑셀 + 2026-09~ 직접 수집)"


# ── 온라인: 아마존 K브랜드 ────────────────────────────────────────
def amazon_table() -> tuple[pd.DataFrame, list[dict]]:
    """행 = (날짜, 카테고리, 순위, asin, 브랜드, 한국생산, 한국브랜드, 분류됨)."""
    df = obs("amazon")
    if df.empty:
        return pd.DataFrame(), []
    titles = {}
    f = DATA / "amazon_titles.csv"
    if f.exists():
        with f.open(encoding="utf-8-sig", newline="") as fh:
            titles = {r["asin"]: r for r in csv.DictReader(fh)}
    master = brands.load()
    by_name = {r["brand"].strip().lower(): r for r in master}
    from lib.core import config as _cfg
    patch = re.compile(_cfg()["amazon"].get("patch_pattern", "patch"), re.I)
    out = []
    for r in df.itertuples():
        cat, asin = r.구분.split("|", 1)
        t = titles.get(asin, {})
        b = by_name.get((t.get("brand") or "").strip().lower()) or brands.match(t.get("byline", ""), master) \
            or brands.match(t.get("title", ""), master)
        out.append({"date": r.기준일, "cat": cat, "rank": int(r.값), "asin": asin, "brand": b["brand"] if b else "",
                    "mik": bool(b and b["mik"]), "kb": bool(b and b["kb"]), "mapped": bool(b),
                    "title": t.get("title", ""), "origin": t.get("origin", ""),
                    "patch": bool(patch.search(t.get("title", "") or ""))})
    tab = pd.DataFrame(out)
    un = tab[~tab["mapped"]]
    unmapped = []
    for asin, g in un.groupby("asin"):
        unmapped.append({"asin": asin, "title": g["title"].iloc[0][:90], "n": len(g), "cats": ", ".join(sorted(set(g["cat"]))),
                         "last": g["date"].max()})
    unmapped.sort(key=lambda x: (-x["n"], x["title"]))
    return tab, unmapped


def amazon_share(tab: pd.DataFrame) -> pd.DataFrame:
    """스냅샷별 한국생산·한국브랜드 비중(%)과 미분류 수."""
    if tab.empty:
        return pd.DataFrame()
    tab = tab.assign(fb=tab["mik"] & ~tab["kb"], patch_mik=tab["patch"] & tab["mik"])
    g = tab.groupby(["cat", "date"])
    s = pd.DataFrame({"n": g.size(), "mik": g["mik"].sum(), "kb": g["kb"].sum(), "fb": g["fb"].sum(),
                      "patch": g["patch"].sum(), "patch_mik": g["patch_mik"].sum(),
                      "unmapped": g["mapped"].apply(lambda x: (~x).sum())})
    s["mik_pct"] = s["mik"] / s["n"] * 100          # 한국생산 비중 (Top50 중)
    s["kb_pct"] = s["kb"] / s["n"] * 100            # K브랜드 비중 (Top50 중)
    s["fb_pct"] = s["fb"] / s["n"] * 100            # 한국생산·외국 브랜드 (Top50 중)
    s["fb_in_mik"] = (s["fb"] / s["mik"].where(s["mik"] > 0) * 100)  # 한국생산 중 외국 브랜드 비중
    return s.reset_index()


def churn(tab: pd.DataFrame, cat: str) -> dict:
    sub = tab[(tab["cat"] == cat) & tab["mik"]]
    dates = sorted(tab[tab["cat"] == cat]["date"].unique())
    if len(dates) < 2:
        return {}
    cur, prev = dates[-1], dates[-2]
    a = set(sub[sub["date"] == cur]["brand"])
    b = set(sub[sub["date"] == prev]["brand"])
    return {"cur": cur, "prev": prev, "new": sorted(a - b), "out": sorted(b - a), "stay": sorted(a & b)}


# ── 시차 분석 ─────────────────────────────────────────────────────
def xcorr(x: pd.Series, y: pd.Series, max_lag: int) -> list[tuple[int, float, int]]:
    """x가 k기 선행: corr(x[t-k], y[t]). (k, r, 표본수)"""
    out = []
    for k in range(0, max_lag + 1):
        xs = x.copy()
        xs.index = xs.index + k
        j = pd.concat([xs.rename("x"), y.rename("y")], axis=1).dropna()
        if len(j) >= 6 and j["x"].std() > 0 and j["y"].std() > 0:
            out.append((k, float(j["x"].corr(j["y"])), len(j)))
    return out


def period_index(idx, freq: str) -> pd.Index:
    """날짜 → 정수 기간 번호 (월: y*12+m, 분기: y*4+q)."""
    d = pd.to_datetime(pd.Index(idx))
    return (d.year * 12 + d.month - 1) if freq == "M" else (d.year * 4 + (d.month - 1) // 3)


def lag_row(name: str, xname: str, yname: str, x: pd.Series, y: pd.Series, freq: str, max_lag: int, min_n: int,
            transform: str) -> list:
    unit = "개월" if freq == "M" else "분기"
    if x.empty or y.empty:
        return [name, transform, "데이터 부족", "", "", "수집 전"]
    x = x.copy(); x.index = period_index(x.index, freq)  # noqa: E702
    y = y.copy(); y.index = period_index(y.index, freq)  # noqa: E702
    res = xcorr(x, y, max_lag)
    if not res:
        return [name, transform, "겹치는 기간 부족", "", "", "표본 6개 미만"]
    k, r, n = max(res, key=lambda t: t[1])
    warn = f"참고용 — 표본 {n}개" if n < min_n else f"표본 {n}개"
    detail = " · ".join(f"{kk}{unit[0]}:{rr:+.2f}" for kk, rr, _ in res)
    if r < 0.3:
        verdict = "뚜렷한 선행 관계 없음 (최대 r < 0.3)"
    else:
        verdict = f"{xname}이(가) {k}{unit} 선행" if k else "동행(시차 0)"
    return [name, transform, verdict, round(r, 2), detail, warn]


# ── 본체 ─────────────────────────────────────────────────────────
def build(cfg) -> tuple[dict, dict]:
    kpi: dict = {}
    tab, unmapped = amazon_table()
    sh = amazon_share(tab)
    uv = cfg["amazon"].get("uv_filter_date")
    online = []
    cats = list(cfg["amazon"]["categories"])
    for cat in cats:
        s = sh[sh["cat"] == cat].set_index("date") if not sh.empty else pd.DataFrame()
        marks = [{"x": uv, "label": "UV 필터 승인"}] if cat == "선케어" and uv else []
        un_note = ""
        if not s.empty and s["unmapped"].iloc[-1]:
            un_note = f" 최근 스냅샷 미분류 {int(s['unmapped'].iloc[-1])}개 — 미분류가 K브랜드면 비중이 과소 추정됨(아래 '미분류 브랜드' 표)."
        es = pd.Series(dtype=float)
        online.append(chart(
            f"amz_{cat}", f"아마존 US {cat} Top50 — 한국생산·K브랜드 비중", grade="auto", unit="%", freq="W", yoy="diff",
            series_list=[series("한국생산", s["mik_pct"] if not s.empty else es, slot=0),
                         series("K브랜드", s["kb_pct"] if not s.empty else es, slot=1),
                         series("한국생산·외국 브랜드", s["fb_pct"] if not s.empty else es, slot=2, dash=True)],
            source=AMZ_SRC if cat == "스킨케어" else "아마존 US 베스트셀러 Top50 (직접 수집, 2026-09~)",
            collectors=["amazon"], marks=marks,
            note="한국생산 = 한국에서 만든 제품(브랜드 국적 무관, 예: 미국 브랜드 Mighty Patch), K브랜드 = 한국 브랜드 제품, "
                 "점선 = 한국에서 만든 외국 브랜드 제품(글로벌 브랜드의 K-ODM 사용 신호). 모두 Top50 중 비중. 판별 기준은 README '아마존 지표'." + un_note,
            sample="" if cat == "스킨케어" else "직접 수집이 2026-09부터라 추이는 몇 주 쌓인 뒤부터 의미"))
        if not s.empty:
            kpi[f"amz_{cat}"] = s["mik_pct"].tolist()
    def by_cat(col):
        return [series(c, sh[sh["cat"] == c].set_index("date")[col], slot=i) for i, c in enumerate(cats)
                if not sh.empty and (sh["cat"] == c).any()]
    src_all = "아마존 US 베스트셀러 Top50 (스킨케어는 2022-03~ 과거 스냅샷 포함, 나머지는 2026-09~ 직접 수집)"
    kpi["amz_charts"] = {
        "mik": chart("amz_mik_all", "아마존 Top50 한국생산 비중 — 카테고리별", grade="auto", unit="%", freq="W", yoy="diff",
                     series_list=by_cat("mik_pct"), source=src_all, collectors=["amazon"],
                     note="한국에서 만든 제품의 비중(브랜드 국적 무관). K-ODM이 만든 물량이 미국 온라인에서 얼마나 팔리는지 — ODM 단계 신호."),
        "kb": chart("amz_kb_all", "아마존 Top50 K브랜드 비중 — 카테고리별", grade="auto", unit="%", freq="W", yoy="diff",
                    series_list=by_cat("kb_pct"), source=src_all, collectors=["amazon"],
                    note="한국 브랜드 제품의 비중(생산지 무관). 소비자가 K브랜드를 고르는지 — 채널 단계 신호."),
        "fb": chart("amz_fb", "한국생산 중 외국 브랜드 비중 — 글로벌 브랜드의 K-ODM 사용 신호", grade="auto", unit="%", freq="W", yoy="diff",
                    series_list=by_cat("fb_in_mik"), source=src_all, collectors=["amazon"],
                    note="Top50 안의 한국생산 제품 중 한국 브랜드가 아닌 것의 비중(예: Mighty Patch·Rael). 한국생산 제품이 적은 스냅샷은 "
                         "분모가 작아 크게 출렁인다 — 아래 개수와 함께 볼 것."),
    }
    fs = sh[sh["cat"] == "페이셜 트리트먼트"].set_index("date") if not sh.empty else pd.DataFrame()
    kpi["amz_charts"]["patch"] = chart(
        "amz_patch", "여드름 패치: 아마존 'Facial Treatments & Masks' Top50 내 패치 제품 수", grade="proxy", unit="개", freq="W", yoy="",
        series_list=[series("패치 제품", fs["patch"], slot=0), series("그중 한국생산", fs["patch_mik"], slot=1)] if not fs.empty else [],
        source="아마존 US 베스트셀러 (Facial Treatments & Masks, 직접 수집)", collectors=["amazon"],
        empty_reason="수집 전 — 다음 아마존 수집(주 1회)부터",
        note="여드름 패치 전용 베스트셀러 카테고리가 없어(2026-09 확인) 상위 카테고리에서 제품명(patch·pimple·hydrocolloid 등)으로 골라 센다. "
             "30위 밖 제품명은 상품 페이지에서 채워지므로 첫 주엔 일부 누락될 수 있다.")
    ch = churn(tab, "스킨케어") if not tab.empty else {}
    churn_rows = []
    for cat in cats:
        c = churn(tab, cat) if not tab.empty else {}
        if c:
            churn_rows.append([cat, f"{c['prev']} → {c['cur']}", ", ".join(c["new"]) or "–", ", ".join(c["out"]) or "–",
                               len(c["stay"])])
    online.append({"id": "amz_churn", "title": "한국 생산 브랜드 신규 진입·이탈 (직전 스냅샷 대비)", "grade": "auto",
                   "gradeLabel": "자동 수집", "table": {"columns": ["카테고리", "비교 시점", "신규 진입", "이탈", "유지(개)"],
                                                     "rows": churn_rows},
                   "empty": "" if churn_rows else "스냅샷 2개 이상 필요",
                   "note": "엑셀 과거 스냅샷과 직접 수집 사이에 2026-02~09 공백이 있어 첫 비교는 그 간격 전체의 변화."})
    online.append({"id": "amz_unmapped", "title": f"미분류 브랜드 {len(unmapped)}개 — brand_master에 추가 필요", "grade": "auto",
                   "gradeLabel": "자동 수집", "table": {"columns": ["ASIN", "제품명", "등장 횟수", "카테고리", "최근 등장"],
                                                     "rows": [[u["asin"], u["title"], u["n"], u["cats"], u["last"]] for u in unmapped[:60]]},
                   "empty": "" if unmapped else "미분류 없음",
                   "note": "config/brand_master.csv에 brand, made_in_korea(0/1), is_korean_brand(0/1)를 추가하고 커밋하면 "
                           "다음 빌드에서 과거 스냅샷까지 다시 분류된다. 제목 표기가 다르면 aliases 열에 ';'로 추가."})

    # 인지도
    wk = obs("wiki")
    aware = []
    if not wk.empty:
        p = wk.pivot_table(index="기준일", columns="구분", values="값", aggfunc="sum")
        p.index = pd.to_datetime(p.index)
        w = p.resample("W-MON", label="left", closed="left").sum(min_count=7)
        w.index = w.index.strftime("%Y-%m-%d")
        brands_cols = [c for c in w.columns if c not in ("Medical spa", "Hydrocolloid")]
        aware.append(chart("wiki_kbeauty", "해외 인지도 대용: 영문 위키백과 주간 조회수", grade="proxy", unit="회", freq="W",
                           series_list=[series(c, w[c]) for c in brands_cols], source="Wikimedia Pageviews API",
                           collectors=["wiki"],
                           note="Google Trends 대신 쓰는 대용 지표. 위키 조회수는 '검색해서 찾아본 사람' 수에 가깝다. "
                                "COSRX·Beauty of Joseon·Rejuran은 영문 문서가 없어 제외."))
        m = p.resample("MS").sum()
        kpi["wiki_m"] = m.get("K-beauty", pd.Series(dtype=float))
        kpi["wiki_w"] = w.get("K-beauty", pd.Series(dtype=float)).dropna().tolist()
    else:
        aware.append(chart("wiki_kbeauty", "해외 인지도 대용: 영문 위키백과 주간 조회수", grade="proxy", unit="회", freq="W",
                           series_list=[], source="Wikimedia Pageviews API", collectors=["wiki"]))
    gt = manual("google_trends")
    gt_series = []
    if not gt.empty:
        for kw, g in gt.groupby("키워드"):
            gt_series.append(series(kw, pd.Series({f"{m}-01": pd.to_numeric(v, errors="coerce") for m, v in zip(g["기준월"], g["값"])}).sort_index()))
    aware.append(chart("gtrends", "Google Trends (수동 입력, 월)", grade="manual", unit="지수", freq="M", series_list=gt_series,
                       source="trends.google.com 내보내기 → manual/google_trends.csv",
                       empty_reason="수동 입력 대기 — 공식 무료 API 없음, 비공식 경로는 클라우드에서 자주 차단. "
                                    "월 1회 trends.google.com에서 CSV를 받아 manual/google_trends.csv에 붙여넣기"))

    # 오프라인
    offline = []
    items_f = DATA / "news_items.csv"
    store_kw = cfg["news"]["store_keywords"]
    pat = re.compile("|".join(re.escape(k) for k in store_kw), re.I)
    if items_f.exists():
        with items_f.open(encoding="utf-8-sig", newline="") as fh:
            items = [r for r in csv.DictReader(fh) if "채널·입점" in r["그룹"] and pat.search(r["제목"] + " " + r["제목_ko"])]
        chans = cfg["news"].get("channels", {})
        cpat = {ch: re.compile("|".join(re.escape(k) for k in kws), re.I) for ch, kws in chans.items()}
        cnt: dict[str, Counter] = {ch: Counter() for ch in chans}
        cnt["입점 기사 전체"] = Counter()
        for r in items:
            d = pd.Timestamp(r["날짜"])
            wk = (d - pd.Timedelta(days=d.weekday())).strftime("%Y-%m-%d")
            cnt["입점 기사 전체"][wk] += 1
            for ch, pt in cpat.items():
                if pt.search(r["제목"] + " " + r["제목_ko"]):
                    cnt[ch][wk] += 1
        first = min((r["수집일"] for r in items), default="")
        weeks = sorted(cnt["입점 기사 전체"])
        s_list = [series(ch, pd.Series({w: float(cnt[ch].get(w, 0)) for w in weeks}), slot=i) for i, ch in enumerate(chans)]
        s_list.append(series("입점 기사 전체", pd.Series({w: float(cnt["입점 기사 전체"][w]) for w in weeks}), slot="muted"))
        offline.append(chart("store_news", "채널별 입점 기사 건수 (주간)", grade="proxy", unit="건", freq="W", yoy="",
                             series_list=s_list, source="Google News RSS (채널·입점 그룹)", collectors=["news"],
                             note=f"채널 키워드는 config/tracker.yml의 news.channels. 같은 기사는 한 번만 셈. 입점 자체가 아니라 "
                                  f"'입점 관련 보도량'. 틱톡샵은 플랫폼 매출이 비공개라 이 건수가 유일한 공개 지표.",
                             sample=f"RSS는 과거 백필이 안 돼 {first}부터 누적 — 표본이 짧다" if first else ""))
    else:
        offline.append(chart("store_news", "입점 키워드 기사 건수 (주간)", grade="proxy", unit="건", freq="W", series_list=[],
                             source="Google News RSS", collectors=["news"]))
    ev = manual("store_events")
    offline.append({"id": "store_events", "title": "입점 이벤트 로그 (수동 입력)", "grade": "manual", "gradeLabel": "수동 입력",
                    "table": {"columns": ["날짜", "브랜드", "채널", "국가", "내용", "출처URL"],
                              "rows": ev.sort_values("날짜", ascending=False)[["날짜", "브랜드", "채널", "국가", "내용", "출처URL"]].values.tolist() if not ev.empty else []},
                    "empty": "수동 입력 대기 — manual/store_events.csv (공개 기사·공시로 확인한 입점만 입력 권장)"})

    # 수출
    exp_p, _ = customs_pivot("3304")
    regmap = region_of(cfg)
    exports = []
    if not exp_p.empty:
        # 홍콩은 이 차트에서 따로 그리지 않고 기타에 합친다(8개 지역 + 기타)
        reg = exp_p.T.groupby(lambda c: regmap.get(c) if regmap.get(c) in REGIONS_8 else "기타").sum().T / 1e6
        reg.index = [f"{m}-01" for m in reg.index]
        exports.append(chart("exp_region", "지역별 화장품 수출액 (월)", grade="auto", unit="백만 달러", freq="M",
                             series_list=[series(n, reg[n], slot=REGION_SLOT[n]) for n in REGIONS_8 + ["기타"] if n in reg],
                             source=CUSTOMS_SRC, collectors=["customs"],
                             note="YoY 전환으로 증가율 확인. 관세청 수출은 출하 기준 — 현지 판매와 시차·차이가 있다(데이터 한계 탭)."))
        kpi["exp_total"] = exp_p.sum(axis=1)
        kpi["exp_us"] = exp_p.get("US", pd.Series(dtype=float))
    else:
        exports.append(chart("exp_region", "지역별 화장품 수출액 (월)", grade="auto", unit="백만 달러", freq="M", series_list=[],
                             source=CUSTOMS_SRC, collectors=["customs"]))

    kpi["churn"] = ch
    kpi["share"] = sh
    groups = {"online": online, "aware": aware, "offline": offline, "exports": exports}
    return value_chain(cfg, groups, kpi)


# ── 밸류체인 5단계 ────────────────────────────────────────────────
STAGES = ["원부자재", "ODM", "브랜드사", "유통", "채널"]
STAGE_ID = {"원부자재": "raw", "ODM": "odm", "브랜드사": "brand", "유통": "dist", "채널": "chan"}
AGG_NOTE = "단계 값은 매출 가중(합산): 매출 YoY = 단계 합산 매출 ÷ 1년 전 합산 − 1(두 시점 모두 값이 있는 회사만), " \
           "영업이익률 = 합산 영업이익 ÷ 합산 매출, 회전일수 = 합산 기말 잔액 ÷ 합산 TTM 매출 × 365."


def prev_q(k: str, n: int = 4) -> str:
    y, q = int(k[:4]), int(k[-1])
    q -= n
    while q <= 0:
        y, q = y - 1, q + 4
    return f"{y}Q{q}"


def company_quarters(cfg, ft) -> dict[str, dict]:
    """기업별 분기 지표. 매출·영업이익은 분기 단독, 재고·채권은 분기말 잔액. 연결 우선, 없으면 별도.
    상장폐지 회사는 dart_until 이후 매출·영업이익을 수동 입력으로 잇는다. unlisted는 수동 입력만."""
    out = {}
    for c in [c for c in cfg["companies"] if c.get("stage")] + [u for u in cfg.get("unlisted", [])]:
        name = c["name"]
        rev, fs = is_quarters(ft, name, "매출액")
        op, _ = is_quarters(ft, name, "영업이익")
        grade = "auto"
        until = c.get("dart_until")
        if until or "code" not in c:
            m_rev = manual_quarters(name, "매출액")
            m_op = manual_quarters(name, "영업이익")
            rev = {**{k: v for k, v in rev.items() if not until or k <= until}, **{k: v for k, v in m_rev.items() if not until or k > until}}
            op = {**{k: v for k, v in op.items() if not until or k <= until}, **{k: v for k, v in m_op.items() if not until or k > until}}
            grade = "manual"  # 상장폐지 이후 분기는 수동 입력 몫이므로 입력 전이라도 [수동 입력]
            if "code" not in c:
                grade = c.get("grade", "manual")
                fs = "수동 입력" if rev else ""
        inv = ft.get((name, fs, "재고자산"), {}) if fs in ("연결", "별도") else {}
        ar = ft.get((name, fs, "매출채권"), {}) if fs in ("연결", "별도") else {}
        out[name] = {"stage": c["stage"], "fs": fs, "grade": grade, "rev": rev, "op": op, "inv": inv, "ar": ar,
                     "ttm": ttm(rev), "note": c.get("note", ""), "listed": "code" in c, "delisted": c.get("delisted", "")}
    return out


def stage_series(comps: dict, stage: str) -> dict[str, dict[str, float]]:
    """매출 가중(합산) 단계 지표 → {'yoy':{분기:값}, 'opm':…, 'inv':…, 'ar':…, 'rev':…, 'n':…}"""
    cs = [v for v in comps.values() if v["stage"] == stage]
    keys = sorted({k for v in cs for k in v["rev"]})
    res = {"yoy": {}, "opm": {}, "inv": {}, "ar": {}, "rev": {}, "n": {}}
    for k in keys:
        both = [v for v in cs if k in v["rev"] and prev_q(k) in v["rev"]]
        if both:
            a, b = sum(v["rev"][k] for v in both), sum(v["rev"][prev_q(k)] for v in both)
            if b > 0:
                res["yoy"][k] = (a / b - 1) * 100
                res["n"][k] = len(both)
        w = [v for v in cs if k in v["rev"] and k in v["op"]]
        if w and sum(v["rev"][k] for v in w):
            res["opm"][k] = sum(v["op"][k] for v in w) / sum(v["rev"][k] for v in w) * 100
        res["rev"][k] = sum(v["rev"][k] for v in cs if k in v["rev"])
        for m in ("inv", "ar"):
            w = [v for v in cs if k in v[m] and k in v["ttm"] and v["ttm"][k]]
            if w:
                res[m][k] = sum(v[m][k] for v in w) / sum(v["ttm"][k] for v in w) * 365
    return res


def qser(d: dict) -> pd.Series:
    return pd.Series({q_date(k): v for k, v in sorted(d.items())}, dtype=float)


def dir_of(d: dict, lag: int, tol: float) -> tuple[str, float | None]:
    ks = sorted(d)
    if not ks:
        return "", None
    k = ks[-1]
    p = prev_q(k, lag)
    if p not in d:
        return "판단 불가", None
    diff = d[k] - d[p]
    return ("보합" if abs(diff) <= tol else "상승" if diff > 0 else "하락"), diff


def value_chain(cfg, groups: dict, kpi: dict) -> tuple[dict, dict]:
    ft = fin_table()
    comps = company_quarters(cfg, ft)
    agg = {st: stage_series(comps, st) for st in STAGES}

    # 유통 단계에 붙는 관세청 분기 수출 YoY (완결된 분기만)
    tot = kpi.get("exp_total", pd.Series(dtype=float))
    exp_q, exp_q_yoy = {}, {}
    if len(tot):
        t = tot.copy()
        t.index = pd.PeriodIndex(t.index, freq="M")
        for p_, g in t.groupby(t.index.asfreq("Q")):
            if len(g) == 3:
                exp_q[f"{p_.year}Q{p_.quarter}"] = float(g.sum())
        exp_q_yoy = {k: (v / exp_q[prev_q(k)] - 1) * 100 for k, v in exp_q.items() if prev_q(k) in exp_q and exp_q[prev_q(k)]}
    # 아마존 스킨케어 분기 평균: 한국생산(ODM 단계) / K브랜드(채널 단계)
    sh = kpi.get("share", pd.DataFrame())
    amz_q, amz_q_kb = {}, {}
    if len(sh):
        s = sh[sh["cat"] == "스킨케어"]
        for col, dst in (("mik_pct", amz_q), ("kb_pct", amz_q_kb)):
            acc: dict = {}
            for d, v in zip(s["date"], s[col]):
                ts = pd.Timestamp(d)
                acc.setdefault(f"{ts.year}Q{(ts.month - 1) // 3 + 1}", []).append(v)
            dst.update({k: sum(v) / len(v) for k, v in acc.items()})

    # 채널: 입점 기사 주간 건수(전체)
    store_w = {}
    for it in groups["offline"]:
        for se in it.get("series", []):
            if se["name"] == "입점 기사 전체":
                store_w = {d: v for d, v in se["data"]}

    # ── 1. 단계 그림 데이터
    boxes = []
    for st in STAGES:
        a = agg[st]
        members = [f"{n}{'' if v['grade'] == 'auto' else ' [' + GRADES_SHORT[v['grade']] + ']'}" for n, v in comps.items() if v["stage"] == st]
        last = max(a["yoy"]) if a["yoy"] else ""
        yd, _ = dir_of(a["yoy"], 1, 2.0)
        od, _ = dir_of(a["opm"], 4, 0.5)
        idd, _ = dir_of(a["inv"], 4, 3.0)
        box = {"id": STAGE_ID[st], "name": st, "members": members, "quarter": f"{last[:4]} {last[-1]}Q" if last else "",
               "metrics": [
                   {"label": "매출 YoY", "value": f"{a['yoy'][last]:+.1f}%" if last else "데이터 없음", "dir": yd,
                    "hint": "직전 분기 YoY 대비"},
                   {"label": "영업이익률", "value": f"{a['opm'][max(a['opm'])]:.1f}%" if a["opm"] else "데이터 없음", "dir": od,
                    "hint": "1년 전 같은 분기 대비"},
                   {"label": "재고일수", "value": f"{a['inv'][max(a['inv'])]:.0f}일" if a["inv"] else "데이터 없음", "dir": idd,
                    "hint": "1년 전 같은 분기 대비", "up_bad": True}]}
        if st == "ODM" and amz_q:
            k = max(amz_q)
            box["metrics"].append({"label": f"아마존 한국생산 비중 ({k[:4]} {k[-1]}Q 평균)", "value": f"{amz_q[k]:.0f}%",
                                   "dir": dir_of(amz_q, 4, 1.0)[0], "hint": "스킨케어 Top50 한국에서 만든 제품, 1년 전 같은 분기 대비"})
        if st == "유통" and exp_q_yoy:
            k = max(exp_q_yoy)
            box["metrics"].append({"label": f"화장품 수출 YoY ({k[:4]} {k[-1]}Q)", "value": f"{exp_q_yoy[k]:+.1f}%",
                                   "dir": dir_of(exp_q_yoy, 1, 2.0)[0], "hint": "관세청, 분기 합"})
        if st == "채널":
            box["name"] = "채널 (세포라·올리브영·아마존·틱톡)"
            box["metrics"] = []
            if amz_q_kb:
                k = max(amz_q_kb)
                box["metrics"].append({"label": f"아마존 K브랜드 비중 ({k[:4]} {k[-1]}Q 평균)", "value": f"{amz_q_kb[k]:.0f}%",
                                       "dir": dir_of(amz_q_kb, 4, 1.0)[0], "hint": "스킨케어 Top50 한국 브랜드, 1년 전 같은 분기 대비"})
            if store_w:
                w = sorted(store_w)
                cur, prev = sum(store_w[x] for x in w[-4:]), sum(store_w[x] for x in w[-8:-4]) if len(w) >= 8 else None
                dd = "" if prev is None else ("보합" if cur == prev else "상승" if cur > prev else "하락")
                box["metrics"].append({"label": "입점 기사 (최근 4주)", "value": f"{cur:.0f}건", "dir": dd or "판단 불가",
                                       "hint": "세포라·울타·코스트코·틱톡샵 등, 직전 4주 대비"})
            ol = [m for m in (agg["채널"]["yoy"],) if m]
            box["metrics"].append({"label": "올리브영 매출 YoY", "value": f"{agg['채널']['yoy'][max(agg['채널']['yoy'])]:+.1f}%" if ol else "수동 입력 대기",
                                   "dir": dir_of(agg["채널"]["yoy"], 1, 2.0)[0] if ol else "", "hint": "manual/company_quarterly.csv"})
        boxes.append(box)
    diagram = {"id": "vc_diagram", "chain": boxes, "wide": True, "title": "밸류체인 5단계 — 최근 분기",
               "note": AGG_NOTE + " 방향: 매출 YoY는 직전 분기, 이익률·재고일수는 1년 전 같은 분기와 비교. "
                                  "상자를 누르면 기업별 상세가 펼쳐진다."}

    # ── 2~4. 단계 비교 그래프
    def stage_chart(cid, title, key, unit, note, extra=None, yoy="diff"):
        sl = [series(st, qser(agg[st][key]), slot=i) for i, st in enumerate(STAGES) if agg[st][key]]
        if extra:
            sl.append(extra)
        return chart(cid, title, grade="auto", unit=unit, freq="Q", yoy=yoy, series_list=sl, source=DART_SRC + " (+ 수동 입력)",
                     collectors=["dart"], note=note)
    exp_series = series("관세청 화장품 수출(분기 합)", qser(exp_q_yoy), slot="muted", dash=True) if exp_q_yoy else None
    charts = [
        stage_chart("vc_yoy", "단계별 매출 YoY (분기, 매출 가중)", "yoy", "%", AGG_NOTE + " 점선 = 관세청 화장품 수출 YoY(유통 단계 참고). "
                    "오른쪽(채널·유통) 단계가 먼저 꺾이고 왼쪽(ODM·원부자재)이 뒤따르는지 본다.", exp_series, yoy=""),
        stage_chart("vc_opm", "단계별 영업이익률 (분기, 매출 가중)", "opm", "%", "이익이 어느 단계에 남는지. " + AGG_NOTE),
        stage_chart("vc_inv", "단계별 재고자산 회전일수 (매출 가중)", "inv", "일",
                    "재고가 어느 단계에 쌓이는지. 기말 재고 ÷ TTM 매출 × 365. 수동 입력 회사는 재고가 없어 해당 분기에서 빠진다."),
        stage_chart("vc_ar", "단계별 매출채권 회전일수 (매출 가중)", "ar", "일",
                    "대금 회수 기간. 짧아지면 거래 조건이 유리해졌거나 현금 판매 비중이 커진 것."),
    ]

    # ── 5. 인접 단계 시차 (전부 분기)
    def q_yoy_series(d):
        return qser(d)
    rows = []
    pairs = [
        ("채널 → 유통", "아마존 K브랜드 비중", "유통 매출", qser(amz_q_kb), q_yoy_series(agg["유통"]["yoy"]),
         "아마존 스킨케어 K브랜드 비중(분기 평균, %) 대 유통 단계 매출 YoY"),
        ("수출 → 브랜드사", "화장품 수출", "브랜드사 매출", qser(exp_q_yoy), q_yoy_series(agg["브랜드사"]["yoy"]),
         "관세청 HS3304 수출 YoY(분기 합) 대 브랜드사 단계 매출 YoY"),
        ("유통 → 브랜드사", "유통 매출", "브랜드사 매출", q_yoy_series(agg["유통"]["yoy"]), q_yoy_series(agg["브랜드사"]["yoy"]),
         "유통 단계 매출 YoY 대 브랜드사 단계 매출 YoY"),
        ("브랜드사 → ODM", "브랜드사 매출", "ODM 매출", q_yoy_series(agg["브랜드사"]["yoy"]), q_yoy_series(agg["ODM"]["yoy"]),
         "브랜드사 단계 매출 YoY 대 ODM 단계 매출 YoY"),
        ("ODM → 원부자재", "ODM 매출", "원부자재 매출", q_yoy_series(agg["ODM"]["yoy"]), q_yoy_series(agg["원부자재"]["yoy"]),
         "ODM 단계 매출 YoY 대 원부자재 단계 매출 YoY"),
        ("원부자재 → ODM (역방향 확인)", "원부자재 매출", "ODM 매출", q_yoy_series(agg["원부자재"]["yoy"]), q_yoy_series(agg["ODM"]["yoy"]),
         "부자재를 먼저 준비하는 구조라면 원부자재가 ODM을 앞설 수도 있다"),
    ]
    for name, xn, yn, x, y, tr in pairs:
        rows.append(lag_row(name, xn, yn, x, y, "Q", 3, 12, tr))
    # 기존 플라이휠 고리 (분기로 맞춤)
    wm = kpi.get("wiki_m", pd.Series(dtype=float))
    wiki_q = {}
    if len(wm):
        w = wm.copy()
        w.index = pd.PeriodIndex(w.index, freq="M")
        for p_, g in w.groupby(w.index.asfreq("Q")):
            if len(g) == 3:
                wiki_q[f"{p_.year}Q{p_.quarter}"] = float(g.sum())
    wiki_yoy = {k: (v / wiki_q[prev_q(k)] - 1) * 100 for k, v in wiki_q.items() if prev_q(k) in wiki_q and wiki_q[prev_q(k)]}
    rows.append(lag_row("(기존 고리) 아마존 → 수출", "아마존 한국생산 비중", "화장품 수출", qser(amz_q), qser(exp_q_yoy), "Q", 3, 12,
                        "아마존 한국생산 비중(분기 평균) 대 수출 YoY(분기 합)"))
    rows.append(lag_row("(기존 고리) 인지도 → 수출", "위키 조회수", "화장품 수출", qser(wiki_yoy), qser(exp_q_yoy), "Q", 3, 12,
                        "K-beauty 위키 조회수 YoY(분기 합) 대 수출 YoY"))
    lags = {"id": "vc_lags", "title": "인접 단계 간 시차 (분기, 교차상관)", "grade": "auto", "gradeLabel": "자동 수집", "wide": True,
            "table": {"columns": ["연결", "변환", "가장 강한 시차", "상관계수 r", "시차별 r (0~3분기)", "표본"], "rows": rows},
            "empty": "", "note": "수요 신호는 오른쪽→왼쪽(채널 → 유통 → 브랜드사 → ODM → 원부자재)으로 전해진다는 가정으로, "
                                 "x를 k분기 뒤로 밀어 y와 상관을 본다. 월·주 자료는 분기로 맞춰 계산(수출·위키는 분기 합, 아마존은 분기 평균). "
                                 "분기 표본 12개 미만은 '참고용'. 상관은 인과가 아니다."}

    # ── 6. 단계별 상세 (펼치기)
    def comp_table(st):
        rows_ = []
        for n, v in comps.items():
            if v["stage"] != st:
                continue
            ks = sorted(v["rev"])
            k = ks[-1] if ks else ""
            yoy = (v["rev"][k] / v["rev"][prev_q(k)] - 1) * 100 if k and prev_q(k) in v["rev"] and v["rev"][prev_q(k)] else None
            opm = v["op"][k] / v["rev"][k] * 100 if k in v["op"] and v["rev"].get(k) else None
            inv = v["inv"][k] / v["ttm"][k] * 365 if k in v["inv"] and v["ttm"].get(k) else None
            ar = v["ar"][k] / v["ttm"][k] * 365 if k in v["ar"] and v["ttm"].get(k) else None
            label = GRADES_SHORT[v["grade"]]
            status = ("비상장" if not v["listed"] else f"상장폐지 {v['delisted']}" if v["delisted"] else "상장")
            rows_.append([n, status, v["fs"] or "–", label, f"{k[:4]} {k[-1]}Q" if k else "–",
                          round(v["rev"][k] / 1e8) if k else "–", f"{yoy:+.1f}%" if yoy is not None else "–",
                          f"{opm:.1f}%" if opm is not None else "–", f"{inv:.0f}" if inv is not None else "–",
                          f"{ar:.0f}" if ar is not None else "–", v["note"]])
        return {"id": f"vc_tab_{STAGE_ID[st]}", "title": f"{st} 기업별 최근 분기", "grade": "auto", "gradeLabel": "자동 수집",
                "wide": True, "empty": "" if rows_ else "구성 기업 없음",
                "table": {"columns": ["기업", "상장", "재무 기준", "등급", "분기", "매출(억원)", "매출 YoY", "영업이익률",
                                      "재고일수", "채권일수", "비고"], "rows": rows_}}

    def comp_chart(st, cid=None):
        names = [n for n, v in comps.items() if v["stage"] == st and v["rev"]]
        sl = [series(f"{n} ({comps[n]['fs']})", qser({k: v / 1e8 for k, v in comps[n]["rev"].items()}), slot=i)
              for i, n in enumerate(names)][:8]
        grade = "manual" if any(comps[n]["grade"] == "manual" for n in names) else "auto"
        return chart(cid or f"vc_rev_{STAGE_ID[st]}", f"{st} 기업별 분기 매출", grade=grade, unit="억원", freq="Q", series_list=sl,
                     source=DART_SRC + (" + manual/company_quarterly.csv" if grade == "manual" else ""), collectors=["dart"],
                     note="범례 괄호는 재무 기준(연결/별도/수동 입력). YoY 전환 가능.",
                     empty_reason="수동 입력 대기 — manual/company_quarterly.csv" if st == "채널" else "")

    unl = {u["name"]: u for u in cfg.get("unlisted", [])}
    detail = {st: [comp_table(st)] for st in STAGES}
    detail["원부자재"].append(comp_chart("원부자재"))
    for c in [c for c in cfg["companies"] if c.get("delisted") and c.get("stage")]:
        detail[c["stage"]].append(card(f"delisted_{c['name']}", f"{c['name']} — 상장폐지, 이후 분기 수동 입력", "manual",
                                       f"{c['delisted']} 한국콜마와의 포괄적 주식교환으로 상장폐지(한국콜마 100% 자회사). DART 정기보고서는 "
                                       f"{c['dart_until'][:4]} 사업보고서가 마지막. 이후 분기 매출·영업이익은 manual/company_quarterly.csv에 "
                                       f"기업={c['name']}으로 입력하면 위 표·그래프와 단계 합산에 이어 붙는다(재고·채권은 없음)."))
    # ODM: 매출 + 직원 수 + CAPA 카드
    detail["ODM"].append(comp_chart("ODM", "odm_orders"))
    emp_ser = []
    for i, n in enumerate([n for n, v in comps.items() if v["stage"] == "ODM"]):
        e = ft.get((n, "별도", "직원수"), {})
        emp_ser.append(series(n, qser(e), slot=i))
        kpi.setdefault("emp", {})[n] = e
    ac = kpi.get("amz_charts", {})
    detail["ODM"] += [c for c in (ac.get("mik"), ac.get("fb")) if c]
    detail["ODM"] += [
        chart("odm_emp", "ODM 직원 수 (별도, 재투자)", grade="auto", unit="명", freq="Q", series_list=emp_ser, source=DART_SRC,
              collectors=["dart"], note="기존 플라이휠 '재투자' 고리. 분기보고서에서 직원 현황을 생략하는 회사는 반기·연간 점만 찍힌다."),
        card("reinvest_manual", "CAPA·가동률·연구개발비 (재투자)", "manual",
             "사업보고서 본문에만 있어 자동 추출하지 않는다. manual/reinvest.csv에 입력하면 리레이팅 조건 탭 1-2에 그려진다.",
             memo="신규 공장 CAPA 계획과 CAPA 산정 기준: IR 확인(비공개 자료).", source="IR 확인(비공개 자료)")]
    # 브랜드사: 매출 + 인지도(기존 고리) + 비상장
    detail["브랜드사"].append(comp_chart("브랜드사"))
    for n, u in unl.items():
        if u["stage"] == "브랜드사":
            detail["브랜드사"].append(card(f"unl_{n}", n, u.get("grade", "none"), u["note"]))
    # 유통: 매출 + 수출(기존 고리) + 재고 쌓기
    detail["유통"].append(comp_chart("유통"))
    detail["유통"] += groups["exports"]
    detail["유통"] += stock_charts(kpi)
    # 채널: 올리브영(수동) + 아마존(기존 온라인 고리) + 입점(기존 오프라인 고리) + 틱톡샵
    detail["채널"] = [comp_table("채널"), comp_chart("채널", "vc_rev_chan")]
    for n, u in unl.items():
        if u["stage"] == "채널":
            detail["채널"].append(card(f"unl_{n}", n, u.get("grade", "none"), u["note"]))
    detail["채널"] += [c for c in (ac.get("kb"), ac.get("patch")) if c]
    detail["채널"] += groups["online"] + groups["offline"] + groups["aware"]  # 온라인·오프라인·인지도(기존 고리)

    odm_rev = {}
    for n, v in comps.items():
        if v["stage"] == "ODM" and not v["delisted"]:
            for k, x in v["rev"].items():
                odm_rev[k] = odm_rev.get(k, 0) + x
    kpi["odm_sum"] = odm_rev
    kpi["stage"] = agg
    kpi["chain"] = diagram

    sections = [
        {"id": "vc-1", "title": "밸류체인 — 물건은 왼쪽→오른쪽, 수요 신호는 오른쪽→왼쪽", "items": [diagram]},
        {"id": "vc-2", "title": "단계 비교", "items": charts},
        {"id": "vc-3", "title": "인접 단계 간 시차", "items": [lags]},
    ] + [{"id": f"st-{STAGE_ID[st]}", "title": f"{i + 1}. {st} — 기업별 상세", "items": detail[st], "collapsible": True}
         for i, st in enumerate(STAGES)]
    return {"sections": sections}, kpi


GRADES_SHORT = {"auto": "자동 수집", "manual": "수동 입력", "proxy": "대용 지표", "none": "데이터 없음"}


def stock_charts(kpi) -> list[dict]:
    """재고 쌓기 대 실수요 — 미국 수출 YoY와 아마존 한국생산 비중 변화 (월)."""
    sh = kpi.get("share", pd.DataFrame())
    months = sh[sh["cat"] == "스킨케어"].copy() if len(sh) else pd.DataFrame()
    amz_m = pd.Series(dtype=float)
    if not months.empty:
        months["m"] = months["date"].str[:7] + "-01"
        amz_m = months.groupby("m")["mik_pct"].mean()
    us = kpi.get("exp_us", pd.Series(dtype=float)).copy()
    us_yoy = pd.Series(dtype=float)
    if len(us):
        us.index = [f"{m}-01" for m in us.index]
        us_yoy = (us / us.shift(12) - 1).dropna() * 100
    if not (len(us_yoy) and len(amz_m)):
        return [card("stock_us", "재고 쌓기 대 실수요", "auto", "관세청 수출과 아마존 비중이 모두 쌓이면 표시된다.")]
    amz_chg = {}
    am = amz_m.copy()
    am.index = pd.to_datetime(am.index)
    for d in pd.to_datetime(us_yoy.index):
        now = am[(am.index <= d) & (am.index > d - pd.DateOffset(months=2))]
        past = am[(am.index <= d - pd.DateOffset(months=6)) & (am.index > d - pd.DateOffset(months=9))]
        if len(now) and len(past):
            amz_chg[d.strftime("%Y-%m-%d")] = now.iloc[-1] - past.iloc[-1]
    flags = [d for d, v in amz_chg.items() if v <= 0 and us_yoy.get(d, 0) >= 15]
    areas = [{"from": d, "to": (pd.Timestamp(d) + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d"), "label": ""} for d in flags]
    a = chart("stock_us", "재고 쌓기 대 실수요 ① 미국향 화장품 수출 YoY", grade="auto", unit="%", freq="M", yoy="",
              series_list=[series("미국 수출 YoY", us_yoy)], source=CUSTOMS_SRC, collectors=["customs"],
              note="음영 = 수출은 +15% 이상 늘었는데 아마존 한국생산 비중은 6개월 전보다 늘지 않은 달(재고 쌓기 가능성). "
                   "아마존 스냅샷이 없는 달은 판단하지 않음.", sample=f"판단 가능한 달 {len(amz_chg)}개")
    b = chart("stock_amz", "재고 쌓기 대 실수요 ② 아마존 스킨케어 한국생산 비중 6개월 변화", grade="auto", unit="%p", freq="M", yoy="",
              series_list=[series("6개월 변화", pd.Series(amz_chg))], source=AMZ_SRC, collectors=["amazon"],
              note="①과 같은 달에 음영. 두 지표는 단위가 달라 한 축에 겹치지 않았다.")
    a["areas"] = b["areas"] = areas
    return [a, b]
