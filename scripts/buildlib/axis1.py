"""축 1. 멀티플 리레이팅 조건 — 성장의 분산·지속성, 이익의 질, 시장 반영 정도."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

import pandas as pd

from .common import (available_from, card, chart, direction, manual, obs, q_date, series, standalone_quarters,
                     ttm, yoy_pct)

CUSTOMS_SRC = "관세청 수출입무역통계 (HS 3304, 출하 기준)"
DART_SRC = "DART OpenAPI 정기보고서"
STOCK_SRC = "금융위원회 주식시세정보"


# ── 공통 로더 ─────────────────────────────────────────────────────
def customs_pivot(hs: str, ind: str = "수출액") -> tuple[pd.DataFrame, dict]:
    """월 × 국가코드 표, 국가코드→국가명. hs로 시작하는 저장 코드 중 가장 얕은 단계만 합산한다
    (예: 3304 → 6자리 하위 코드 합, 330499 → 6자리 행 그대로, 3304991000 → 10자리 행). 이중 집계 방지."""
    df = obs("customs")
    if df.empty:
        return pd.DataFrame(), {}
    parts = df["구분"].str.split("|", expand=True)
    df = df.assign(hs=parts[0], cc=parts[1], nm=parts[2])
    df = df[(df["지표"] == ind) & df["hs"].str.startswith(hs)]
    if df.empty:
        return pd.DataFrame(), {}
    depth = df["hs"].str.len().min()
    df = df[df["hs"].str.len() == depth]
    names = dict(zip(df["cc"], df["nm"]))
    p = df.pivot_table(index="기준일", columns="cc", values="값", aggfunc="sum").sort_index().fillna(0.0)
    return p, names


# 지역 색 고정 (색은 지역을 따라간다). 홍콩과 중남미는 같은 차트에 함께 나오지 않아 같은 번호를 쓴다.
REGION_SLOT = {"중국": 0, "미국": 1, "일본": 2, "홍콩": 3, "유럽": 4, "동남아": 5, "러시아·CIS": 6, "중동": 7, "중남미": 3,
               "기타": "muted"}
REGIONS_8 = ["중국", "미국", "일본", "유럽", "동남아", "러시아·CIS", "중동", "중남미"]


def region_of(cfg) -> dict[str, str]:
    m = {}
    for reg, ccs in cfg["customs"]["regions"].items():
        for c in ccs:
            m[c] = reg
    return m


def fin_table() -> dict[tuple[str, str, str], dict[str, float]]:
    """(회사, 연결|별도, 지표) → {기준일: 값}"""
    df = obs("dart")
    out: dict = defaultdict(dict)
    for r in df.itertuples():
        comp, fs = r.구분.split("|")
        out[(comp, fs, r.지표)][r.기준일] = r.값
    return out


def manual_quarters(comp: str, ind: str) -> dict[str, float]:
    """manual/company_quarterly.csv (기업, 분기 YYYYQn, 항목, 값, 단위 억원|원) → {분기: 원}."""
    df = manual("company_quarterly")
    if df.empty:
        return {}
    out = {}
    for r in df[(df["기업"].str.strip() == comp) & (df["항목"].str.strip() == ind)].itertuples():
        v = pd.to_numeric(str(r.값).replace(",", ""), errors="coerce")
        if pd.notna(v):
            out[str(r.분기).strip().upper()] = float(v) * (1e8 if "억" in str(r.단위) else 1.0)
    return out


def is_quarters(ft, comp: str, ind: str, prefer: str = "연결") -> tuple[dict[str, float], str]:
    """분기 단독 손익. 연결 우선이지만, 연결이 별도보다 먼저 끊긴 회사(예: 자회사 정리로 연결 작성 중단)는
    한 회사 안에서 기준이 섞이지 않도록 전 기간 별도를 쓴다. 기준은 회사 단위로 매출액 기준으로 정한다."""
    basis = company_basis(ft, comp) if prefer == "연결" else prefer
    v = ft.get((comp, basis, ind))
    return (standalone_quarters(v), basis) if v else ({}, "")


def company_basis(ft, comp: str) -> str:
    last = lambda d: max((k.replace("FY", "Q4") for k in d), default="")  # noqa: E731
    c, o = ft.get((comp, "연결", "매출액"), {}), ft.get((comp, "별도", "매출액"), {})
    if c and (not o or last(c) >= last(o)):
        return "연결"
    return "별도" if o else ("연결" if c else "")


def net_income_q(ft, comp: str) -> dict[str, float]:
    """PER용 분기 순이익. 매출과 같은 재무 기준(company_basis)을 따른다 — 연결이면 지배주주순이익(없으면 당기순이익)."""
    basis = company_basis(ft, comp)
    if basis == "별도":
        return standalone_quarters(ft.get((comp, "별도", "당기순이익"), {}))
    ctrl, total = ft.get((comp, "연결", "지배주주순이익"), {}), ft.get((comp, "연결", "당기순이익"), {})
    # 지배주주순이익이 일부 분기에만 잡혀 있으면(계정명 차이) 연속된 당기순이익을 쓴다 — 섞어 쓰지 않는다
    return standalone_quarters(ctrl if len(ctrl) >= len(total) else total)


def month_label(m: str) -> str:
    return f"{m}-01" if len(m) == 7 else m


# ── 1-1 성장의 지역 분산 ───────────────────────────────────────────
def geo(cfg) -> tuple[list[dict], dict]:
    p, names = customs_pivot("3304")
    regmap = region_of(cfg)
    major = ["중국", "미국", "일본", "홍콩", "유럽"]
    emerging = ["동남아", "러시아·CIS", "중동", "중남미", "기타"]
    kpi = {}
    if p.empty:
        empty = dict(grade="auto", unit="%", freq="M", series_list=[], source=CUSTOMS_SRC, collectors=["customs"])
        return [chart("geo_share", "지역별 수출 비중 ① 주요 시장 (12개월 누적)", yoy="diff", **empty),
                chart("geo_hhi", "수출 국가 집중도 HHI (12개월 누적)", yoy="diff", **{**empty, "unit": "pt"}),
                chart("geo_top3", "상위 3개국 비중 (12개월 누적)", yoy="diff", **empty)], kpi
    r12 = p.rolling(12).sum().dropna(how="all")
    total = r12.sum(axis=1)
    share = r12.div(total, axis=0) * 100
    reg = share.T.groupby(lambda c: regmap.get(c, "기타")).sum().T
    s_major = [series(n, reg[n].rename(index=month_label), slot=i) for i, n in enumerate(major) if n in reg]
    s_emerg = [series(n, reg[n].rename(index=month_label), slot=REGION_SLOT[n]) for n in emerging if n in reg]
    etc_share = float(reg["기타"].iloc[-1]) if "기타" in reg else 0.0
    etc_table = None
    if etc_share > 10:
        last = share.iloc[-1]
        etc = last[[c for c in last.index if regmap.get(c, "기타") == "기타"]].sort_values(ascending=False)
        cum, rows = 0.0, []
        for c, v in etc.head(15).items():
            cum += v
            rows.append([names.get(c, c), c, round(float(v), 2), round(cum, 2)])
        etc_table = {"id": "geo_etc", "title": f"'기타' 상위 국가 (12개월 누적, 기타 합계 {etc_share:.1f}%)", "grade": "auto",
                     "gradeLabel": "자동 수집", "table": {"columns": ["국가", "코드", "전체 대비 비중(%)", "누적(%)"], "rows": rows},
                     "empty": "", "note": "기타가 10%를 넘어서 표시. 비중이 큰 나라는 config/tracker.yml의 regions에 묶음을 추가할 수 있다."}
    hhi = (share ** 2).sum(axis=1)
    top3 = share.apply(lambda row: row.nlargest(3).sum(), axis=1)
    top3_names = [names.get(c, c) for c in share.iloc[-1].nlargest(3).index]
    kpi["hhi"] = hhi.tolist()
    kpi["china"] = reg["중국"].tolist() if "중국" in reg else []
    kpi["last_month"] = share.index[-1]
    charts = [
        chart("geo_share", "지역별 수출 비중 ① 주요 시장 (12개월 누적)", grade="auto", unit="%", freq="M", yoy="diff",
              series_list=s_major, source=CUSTOMS_SRC, collectors=["customs"],
              note="HS 3304 수출액 기준, 최근 12개월 합계로 비중 계산. 홍콩은 상당 부분이 중국 재수출이라 따로 표시."),
        chart("geo_share2", "지역별 수출 비중 ② 신흥 시장·기타 (12개월 누적)", grade="auto", unit="%", freq="M", yoy="diff",
              series_list=s_emerg, source=CUSTOMS_SRC, collectors=["customs"],
              note="동남아 = 베트남·태국·인도네시아·말레이시아·필리핀·싱가포르. 러시아·CIS, 중동, 중남미 구성은 "
                   "config/tracker.yml의 regions. ①과 ②를 합치면 100%."),
        *([etc_table] if etc_table else []),
        chart("geo_hhi", "수출 국가 집중도 HHI (12개월 누적)", grade="auto", unit="pt", freq="M", yoy="diff",
              series_list=[series("HHI", hhi.rename(index=month_label))], source=CUSTOMS_SRC, collectors=["customs"],
              note="HHI = Σ(국가별 비중%)². 10,000이면 한 나라에 전부, 낮을수록 분산. 1,500 미만을 흔히 '분산'으로 본다."),
        chart("geo_top3", "상위 3개국 비중 (12개월 누적)", grade="auto", unit="%", freq="M", yoy="diff",
              series_list=[series("상위 3개국", top3.rename(index=month_label))], source=CUSTOMS_SRC, collectors=["customs"],
              note=f"최근 기준 상위 3개국: {', '.join(top3_names)}"),
    ]
    return charts, kpi


# ── 1-2 이익의 질 ─────────────────────────────────────────────────
def earnings_quality(cfg) -> tuple[list[dict], dict]:
    ft = fin_table()
    odm = [c["name"] for c in cfg["companies"] if c["group"] == "ODM"]
    rev_s, opm_s, pc_s, ratio_s, inv_s, ar_s, cl_s = [], [], [], [], [], [], []
    growth_charts = []
    kpi = {"opm": {}, "pc": {}, "ratio": {}, "rev_yoy": {}}
    for comp in odm:
        rev, fs = is_quarters(ft, comp, "매출액")
        op, _ = is_quarters(ft, comp, "영업이익")
        if rev:
            rev_s.append(series(comp, pd.Series({q_date(k): v / 1e8 for k, v in rev.items()})))
            opm = {k: op[k] / rev[k] * 100 for k in rev if k in op and rev[k]}
            opm_s.append(series(comp, pd.Series({q_date(k): v for k, v in opm.items()})))
            kpi["opm"][comp] = [opm[k] for k in sorted(opm)]
            kpi["rev_yoy"][comp] = yoy_pct(rev)
            ratio = {}
            for k in rev:
                if k.endswith("Q4") and f"{k[:4]}Q3" in rev and rev[f"{k[:4]}Q3"]:
                    ratio[f"{k[:4]}-01-01"] = rev[k] / rev[f"{k[:4]}Q3"]
            ratio_s.append(series(comp, pd.Series(ratio)))
            kpi["ratio"][comp] = {k[:4]: v for k, v in ratio.items()}
            # 운전자본 (연결 재무상태표 기말 ÷ TTM 매출)
            rt = ttm(rev)
            for ind, bucket in (("재고자산", inv_s), ("매출채권", ar_s)):
                bs = ft.get((comp, fs, ind), {})
                days = {q_date(k): bs[k] / rt[k] * 365 for k in rt if k in bs and rt[k]}
                bucket.append(series(comp, pd.Series(days)))
            cl = ft.get((comp, fs, "계약부채"), {})
            cl_s.append(series(comp, pd.Series({q_date(k): cl[k] / rt[k] * 100 for k in rt if k in cl and rt[k]})))
        # 인당 매출: 별도 TTM 매출 ÷ 별도 직원 수 (직원 수가 별도 기준이므로 분자도 별도)
        rev_o = standalone_quarters(ft.get((comp, "별도", "매출액"), {}))
        emp = ft.get((comp, "별도", "직원수"), {})
        rt_o = ttm(rev_o)
        pc = {k: rt_o[k] / emp[k] / 1e8 for k in emp if k in rt_o and emp[k]}
        pc_s.append(series(comp, pd.Series({q_date(k): v for k, v in sorted(pc.items())})))
        kpi["pc"][comp] = [pc[k] for k in sorted(pc)]
        rev_g = {k: (rt_o[k] / rt_o[p] - 1) * 100 for k in rt_o if (p := f"{int(k[:4]) - 1}{k[4:]}") in rt_o and rt_o[p]}
        emp_g = {k: (emp[k] / emp[p] - 1) * 100 for k in emp if (p := f"{int(k[:4]) - 1}{k[4:]}") in emp and emp[p]}
        growth_charts.append(chart(
            f"lev_{comp}", f"{comp}: 매출 증가율 대 인력 증가율 (별도, YoY)", grade="auto", unit="%", freq="Q", yoy="",
            series_list=[series("매출 증가율(TTM)", pd.Series({q_date(k): v for k, v in rev_g.items()})),
                         series("직원 수 증가율", pd.Series({q_date(k): v for k, v in emp_g.items()}))],
            source=DART_SRC, collectors=["dart"],
            note="두 선의 간격이 벌어질수록 인력 대비 매출 레버리지. 직원 수는 정기보고서 '직원 현황'(별도)이라 "
                 "분기보고서에서 생략되면 반기·연간 점만 찍힌다."))
    src = DART_SRC
    charts = [
        chart("odm_rev", "ODM 4사 분기 매출 (연결)", grade="auto", unit="억원", freq="Q", series_list=rev_s,
              source=src, collectors=["dart"], note="4분기 단독 = 사업보고서 연간 − 1~3분기. 연결이 없으면 별도."),
        chart("odm_opm", "ODM 4사 영업이익률 (연결)", grade="auto", unit="%", freq="Q", yoy="diff", series_list=opm_s,
              source=src, collectors=["dart"]),
        chart("odm_pc", "인당 매출 (별도 TTM 매출 ÷ 별도 직원 수)", grade="auto", unit="억원/명", freq="Q", series_list=pc_s,
              source=src, collectors=["dart"],
              note="해외 법인 인력은 빠져 있어 별도 기준으로만 비교. 인력 대비 매출 레버리지에 대한 회사 설명은 IR 확인(비공개 자료)."),
        *growth_charts,
        chart("odm_q4q3", "계절성: 4Q ÷ 3Q 매출 비율 (연결)", grade="auto", unit="배", freq="Y", yoy="diff", kind="bar",
              series_list=ratio_s, source=src, collectors=["dart"],
              note="1에 가까울수록 4분기 계절적 감소가 약하다. 4분기 발주 패턴 변화에 대한 설명은 IR 확인(비공개 자료)."),
    ]
    return charts, {**kpi, "wc": (inv_s, ar_s, cl_s)}


def employee_coverage(cfg) -> dict:
    """DART 직원 현황(별도)이 회사·분기별로 들어왔는지."""
    ft = fin_table()
    years = list(range(int(cfg["backfill_start"][:4]) - 1, pd.Timestamp.now().year + 1))
    rows = []
    for c in cfg["companies"]:
        e = ft.get((c["name"], "별도", "직원수"), {})
        rev = ft.get((c["name"], "별도", "매출액"), {}) or ft.get((c["name"], "연결", "매출액"), {})
        filed = {k.replace("FY", "Q4") for k in rev}  # 재무가 있는 분기 = 보고서가 있는 분기
        cells, miss = [], []
        for y in years:
            got = [f"{i}Q" for i in range(1, 5) if f"{y}Q{i}" in e]
            gap = [f"{y}Q{i}" for i in range(1, 5) if f"{y}Q{i}" in filed and f"{y}Q{i}" not in e]
            miss += gap
            cells.append("·".join(got) if got else ("–" if not any(f"{y}Q{i}" in filed for i in range(1, 5)) else "없음"))
        rows.append([c["name"], *cells, len(e), ", ".join(miss) if miss else "없음"])
    return {"id": "emp_cov", "title": "직원 수(DART 직원 현황, 별도) 수집 현황", "grade": "auto", "gradeLabel": "자동 수집",
            "table": {"columns": ["기업", *[str(y) for y in years], "합계(분기)", "보고서는 있는데 직원 수가 빠진 분기"], "rows": rows},
            "empty": "", "note": "칸 = 직원 수가 들어온 분기. '–'는 그해 정기보고서 자체가 없음(상장 전·폐지 후). "
                                 "분기보고서(1Q·3Q)는 직원 현황을 생략하는 회사가 많아 빠진 분기는 대부분 1Q·3Q."}


def reinvest_manual() -> list[dict]:
    df = manual("reinvest")
    out = []
    items = [("CAPA", "CAPA (수동 입력)"), ("가동률", "가동률 (수동 입력)"), ("연구개발비", "연구개발비 (수동 입력)")]
    for key, title in items:
        sub = df[df["항목"].str.strip() == key] if not df.empty else df
        s_list, unit = [], ""
        if not sub.empty:
            unit = sub["단위"].iloc[0]
            for comp, g in sub.groupby("기업"):
                pts = {}
                for r in g.itertuples():
                    d = str(r.기준일).strip()
                    d = q_date(d) if "Q" in d else (f"{d}-01-01" if len(d) == 4 else d)
                    pts[d] = pd.to_numeric(r.값, errors="coerce")
                s_list.append(series(comp, pd.Series(pts).sort_index()))
        out.append(chart(f"manual_{key}", title, grade="manual", unit=unit, freq="Y", series_list=s_list,
                         source="사업보고서 II. 사업의 내용 (수동 입력: manual/reinvest.csv)", yoy="pct",
                         empty_reason="수동 입력 대기 — manual/reinvest.csv에 행 추가 (README '수동 입력')"))
    return out


# ── 1-3 시장 반영 정도 ─────────────────────────────────────────────
def valuation(cfg) -> tuple[list[dict], dict, pd.DataFrame]:
    st = obs("stocks")
    ft = fin_table()
    groups = {c["name"]: c["group"] for c in cfg["companies"]}
    kpi = {}
    if st.empty:
        empty = dict(grade="auto", freq="W", series_list=[], source=STOCK_SRC, collectors=["stocks", "dart"])
        return [chart("mcap_group", "그룹별 시가총액 합계", unit="조원", **empty),
                chart("per_group", "그룹별 후행 PER (합산 기준)", unit="배", yoy="diff", **empty),
                chart("per_gap", "ODM PER ÷ 브랜드 PER", unit="배", yoy="diff", **empty)], kpi, pd.DataFrame()
    mc = st[st["지표"] == "시가총액"].pivot_table(index="기준일", columns="구분", values="값").sort_index()
    mc.index = pd.to_datetime(mc.index)
    wk = mc.resample("W-FRI").last()
    wk.index = [min(i, mc.index.max()) for i in wk.index]  # 마지막 주 라벨이 아직 오지 않은 금요일이 되지 않게
    for c in cfg["companies"]:  # 상장폐지 이후는 시총·PER 집계에서 뺀다
        if c.get("delisted") and c["name"] in wk:
            wk.loc[wk.index >= pd.Timestamp(c["delisted"]), c["name"]] = float("nan")
    # 후행 4분기 지배주주순이익 (그 시점에 공시로 알 수 있었던 최근 분기 기준)
    ni_ttm = {}
    for comp in wk.columns:
        t = ttm(net_income_q(ft, comp))
        ser = {}
        for d in wk.index:
            avail = [k for k in t if available_from(k) <= d.date()]
            if avail:
                ser[d] = t[max(avail)]
        ni_ttm[comp] = pd.Series(ser, dtype=float)
    ni = pd.DataFrame(ni_ttm).reindex(wk.index)
    per = wk / ni.where(ni > 0)
    fmt = lambda s: s.rename(index=lambda d: d.strftime("%Y-%m-%d"))  # noqa: E731
    g_mcap, g_per = {}, {}
    for g in ["ODM", "원부자재", "브랜드", "유통", "메디컬"]:
        cols = [c for c in wk.columns if groups.get(c) == g]
        if not cols:
            continue
        g_mcap[g] = wk[cols].sum(axis=1, min_count=1) / 1e12
        pos = ni[cols].where(ni[cols] > 0)
        g_per[g] = wk[cols].where(pos.notna()).sum(axis=1, min_count=1) / pos.sum(axis=1, min_count=1)
    gap = (g_per.get("ODM") / g_per.get("브랜드")) if "ODM" in g_per and "브랜드" in g_per else pd.Series(dtype=float)
    kpi["gap"] = gap.dropna().tolist()
    kpi["per"] = {g: s.dropna().tolist() for g, s in g_per.items()}
    per_note = ("후행 PER = 시가총액 ÷ 최근 4개 분기 지배주주순이익(계정이 일부 분기만 잡힌 회사는 당기순이익, 별도 기준 회사는 별도 당기순이익. 공시 기한이 지난 분기만 사용 — 1Q 5/15, 2Q 8/14, "
                "3Q 11/14, 4Q 다음해 3/31). 그룹 PER은 흑자 기업의 시총 합 ÷ 순이익 합. 적자 기업은 제외.")
    charts = [
        chart("mcap_group", "그룹별 시가총액 합계 (주간)", grade="auto", unit="조원", freq="W",
              series_list=[series(g, fmt(s)) for g, s in g_mcap.items()], source=STOCK_SRC, collectors=["stocks"],
              note="그룹 구성은 config/tracker.yml의 companies."),
        chart("per_group", "그룹별 후행 PER (주간, 합산 기준)", grade="auto", unit="배", freq="W", yoy="diff",
              series_list=[series(g, fmt(s)) for g, s in g_per.items()], source=f"{STOCK_SRC} + {DART_SRC}",
              collectors=["stocks", "dart"], note=per_note),
        chart("per_gap", "ODM PER ÷ 브랜드 PER (주간)", grade="auto", unit="배", freq="W", yoy="diff",
              series_list=[series("ODM ÷ 브랜드", fmt(gap))], source=f"{STOCK_SRC} + {DART_SRC}",
              collectors=["stocks", "dart"], note="1보다 작으면 ODM이 브랜드보다 할인. 상승하면 격차 축소(ODM 상대 리레이팅)."),
    ]
    latest = pd.DataFrame({"그룹": pd.Series(groups), "시가총액(조원)": wk.iloc[-1] / 1e12, "후행PER": per.iloc[-1],
                           "TTM순이익(억원)": ni.iloc[-1] / 1e8})
    latest.index.name = "기업"
    latest = latest.dropna(subset=["시가총액(조원)"]).sort_values(["그룹", "시가총액(조원)"], ascending=[True, False])
    return charts, kpi, latest.reset_index()


def peers_table() -> dict:
    df = manual("global_peers")
    rows = []
    if not df.empty:
        df = df.sort_values("기준일").drop_duplicates(["기업", "항목"], keep="last")
        rows = df[["기업", "국가", "기준일", "항목", "값", "단위", "출처"]].values.tolist()
    return {"id": "global_peers", "title": "글로벌 피어 (수동 입력)", "grade": "manual", "gradeLabel": "수동 입력",
            "table": {"columns": ["기업", "국가", "기준일", "항목", "값", "단위", "출처"], "rows": rows},
            "empty": "" if rows else "수동 입력 대기 — manual/global_peers.csv (인터코스는 무료 자동 수집 경로 없음: "
                                     "Stooq는 JS 검증 페이지로 차단, Yahoo는 429 응답)"}


# ── 1-4 주가 상승 분해 ────────────────────────────────────────────
def decomposition() -> list[dict]:
    eps = manual("consensus_eps")
    st = obs("stocks")
    if eps.empty or st.empty:
        why = "수동 입력 대기 — manual/consensus_eps.csv (기업, 기준월 YYYY-MM, 선행12개월EPS)" if eps.empty else \
              "주가 데이터 없음(stocks 수집기 상태 확인)"
        return [chart("decomp", "주가 변화 분해: EPS 변화 × PER 변화", grade="manual", unit="%", freq="M",
                      series_list=[], source="수동 입력 EPS + 주가", empty_reason=why, yoy="")]
    px = st[st["지표"] == "종가"].copy()
    px["m"] = px["기준일"].str[:7]
    px = px.sort_values("기준일").groupby(["구분", "m"])["값"].last()
    out = []
    for comp, g in eps.groupby("기업"):
        g = g.assign(eps=pd.to_numeric(g["선행12개월EPS"], errors="coerce")).dropna(subset=["eps"]).sort_values("기준월")
        pts = [(m, px.get((comp, m)), e) for m, e in zip(g["기준월"], g["eps"]) if px.get((comp, m)) and e > 0]
        if len(pts) < 2:
            continue
        m0, p0, e0 = pts[0]
        per0 = p0 / e0
        tot, e_c, m_c, fper = {}, {}, {}, {}
        for m, p, e in pts:
            d = f"{m}-01"
            tot[d] = math.log(p / p0) * 100
            e_c[d] = math.log(e / e0) * 100
            m_c[d] = math.log((p / e) / per0) * 100
            fper[d] = p / e
        out.append(chart(f"decomp_{comp}", f"{comp}: 주가 변화 분해 ({m0} 대비 누적, 로그%)", grade="manual", unit="%",
                         freq="M", yoy="", source="수동 입력 컨센서스 EPS + 금융위 종가",
                         series_list=[series("주가", pd.Series(tot)), series("EPS 기여", pd.Series(e_c)),
                                      series("PER 기여", pd.Series(m_c))],
                         note="로그 변화라 '주가 = EPS 기여 + PER 기여'가 정확히 성립. EPS 기여가 크면 이익 상향, "
                              "PER 기여가 크면 멀티플 확장."))
        out.append(chart(f"fper_{comp}", f"{comp}: 12개월 선행 PER", grade="manual", unit="배", freq="M", yoy="diff",
                         source="수동 입력 컨센서스 EPS + 금융위 종가", series_list=[series("선행 PER", pd.Series(fper))]))
    return out or [chart("decomp", "주가 변화 분해", grade="manual", unit="%", freq="M", series_list=[], source="",
                         empty_reason="기업별로 2개월 이상 입력이 필요", yoy="")]


# ── 1-5 주문 가시성 ───────────────────────────────────────────────
def visibility(wc) -> list[dict]:
    inv_s, ar_s, cl_s = wc
    return [
        card("visibility", "주문 가시성 (선주문 기간)", "none",
             "주문 기간이 얼마나 앞서 잡히는지는 공시·공공 데이터에 없다. IR·고객사 확인 영역.",
             memo="선주문 기간 변화: IR 확인(비공개 자료).", source="IR 확인(비공개 자료)"),
        chart("wc_inv", "대용: 재고자산 회전일수 (재고 ÷ TTM 매출 × 365)", grade="proxy", unit="일", freq="Q",
              series_list=inv_s, source=DART_SRC, collectors=["dart"],
              note="납기가 길어지면 원부자재를 미리 확보해 재고가 늘 수 있다. "
                   "다만 재고 부진과 구분되지 않으므로 매출 증가율과 함께 읽을 것. 약한 대용 지표."),
        chart("wc_cl", "대용: 계약부채 ÷ TTM 매출", grade="proxy", unit="%", freq="Q", yoy="diff", series_list=cl_s,
              source=DART_SRC, collectors=["dart"],
              note="고객이 선급한 금액. 늘면 선주문 신호에 가장 가깝지만 ODM은 선수금 관행이 약해 값이 작을 수 있다."),
        chart("wc_ar", "참고: 매출채권 회전일수", grade="proxy", unit="일", freq="Q", series_list=ar_s,
              source=DART_SRC, collectors=["dart"],
              note="대금 결제 조건을 반영할 뿐 주문 가시성과는 관계가 약하다 — 가시성 대용으로는 부적합(참고용)."),
    ]


def build(cfg) -> tuple[dict, dict]:
    geo_c, geo_k = geo(cfg)
    eq_c, eq_k = earnings_quality(cfg)
    val_c, val_k, latest = valuation(cfg)
    sections = [
        {"id": "1-1", "title": "1-1. 성장의 지역 분산", "items": geo_c},
        {"id": "1-2", "title": "1-2. 이익의 질 (ODM 4사)", "items": eq_c + [employee_coverage(cfg)] + reinvest_manual()},
        {"id": "1-3", "title": "1-3. 시장 반영 정도", "items": val_c + [
            {"id": "val_table", "title": "기업별 최신 밸류에이션", "grade": "auto", "gradeLabel": "자동 수집",
             "table": {"columns": list(latest.columns), "rows": latest.round(2).fillna("").values.tolist()},
             "empty": "" if not latest.empty else "주가·재무 수집 전"}, peers_table()]},
        {"id": "1-4", "title": "1-4. 주가 상승 분해 (이익 상향 대 멀티플 확장)", "items": decomposition()},
        {"id": "1-5", "title": "1-5. 주문 가시성", "items": visibility(eq_k.pop("wc"))},
    ]
    return {"sections": sections}, {"geo": geo_k, "eq": eq_k, "val": val_k}
