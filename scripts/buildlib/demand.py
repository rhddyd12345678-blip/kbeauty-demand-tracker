"""「전방수요 모니터」 영역 (기존 kbeauty-demand-tracker에서 온 화면).

수출·수입시장 / 아마존 / 검색·인지도 / 기업·주가 탭. 리레이팅·밸류체인 영역과 같은 지표는 새로 계산하지 않고
같은 차트 명세(같은 data/obs CSV)를 그대로 가져다 쓴다.
"""
from __future__ import annotations

import pandas as pd

from .axis1 import CUSTOMS_SRC, DART_SRC, STOCK_SRC, company_basis, customs_pivot, fin_table, is_quarters, net_income_q
from .common import card, chart, manual, obs, q_date, series, ttm

CT_SRC = "UN Comtrade 공개 API (보고국 수입 통계)"


def find(sections: list[dict], ids: list[str]) -> list[dict]:
    """다른 탭에서 만든 항목을 id로 가져온다(같은 데이터, 같은 계산)."""
    idx = {it["id"]: it for sec in sections for it in sec["items"]}
    return [idx[i] for i in ids if i in idx]


def monthly(p: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    p.index = [f"{m}-01" for m in p.index]
    return p


# ── 수출·수입시장 ─────────────────────────────────────────────────
def exports(cfg, a1, a2) -> list[dict]:
    items = []
    v, _ = customs_pivot("3304")
    items.append(chart("exp_total_m", "관세청 월별 화장품 수출액 (HS 3304)", grade="auto", unit="백만 달러", freq="M",
                       series_list=[series("화장품 수출", monthly(v).sum(axis=1) / 1e6)] if not v.empty else [],
                       source=CUSTOMS_SRC, collectors=["customs"],
                       note="YoY 전환으로 전년 동월 대비 증감률. 기존 트래커의 '관세청 월별 수출액'·'전년동월 대비 증감률' 화면. "
                            "2019~2020년은 합치면서 같은 API로 추가 백필."))
    subs = [("330410", "입술"), ("330420", "눈"), ("330430", "매니큐어"), ("330491", "파우더"), ("3304991000", "기초"),
            ("3304992000", "메이크업"), ("3304993000", "어린이용"), ("3304999000", "기타")]
    sl = []
    for i, (hs, nm) in enumerate(subs):
        p, _ = customs_pivot(hs)
        if not p.empty:
            sl.append(series(f"{nm}({hs})", monthly(p).sum(axis=1) / 1e6, slot=i))
    items.append(chart("exp_items", "품목별 화장품 수출액 (월)", grade="auto", unit="백만 달러", freq="M", series_list=sl,
                       source=CUSTOMS_SRC, collectors=["customs"], note="3304.99는 10단위(기초·메이크업·어린이용·기타)로 나눠 표시."))
    sl = []
    for i, (hs, nm) in enumerate([("300249", "보툴리눔 톡신 등(3002.49)"), ("300490", "기타 의약품(3004.90)"), ("9018", "의료기기(9018)")]):
        p, _ = customs_pivot(hs)
        if not p.empty:
            sl.append(series(nm, monthly(p).sum(axis=1) / 1e6, slot=i))
    items.append(chart("exp_pharma", "톡신·의약품·의료기기 수출액 (월)", grade="auto", unit="백만 달러", freq="M", series_list=sl,
                       source=CUSTOMS_SRC, collectors=["customs"],
                       note="기존 트래커에서 이관한 3002.49·3004.90 포함. 3002.49는 톡신 외 면역제품이 섞여 절대값보다 방향·YoY로 해석."))
    items += find(a2["sections"], ["exp_region"]) + find(a1["sections"], ["geo_share", "geo_share2", "geo_hhi"])
    return items


def comtrade_items() -> list[dict]:
    df = obs("comtrade")
    if df.empty:
        return [chart("ct_us_share", "미국 화장품 수입 중 한국 점유율", grade="auto", unit="%", freq="M", series_list=[],
                      source=CT_SRC, collectors=["comtrade"])]
    parts = df["구분"].str.split("|", expand=True)
    df = df.assign(rep=parts[0], cmd=parts[1], pc=parts[2], pn=parts[3])
    out = []
    for rep in ("미국", "일본"):
        sub = df[(df["rep"] == rep) & (df["cmd"] == "3304")]
        if sub.empty:
            continue
        p = sub.pivot_table(index="기준일", columns="pc", values="값", aggfunc="sum").sort_index().fillna(0.0)
        names = dict(zip(sub["pc"], sub["pn"]))
        total = p["0"] if "0" in p else p.sum(axis=1)
        partners = p.drop(columns=["0"], errors="ignore")
        r12 = partners.rolling(12).sum().dropna(how="all")
        t12 = total.rolling(12).sum().reindex(r12.index)
        share = r12.div(t12, axis=0) * 100
        sl = [series(n, monthly(share[[c]])[c], slot=i) for i, (c, n) in enumerate([("410", "한국"), ("251", "프랑스")]) if c in share]
        rank = share.rank(axis=1, ascending=False).get("410")
        rank_note = f" 최근 12개월 누적 기준 한국 {int(rank.iloc[-1])}위({share.index[-1]})." if rank is not None and len(rank) else ""
        out.append(chart(f"ct_{'us' if rep == '미국' else 'jp'}_share", f"{rep} 화장품(HS 3304) 수입 중 점유율 — 한국 대 프랑스 (12개월 누적)",
                         grade="auto", unit="%", freq="M", yoy="diff", series_list=sl, source=CT_SRC, collectors=["comtrade"],
                         note="월간 수치는 선적 시점에 따라 출렁여 12개월 합계로 추세를 본다. 보고국 공표가 1~2개월 늦다." + rank_note))
        top = partners.iloc[-12:].sum().sort_values(ascending=False).index[:7]
        m = monthly(partners) / 1e6
        sl = [series(names.get(c, c), m[c], slot=i) for i, c in enumerate(top)]
        rest = [c for c in partners.columns if c not in top]
        if rest:
            sl.append(series("기타", m[rest].sum(axis=1), slot="muted"))
        out.append(chart(f"ct_{'us' if rep == '미국' else 'jp'}_by", f"{rep} 화장품 수입 — 국가별 (월, 최근 12개월 상위 7개국)", grade="auto",
                         unit="백만 달러", freq="M", series_list=sl, source=CT_SRC, collectors=["comtrade"]))
    sub = df[(df["rep"] == "미국") & (df["cmd"] == "300249")]
    if not sub.empty:
        p = sub.pivot_table(index="기준일", columns="pc", values="값", aggfunc="sum").sort_index().fillna(0.0).drop(columns=["0"], errors="ignore")
        names = dict(zip(sub["pc"], sub["pn"]))
        top = p.iloc[-12:].sum().sort_values(ascending=False).index[:5]
        top = list(dict.fromkeys(["410", *top]))[:6]
        m = monthly(p) / 1e6
        out.append(chart("ct_us_toxin", "미국 HS 3002.49(보툴리눔 톡신 등) 수입 — 국가별 (월)", grade="proxy", unit="백만 달러", freq="M",
                         series_list=[series(names.get(c, c), m[c], slot=i) for i, c in enumerate(top) if c in m],
                         source=CT_SRC, collectors=["comtrade"],
                         note="한국산 톡신 미국 판매의 대용 지표. 백신 외 면역제품이 섞여 있어 방향·YoY로 해석."))
    return out


# ── 아마존 ───────────────────────────────────────────────────────
def amazon(a2) -> list[dict]:
    from .axis2 import amazon_table
    items = find(a2["sections"], ["amz_mik_all", "amz_kb_all", "amz_fb", "amz_patch",
                                  "amz_스킨케어", "amz_선케어", "amz_뷰티 전체", "amz_페이셜 트리트먼트"])
    tab, _ = amazon_table()
    for cat in ("스킨케어", "선케어", "페이셜 트리트먼트", "뷰티 전체"):
        sub = tab[tab["cat"] == cat] if not tab.empty else tab
        if sub.empty:
            continue
        last = sub["date"].max()
        s = sub[sub["date"] == last].sort_values("rank")
        rows = [[int(r["rank"]), r["brand"] or "미분류", "●" if r["mik"] else "", "●" if r["kb"] else "",
                 "●" if r["mik"] and not r["kb"] else "", "●" if r.get("patch") else "", (r["title"] or "")[:80]]
                for _, r in s.iterrows()]
        items.append({"id": f"amz_list_{cat}", "title": f"최신 스냅샷 {cat} Top50 ({last})", "grade": "auto", "gradeLabel": "자동 수집",
                      "wide": True, "table": {"columns": ["순위", "브랜드", "한국생산", "K브랜드", "한국생산·외국 브랜드", "패치", "제품명"], "rows": rows}, "empty": "",
                      "note": "기존 트래커의 '최신 스냅샷 Top50' 화면. 브랜드 판별은 config/brand_master.csv."})
    items += find(a2["sections"], ["amz_churn", "amz_unmapped"])
    return items


# ── 기업·주가 ─────────────────────────────────────────────────────
def companies(cfg, a1, a3) -> list[dict]:
    ft = fin_table()
    st = obs("stocks")
    px = st[st["지표"] == "종가"].pivot_table(index="기준일", columns="구분", values="값").sort_index() if not st.empty else pd.DataFrame()
    mc = st[st["지표"] == "시가총액"].pivot_table(index="기준일", columns="구분", values="값").sort_index() if not st.empty else pd.DataFrame()
    ix = obs("index")
    items = []
    # 커버리지
    rows = []
    for c in cfg["companies"]:
        n = c["name"]
        q, basis = is_quarters(ft, n, "매출액")
        pr = px[n].dropna() if n in px else pd.Series(dtype=float)
        rows.append([n, c["group"], c.get("stage", "–") or "–", "상장폐지 " + c["delisted"] if c.get("delisted") else "상장", basis or "–",
                     f"{min(q)[:4]} {min(q)[-1]}Q ~ {max(q)[:4]} {max(q)[-1]}Q" if q else "–",
                     f"{pr.index.min()} ~ {pr.index.max()}" if len(pr) else "–"])
    items.append({"id": "coverage", "title": f"커버리지 {len(cfg['companies'])}개사", "grade": "auto", "gradeLabel": "자동 수집", "wide": True,
                  "table": {"columns": ["기업", "PER 그룹", "밸류체인 단계", "상장", "재무 기준", "DART 분기", "주가"], "rows": rows}, "empty": "",
                  "note": "기존 트래커 6개사(ODM 3·메디컬 3)에서 합치면서 20개사로 확대. 재무는 DART, 주가는 금융위 주식시세."})
    # 상대 주가 수익률: 그룹별 동일가중 (시작일=100) + KOSPI·KOSDAQ
    sl = []
    if not px.empty:
        groups = {}
        for c in cfg["companies"]:
            groups.setdefault(c["group"], []).append(c["name"])
        p = px.copy()
        p.index = pd.to_datetime(p.index)
        w = p.resample("W-FRI").last()
        w.index = [min(i, p.index.max()) for i in w.index]
        for i, (g, names) in enumerate(groups.items()):
            cols = [n for n in names if n in w]
            rel = w[cols].div(w[cols].bfill().iloc[0]) * 100  # 각 종목 첫 거래일=100 (신규 상장은 상장일=100)
            sl.append(series(f"{g} ({len(cols)}사 동일가중)", rel.mean(axis=1).rename(index=lambda d: d.strftime("%Y-%m-%d")), slot=i))
    if not ix.empty:
        ix = ix.assign(pri=ix["출처"].str.startswith("금융위").astype(int)).sort_values(["기준일", "pri"]).drop_duplicates(["기준일", "구분"], keep="last")
        q = ix.pivot_table(index="기준일", columns="구분", values="값").sort_index()
        q.index = pd.to_datetime(q.index)
        qw = q.resample("W-FRI").last()
        qw.index = [min(i, q.index.max()) for i in qw.index]
        for lab in ("KOSPI", "KOSDAQ"):
            if lab in qw:
                s = qw[lab] / qw[lab].dropna().iloc[0] * 100
                sl.append(series(lab, s.rename(index=lambda d: d.strftime("%Y-%m-%d")), slot="muted", dash=lab == "KOSDAQ"))
    ix_src = "금융위 지수시세정보(활용신청 후) / 기존 트래커 이관분(네이버 차트, 신청 전)"
    items.append(chart("rel_return", "상대 주가 수익률 (주간, 시작 = 100)", grade="auto", unit="지수", freq="W", yoy="", series_list=sl,
                       source=f"{STOCK_SRC} + {ix_src}", collectors=["stocks", "index"],
                       note="그룹마다 종목별 주가를 첫 거래일(상장일)=100으로 맞춘 뒤 단순 평균. 회색 실선 KOSPI, 점선 KOSDAQ. "
                            "기간 버튼을 바꿔도 기준일은 2021년 초(또는 상장일) 그대로."))
    # 분기 실적 비교 (메디컬 — 기존 화면의 중심)
    med = [c["name"] for c in cfg["companies"] if c["group"] == "메디컬"]
    rev_s, opm_s = [], []
    for i, n in enumerate(med):
        rev, _ = is_quarters(ft, n, "매출액")
        op, _ = is_quarters(ft, n, "영업이익")
        rev_s.append(series(n, pd.Series({q_date(k): v / 1e8 for k, v in rev.items()}), slot=i))
        opm_s.append(series(n, pd.Series({q_date(k): op[k] / rev[k] * 100 for k in rev if k in op and rev[k]}), slot=i))
    items.append(chart("med_rev", "메디컬 에스테틱 분기 매출", grade="auto", unit="억원", freq="Q", series_list=rev_s, source=DART_SRC,
                       collectors=["dart"], note="기존 트래커의 '분기 실적 추이 비교'. ODM은 리레이팅 조건 탭 1-2."))
    items.append(chart("med_opm", "메디컬 에스테틱 영업이익률", grade="auto", unit="%", freq="Q", yoy="diff", series_list=opm_s,
                       source=DART_SRC, collectors=["dart"]))
    items += find(a1["sections"], ["val_table"])
    # 컨센서스 기반 선행 PER (수동 입력 최신월)
    eps = manual("consensus_eps")
    rows = []
    if not eps.empty and not px.empty:
        eps = eps.sort_values("기준월").drop_duplicates("기업", keep="last")
        for r in eps.itertuples():
            e = pd.to_numeric(r.선행12개월EPS, errors="coerce")
            if r.기업 in px and pd.notna(e) and e > 0:
                p = px[r.기업].dropna()
                rows.append([r.기업, r.기준월, f"{e:,.0f}", f"{p.iloc[-1]:,.0f} ({p.index[-1]})", round(p.iloc[-1] / e, 1), r.출처])
    items.append({"id": "fwd_per", "title": "12개월 선행 PER (컨센서스 EPS 수동 입력 기준)", "grade": "manual", "gradeLabel": "수동 입력",
                  "table": {"columns": ["기업", "EPS 기준월", "선행 12개월 EPS(원)", "최근 종가(원)", "선행 PER(배)", "EPS 출처"], "rows": rows},
                  "empty": "" if rows else "manual/consensus_eps.csv 입력 대기",
                  "note": "기존 트래커의 WiseReport 컨센서스는 합치면서 현재값만 1회 이관(출처 칸에 표시). 이후는 월 1회 수동 입력."})
    # 기업별 최근 분기 (기업 상세)
    rows = []
    for c in cfg["companies"]:
        n = c["name"]
        rev, basis = is_quarters(ft, n, "매출액")
        op, _ = is_quarters(ft, n, "영업이익")
        if not rev:
            continue
        k = max(rev)
        p4 = f"{int(k[:4]) - 1}{k[4:]}"
        ni = ttm(net_income_q(ft, n))
        m = mc[n].dropna() if n in mc else pd.Series(dtype=float)
        per = (m.iloc[-1] / ni[max(ni)]) if len(m) and ni and ni[max(ni)] > 0 and not c.get("delisted") else None
        rows.append([n, c["group"], basis, f"{k[:4]} {k[-1]}Q", round(rev[k] / 1e8), f"{(rev[k] / rev[p4] - 1) * 100:+.1f}%" if rev.get(p4) else "–",
                     f"{op[k] / rev[k] * 100:.1f}%" if k in op and rev[k] else "–",
                     round(m.iloc[-1] / 1e12, 2) if len(m) and not c.get("delisted") else "–", round(per, 1) if per else "–"])
    items.append({"id": "company_detail", "title": "기업 상세 — 최근 분기", "grade": "auto", "gradeLabel": "자동 수집", "wide": True,
                  "table": {"columns": ["기업", "그룹", "재무 기준", "분기", "매출(억원)", "매출 YoY", "영업이익률", "시총(조원)", "후행 PER"],
                            "rows": rows}, "empty": ""})
    items += find(a3["sections"], ["fx", "fx_table"])
    return items


GUIDE = [
    ("미국 화장품 수입 중 한국 점유율·순위", "K뷰티 수출의 최대 성장 시장. 미국 화장품(HS 3304) 수입에서 한국이 프랑스를 제치고 1위를 지키는지가 "
     "구조적 성장의 핵심 증거. 월간 수치는 선적 시점에 따라 출렁이므로 12개월 합계 점유율로 추세를 본다. ODM의 인디 브랜드 수주와 직결."),
    ("일본 화장품 수입 중 한국 점유율", "성숙 시장에서의 지속력. 점유율이 정체·하락하는지 확인."),
    ("미국 HS 3002.49 수입 — 한국산", "한국 톡신의 미국 판매 대용 지표. 백신 외 면역제품이 섞여 있어 절대값보다 방향과 YoY로 해석."),
    ("관세청 수출 상대국 비중", "중국 의존도 하락과 미국·일본·동남아·중동·유럽 다변화 확인. 관세청 국가별 통계가 Comtrade보다 빠르다."),
    ("환율", "원화 약세는 달러 표시 수출의 원화 환산 매출·마진에 우호적. 엔화 약세는 일본 시장 가격경쟁력과 방한 일본인 수요에 영향."),
    ("영업이익률·PER·선행 PER", "ODM은 가동률·믹스에 따른 이익률 레버리지가, 메디컬 에스테틱은 고마진 유지와 해외 매출 비중이 밸류에이션을 좌우."),
    ("아마존 Top50·검색량·통관 수출", "소비자 단의 선행 수요(검색·랭킹)와 통관 수출(실제 선적)을 함께 보며 실적 추정의 방향을 확인."),
]


def guide() -> dict:
    return {"id": "guide", "title": "지표 가이드 — 왜 이 숫자를 보나", "grade": "auto", "gradeLabel": "자동 수집", "wide": True,
            "table": {"columns": ["지표", "읽는 법"], "rows": [list(g) for g in GUIDE]}, "empty": "",
            "note": "기존 트래커의 지표 가이드를 옮겨 합친 화면 기준으로 고쳐 씀."}


def build(cfg, a1, a2, a3) -> dict[str, dict]:
    search = find(a3["sections"], [it["id"] for sec in a3["sections"] if sec["id"] == "3-4" for it in sec["items"]])
    search += find(a2["sections"], ["wiki_kbeauty", "gtrends"])
    return {
        "d_exports": {"sections": [{"id": "d-exp", "title": "수출 (관세청)", "items": exports(cfg, a1, a2)},
                                   {"id": "d-imp", "title": "수입시장 (UN Comtrade: 미국·일본)", "items": comtrade_items()}]},
        "d_amazon": {"sections": [{"id": "d-amz", "title": "아마존 US 베스트셀러 Top50", "items": amazon(a2)}]},
        "d_search": {"sections": [{"id": "d-search", "title": "검색 트렌드·인지도", "items": search}]},
        "d_companies": {"sections": [{"id": "d-co", "title": "기업·주가", "items": companies(cfg, a1, a3)}]},
    }
