"""축 3. 개별 지표 — 발표에서 질문받은 항목을 숫자로."""
from __future__ import annotations

import pandas as pd

from .axis1 import CUSTOMS_SRC, DART_SRC, REGION_SLOT, REGIONS_8, customs_pivot, fin_table, is_quarters, region_of
from .common import q_date
from .common import card, chart, obs, series

EU7 = ["GB", "DE", "FR", "NL", "PL", "ES", "IT"]
EM4 = ["AE", "SA", "BR", "MX"]
CC_KO = {"US": "미국", "CN": "중국", "JP": "일본", "GB": "영국", "DE": "독일", "FR": "프랑스", "NL": "네덜란드", "PL": "폴란드",
         "ES": "스페인", "IT": "이탈리아", "AE": "UAE", "SA": "사우디", "BR": "브라질", "MX": "멕시코", "HK": "홍콩"}


def monthly_idx(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = [f"{m}-01" for m in df.index]
    return df


def unit_price(cfg) -> list[dict]:
    val, _ = customs_pivot("3304")
    wgt, _ = customs_pivot("3304", "수출중량")
    out = []
    if val.empty or wgt.empty:
        out.append(chart("px_country", "국가별 화장품 수출단가 (12개월 누적, 달러/kg)", grade="auto", unit="달러/kg", freq="M",
                         series_list=[], source=CUSTOMS_SRC, collectors=["customs"]))
    else:
        regmap = region_of(cfg)
        v12, w12 = val.rolling(12).sum(), wgt.rolling(12).sum()
        groups = {"미국": ["US"], "중국": ["CN"], "일본": ["JP"], "유럽": cfg["customs"]["regions"]["유럽"],
                  "중동": cfg["customs"]["regions"]["중동"]}
        s_list = []
        for i, (g, ccs) in enumerate(groups.items()):
            cols = [c for c in ccs if c in v12]
            if cols:
                s_list.append(series(g, monthly_idx((v12[cols].sum(axis=1) / w12[cols].sum(axis=1)).to_frame("x"))["x"], slot=i))
        s_list.append(series("전체", monthly_idx((v12.sum(axis=1) / w12.sum(axis=1)).to_frame("x"))["x"], slot="muted"))
        out.append(chart("px_country", "국가별 화장품 수출단가 (HS 3304, 12개월 누적, 달러/kg)", grade="auto", unit="달러/kg",
                         freq="M", series_list=s_list, source=CUSTOMS_SRC, collectors=["customs"],
                         note="단가 = 수출액 ÷ 중량. 제품 믹스(기초·색조, 용량, 포장 무게)가 바뀌어도 움직이므로 '가격 인상'과 같지 않다. "
                              "ODM 출고단가는 공개 데이터가 없다(데이터 한계 탭)."))
    items = []
    for i, (hs, nm) in enumerate([(k, v) for k, v in cfg["customs"]["hs"].items() if len(k) == 10]):
        v, _ = customs_pivot(hs)
        w, _ = customs_pivot(hs, "수출중량")
        if not v.empty and not w.empty:
            items.append(series(nm, monthly_idx((v.rolling(12).sum().sum(axis=1) / w.rolling(12).sum().sum(axis=1)).to_frame("x"))["x"], slot=i))
    out.append(chart("px_product", "품목별 수출단가 (HS 10단위, 전 국가, 12개월 누적)", grade="auto", unit="달러/kg", freq="M",
                     series_list=items, source=CUSTOMS_SRC, collectors=["customs"],
                     note="HSK 3304.99 아래 10단위: -1000 기초 / -2000 메이크업 / -3000 어린이용 / -9000 기타. "
                          "선케어만 따로 잡는 코드는 없다(아래 코드 표)."))
    return out


def hs_table(cfg) -> dict:
    rows = []
    for hs, nm in cfg["customs"]["hs"].items():
        p, _ = customs_pivot(hs)
        rows.append([hs, nm, "있음" if not p.empty else "없음(수집 전 또는 해당 코드 없음)", p.index.max() if not p.empty else ""])
    return {"id": "hs_table", "title": "추적 HS 코드와 수집 상태", "grade": "auto", "gradeLabel": "자동 수집",
            "table": {"columns": ["HS 코드", "품목", "데이터", "최근 월"], "rows": rows}, "empty": "",
            "note": "API는 요청 코드의 한 단계 아래 하위 코드별로 준다(3304 → 6자리, 3304.99 → 10자리). 상위 코드 값은 하위 코드 합. "
                    "선크림은 별도 10단위 코드가 없어 기초(-1000) 또는 기타(-9000)에 섞인다 — 선케어 수출액은 직접 알 수 없음."}


def weight_coverage(cfg) -> list[dict]:
    """수출단가(금액÷중량)를 계산할 수 있는 품목·국가 범위."""
    df = obs("customs")
    if df.empty:
        return [{"id": "wgt_cov", "title": "수출단가 계산 가능 범위", "grade": "auto", "gradeLabel": "자동 수집",
                 "table": {"columns": [], "rows": []}, "empty": "관세청 수집 전"}]
    q = cfg["customs"]["queries"]
    rows = []
    for hs, nm in cfg["customs"]["hs"].items():
        stored_w = any(hs.startswith(k) and v.get("weight") for k, v in q.items())
        v, _ = customs_pivot(hs)
        w, _ = customs_pivot(hs, "수출중량")
        if v.empty:
            rows.append([hs, nm, "–", "–", "–", "–", "데이터 없음"])
            continue
        if not stored_w or w.empty:
            rows.append([hs, nm, int((v > 0).sum().sum()), 0, "0%", "–", "중량 미저장(합계만 필요한 품목) — 단가 불가"])
            continue
        w = w.reindex_like(v).fillna(0)
        cells = int((v > 0).sum().sum())
        both = int(((v > 0) & (w > 0)).sum().sum())
        val_cov = float(v.where(w > 0, 0).sum().sum() / v.sum().sum() * 100)
        n_cc = int(((v > 0) & (w > 0)).any().sum())
        rows.append([hs, nm, cells, both, f"{val_cov:.1f}%", n_cc, "가능"])
    t1 = {"id": "wgt_cov", "title": "수출단가 계산 가능 범위 — 품목별 (2021-01~)", "grade": "auto", "gradeLabel": "자동 수집",
          "table": {"columns": ["HS", "품목", "수출액 있는 국가·월", "중량도 있는 국가·월", "수출액 기준 중량 커버리지",
                                "단가 계산 가능 국가 수", "판정"], "rows": rows}, "empty": "",
          "note": "중량이 0인 국가·월(소량 샘플 등)은 단가 계산에서 빠진다. 커버리지는 그 빠진 몫을 수출액 기준으로 본 것."}
    v, names = customs_pivot("3304")
    w, _ = customs_pivot("3304", "수출중량")
    w = w.reindex_like(v).fillna(0)
    crow = []
    for cc in cfg["customs"]["focus_countries"]:
        if cc not in v:
            crow.append([CC_KO.get(cc, cc), cc, 0, 0, "–", "–"])
            continue
        vm, wm = v[cc], w[cc]
        ok = (vm > 0) & (wm > 0)
        last = ok[ok].index.max() if ok.any() else "–"
        l12 = vm.iloc[-12:].sum() / wm.iloc[-12:].sum() if wm.iloc[-12:].sum() else None
        crow.append([CC_KO.get(cc, cc), cc, int((vm > 0).sum()), int(ok.sum()), last, round(l12, 2) if l12 else "–"])
    t2 = {"id": "wgt_cov_cc", "title": "수출단가 계산 가능 범위 — 국가별 (HS 3304)", "grade": "auto", "gradeLabel": "자동 수집",
          "table": {"columns": ["국가", "코드", "수출액 있는 달", "중량도 있는 달", "최근 계산 가능 월", "최근 12개월 단가(달러/kg)"],
                    "rows": crow}, "empty": ""}
    return [t1, t2]


def country_charts(cfg) -> list[dict]:
    val, _ = customs_pivot("3304")
    out = []
    for cid, title, ccs in (("exp_eu", "유럽 주요국 화장품 수출 (월)", EU7), ("exp_em", "중동·남미 화장품 수출 (월)", EM4)):
        if val.empty:
            out.append(chart(cid, title, grade="auto", unit="백만 달러", freq="M", series_list=[], source=CUSTOMS_SRC,
                             collectors=["customs"]))
            continue
        v = monthly_idx(val) / 1e6
        out.append(chart(cid, title, grade="auto", unit="백만 달러", freq="M",
                         series_list=[series(CC_KO[c], v[c], slot=i) for i, c in enumerate(ccs) if c in v],
                         source=CUSTOMS_SRC, collectors=["customs"],
                         note="YoY 전환 권장. 유럽 국가별 수출은 관세청 국가별 출하가 공개 자료로는 가장 가깝다. 네덜란드·벨기에는 EU 물류 허브라 재수출 비중이 클 수 있다."))
    return out


def onset_table(p: pd.DataFrame, anchor: str) -> dict:
    rows = []
    onset = {}
    for c in p.columns:
        s = p[c].dropna().rolling(8, min_periods=4).mean()
        if s.empty or s.max() <= 0:
            continue
        hit = s[s >= s.max() * 0.25]
        onset[c] = hit.index[0] if len(hit) else None
    base = onset.get(anchor)
    for c, d in onset.items():
        lag = ""
        if d is not None and base is not None:
            lag = f"{(pd.Timestamp(d) - pd.Timestamp(base)).days // 7:+d}주"
        rows.append([c, d or "–", lag, round(float(p[c].dropna().iloc[-4:].mean()), 1)])
    rows.sort(key=lambda r: r[1])
    return {"id": "booster_onset", "title": "스킨부스터 제품별 검색 상승 시점", "grade": "auto", "gradeLabel": "자동 수집",
            "table": {"columns": ["키워드", "상승 시점(8주 평균이 최고치의 25% 첫 도달)", f"{anchor} 대비", "최근 4주 평균 지수"], "rows": rows},
            "empty": "" if rows else "네이버 데이터랩 수집 전",
            "note": "상승 시점 기준(최고치의 25%)은 임의 규칙 — 후발 제품이 '성장하기까지의 시간차'를 대략 비교하는 용도. "
                    "2021년 이전에 이미 떴던 제품은 2021년 초가 상승 시점으로 찍힌다."}


NAVER_HOWTO = "수동 입력 대기 — datalab.naver.com 검색어트렌드에서 제품 키워드를 조회해 엑셀을 raw/에 넣고 커밋 (README '네이버 검색 트렌드')"


def naver_queries() -> list[dict]:
    """naver_excel 관측값 → 조회(시트) 단위 목록. 같은 조건·키워드 묶음이면 기간 끝이 가장 늦은 파일만 남긴다."""
    ex = obs("naver_excel")
    if ex.empty:
        return []
    parts = ex["구분"].str.split("|", n=1, expand=True)
    ex = ex.assign(kw=parts[0], cond=parts[1].fillna(""))
    latest: dict = {}
    for src, g in ex.groupby("출처"):
        ident = (g["cond"].iloc[0], frozenset(g["kw"]))
        rank = (g["기준일"].max(), g["수집일"].max())
        if ident not in latest or rank > latest[ident]["rank"]:
            p = g.pivot_table(index="기준일", columns="kw", values="값").sort_index()
            unit = g["단위"].iloc[0]
            latest[ident] = {"rank": rank, "src": src, "cond": ident[0], "p": p, "unit": unit,
                             "freq": "M" if "월간" in unit else "W"}
    return sorted(latest.values(), key=lambda q: q["src"])


def booster(cfg) -> list[dict]:
    kws = set(cfg["naver_manual"]["booster_products"])
    qs = naver_queries()
    main = [q for q in qs if kws & set(q["p"].columns)]
    rest = [q for q in qs if q not in main]
    out = []
    if not main:
        out.append(chart("booster", "스킨부스터 제품별 네이버 검색지수", grade="manual", unit="조회 내 최댓값=100", freq="W",
                         series_list=[], source="네이버 데이터랩 다운로드 엑셀", empty_reason=NAVER_HOWTO))
        out.append({"id": "booster_onset", "title": "스킨부스터 제품별 검색 상승 시점", "grade": "manual",
                    "gradeLabel": "수동 입력", "table": {"columns": [], "rows": []}, "empty": NAVER_HOWTO})
    for i, q in enumerate(main):
        suffix = "" if len(main) == 1 else f" ({i + 1})"
        cols = list(q["p"].columns)
        out.append(chart("booster" if i == 0 else f"booster_{i}", f"스킨부스터 제품별 네이버 검색지수{suffix}", grade="manual",
                         unit=q["unit"], freq=q["freq"], series_list=[series(c, q["p"][c], slot=j) for j, c in enumerate(cols)][:8],
                         source=q["src"],
                         note=f"조건: {q['cond'] or '전체'}. 값은 이 조회 안의 최댓값=100 — 다른 조회(다른 파일·시트)와 수치를 "
                              "직접 비교하면 안 된다. 한 조회에 키워드는 최대 5개라 제품이 더 많으면 기준 키워드(예: 리쥬란)를 "
                              "모든 조회에 넣어 두 조회를 이어 볼 것."))
        anchor = next((k for k in cfg["naver_manual"]["booster_products"] if k in cols), cols[0])
        t = onset_table(q["p"], anchor)
        t["grade"], t["gradeLabel"] = "manual", "수동 입력"
        if i:
            t["id"], t["title"] = f"booster_onset_{i}", t["title"] + suffix
        out.append(t)
    for i, q in enumerate(rest):
        cols = list(q["p"].columns)
        out.append(chart(f"naver_x_{i}", f"네이버 검색지수 — {q['cond'] or '기타 조회'}", grade="manual", unit=q["unit"],
                         freq=q["freq"], series_list=[series(c, q["p"][c], slot=j) for j, c in enumerate(cols)][:8],
                         source=q["src"], note="같은 조회 안에서만 비교 가능(조회마다 최댓값=100). 연령대 간 비교 불가."))
    return out


def pharma_demand(cfg) -> list[dict]:
    out = []
    tr = obs("tourism")
    if tr.empty:
        out.append(chart("tour_nat", "방한 외국인 (국적별, 월)", grade="auto", unit="명", freq="M", series_list=[],
                         source="출입국관광통계", collectors=["tourism"]))
    else:
        p = tr.pivot_table(index="기준일", columns="구분", values="값").sort_index()
        p.index = [f"{m}-01" for m in p.index]
        top = [c for c in p.drop(columns=["전체"], errors="ignore").iloc[-12:].sum().sort_values(ascending=False).index][:7]
        out.append(chart("tour_total", "방한 외국인 전체 (월)", grade="auto", unit="명", freq="M",
                         series_list=[series("전체", p["전체"])] if "전체" in p else [], source="한국문화관광연구원 출입국관광통계",
                         collectors=["tourism"], note="방한 외국인과 국내 리쥬란 수요의 관계: IR 확인(비공개 자료)."))
        out.append(chart("tour_nat", "방한 외국인 국적별 (최근 12개월 상위 7개국, 월)", grade="auto", unit="명", freq="M",
                         series_list=[series(c, p[c], slot=i) for i, c in enumerate(top)], source="한국문화관광연구원 출입국관광통계",
                         collectors=["tourism"]))
    v, _ = customs_pivot("9018")
    if v.empty:
        out.append(chart("exp_9018", "의료기기(HS 9018) 수출 (월)", grade="auto", unit="백만 달러", freq="M", series_list=[],
                         source=CUSTOMS_SRC, collectors=["customs"]))
    else:
        regmap = region_of(cfg)
        reg = monthly_idx(v.T.groupby(lambda c: regmap.get(c) if regmap.get(c) in REGIONS_8 else "기타").sum().T) / 1e6
        order = REGIONS_8 + ["기타"]
        out.append(chart("exp_9018", "의료기기(HS 9018) 지역별 수출 (월)", grade="proxy", unit="백만 달러", freq="M",
                         series_list=[series(n, reg[n], slot=REGION_SLOT[n]) for n in order if n in reg],
                         source=CUSTOMS_SRC, collectors=["customs"],
                         note="HS 9018은 의료기기 전체라 리쥬란(PN 주사제) 외 품목이 대부분 — 방향 확인용 대용 지표. "
                              "회사별 유럽 선적 추이는 IR 확인(비공개 자료)."))
    return out


def fx(cfg) -> list[dict]:
    df = obs("fx")
    if df.empty:
        return [chart("fx", "환율", grade="auto", unit="", freq="D", series_list=[], source="한국은행 ECOS", collectors=["fx"])]
    df["pri"] = df["출처"].str.startswith("한국은행").astype(int)
    df = df.sort_values(["기준일", "pri"]).drop_duplicates(["기준일", "구분"], keep="last")
    p = df.pivot_table(index="기준일", columns="구분", values="값").sort_index()
    src = "한국은행 ECOS 매매기준율" if df["pri"].iloc[-1] else "ECB 기준환율(frankfurter) — ECOS 키 등록 전 폴백"
    idx = p / p.bfill().iloc[0] * 100
    names = list(cfg["fx"])
    rows = []
    for c in names:
        if c not in p:
            continue
        s = p[c].dropna()
        last_d = s.index[-1]
        yr = s[s.index <= str(pd.Timestamp(last_d) - pd.DateOffset(years=1))[:10]]
        chg = (s.iloc[-1] / yr.iloc[-1] - 1) * 100 if len(yr) else None
        rows.append([c, last_d, round(float(s.iloc[-1]), 2), f"{chg:+.1f}%" if chg is not None else "–"])
    return [
        chart("fx", "원화 환율 지수 (2021년 첫 값 = 100, 일별)", grade="auto", unit="지수", freq="D",
              series_list=[series(c, idx[c], slot=i) for i, c in enumerate(names) if c in idx], source=src, collectors=["fx"],
              note="통화마다 단위가 달라 첫 값=100으로 맞춰 한 축에 표시. 위로 갈수록 원화 약세(수출기업에 유리)."),
        {"id": "fx_table", "title": "최근 환율과 1년 변화", "grade": "auto", "gradeLabel": "자동 수집",
         "table": {"columns": ["통화", "기준일", "환율(원)", "1년 전 대비"], "rows": rows}, "empty": ""},
        card("fx_exposure", "환 노출 대비: 파마리서치 대 한국콜마", "auto",
             "같은 환율 변화가 두 회사에 주는 영향은 매출 통화 구조에 따라 크게 다르다. 회사별 환 노출 구조는 공시 구조화 데이터에 없다.",
             memo="파마리서치·한국콜마의 매출 통화·결제 구조: IR 확인(비공개 자료).", source="IR 확인(비공개 자료)"),
    ]


def conditional(cfg) -> list[dict]:
    out = []
    v, _ = customs_pivot("300510")
    out.append(chart("exp_300510", "여드름 패치 대용: 접착성 드레싱(HS 3005.10) 수출 (월)", grade="proxy", unit="백만 달러", freq="M",
                     series_list=[series("전체", (monthly_idx(v).sum(axis=1) / 1e6))] if not v.empty else [],
                     source=CUSTOMS_SRC, collectors=["customs"],
                     note="하이드로콜로이드 여드름 패치는 보통 3005.10으로 분류되지만 일반 반창고·드레싱이 섞여 있다. "
                          "아마존 Top50의 패치 제품(Mighty Patch 등, 한국 생산)과 함께 볼 것."))
    wk = obs("wiki")
    ms = wk[wk["구분"] == "Medical spa"] if not wk.empty else wk
    s = pd.Series(dtype=float)
    if not ms.empty:
        m = ms.set_index(pd.to_datetime(ms["기준일"]))["값"].resample("MS").sum()
        s = m.rename(index=lambda d: d.strftime("%Y-%m-%d")).iloc[:-1]
    out.append(chart("medspa_proxy", "미국 메드스파 관심도 대용: 'Medical spa' 위키 조회수 (월)", grade="proxy", unit="회", freq="M",
                     series_list=[series("Medical spa", s)] if len(s) else [], source="Wikimedia Pageviews API", collectors=["wiki"],
                     note="메드스파 '개수'는 공식 통계가 없다(업종 코드 없음). 이것은 관심도일 뿐 점포 수·매출이 아니다."))
    out.append(card("sigungu", "생산지(시군구) 기준 수출", "none",
                    "관세청 공개 통계는 시도 단위까지이고, 시군구·품목 교차 무료 API는 확인하지 못했다. 불가.",
                    memo="대안: 관세청 수출입무역통계 웹(unipass)의 '지역별 수출입'에서 시도 단위(예: 충북 오송·경기) 화장품 수출을 "
                         "수동으로 받아 manual/에 추가."))
    out.append(card("medspa_why", "미국 메드스파 증가 원인", "none",
                    "원인(규제 완화, 간호사 시술 허용 범위, GLP-1 이후 미용 수요 등)은 숫자로 분해되지 않는다.",
                    memo="대안: AmSpa(미국 메드스파 협회) 연간 보고서 수치를 manual/에 수동 입력, 미국 인구조사국 County Business Patterns의 "
                         "관련 업종(예: NAICS 812199)은 2년 지연·업종 혼재라 참고만."))
    return out


def margins(cfg) -> list[dict]:
    ft = fin_table()
    med = [c["name"] for c in cfg["companies"] if c["group"] == "메디컬"]
    brand = [c["name"] for c in cfg["companies"] if c["group"] == "브랜드"]
    gp_s, sga_s = [], []
    for i, comp in enumerate(med):
        rev, _ = is_quarters(ft, comp, "매출액")
        gp, _ = is_quarters(ft, comp, "매출총이익")
        gp_s.append(series(comp, pd.Series({q_date(k): gp[k] / rev[k] * 100 for k in rev if k in gp and rev[k]}), slot=i))
    for i, comp in enumerate(brand):
        rev, _ = is_quarters(ft, comp, "매출액")
        sga, _ = is_quarters(ft, comp, "판관비")
        sga_s.append(series(comp, pd.Series({q_date(k): sga[k] / rev[k] * 100 for k in rev if k in sga and rev[k]}), slot=i))
    return [
        chart("med_gpm", "메디컬 에스테틱 매출총이익률 (연결, 분기)", grade="auto", unit="%", freq="Q", yoy="diff", series_list=gp_s,
              source=DART_SRC, collectors=["dart"],
              note="발표 질문 8(화장품 비중 확대에 따른 마진 희석). 사업부별 믹스와 회사의 GP 가이던스는 IR 확인(비공개 자료)."),
        chart("brand_sga", "브랜드사 판관비율 (연결, 분기)", grade="auto", unit="%", freq="Q", yoy="diff", series_list=sga_s,
              source=DART_SRC, collectors=["dart"],
              note="발표 질문 10(해외 채널 확대로 판관비가 늘면 ODM에 단가 인하를 요구하나). 판관비율 상승은 확인 가능하지만 "
                   "'단가 인하 요구'로 이어지는지는 숫자로 알 수 없다 — ODM 가격 정책은 IR 확인(비공개 자료)."),
    ]


def build(cfg, a2_sections) -> dict:
    sun = []
    for sec in a2_sections:
        for it in sec["items"]:
            if it.get("id") == "amz_선케어":
                sun.append({**it, "id": "sun_amz"})
    sun.append(card("sun_note", "선케어 수출액", "none",
                    "선크림만 따로 잡는 HS 코드가 없어 선케어 수출액은 알 수 없다. 아마존 선케어 Top50 K 비중이 가장 가까운 공개 지표.",
                    memo="새 UV 필터 승인 이후 ODM 수주 영향: IR 확인(비공개 자료)."))
    sections = [
        {"id": "3-1", "title": "3-1. 국가별·품목별 수출단가", "items": unit_price(cfg) + weight_coverage(cfg) + [hs_table(cfg)]},
        {"id": "3-2", "title": "3-2. 유럽·신흥 지역 국가별 수출", "items": country_charts(cfg)},
        {"id": "3-3", "title": "3-3. 선케어", "items": sun},
        {"id": "3-4", "title": "3-4. 스킨부스터 검색량", "items": booster(cfg)},
        {"id": "3-5", "title": "3-5. 파마리서치 수요", "items": pharma_demand(cfg)},
        {"id": "3-6", "title": "3-6. 환율", "items": fx(cfg)},
        {"id": "3-7", "title": "3-7. 조건부 항목", "items": conditional(cfg)},
        {"id": "3-8", "title": "3-8. 마진 구조 (발표 질문 8·10)", "items": margins(cfg)},
    ]
    return {"sections": sections}
