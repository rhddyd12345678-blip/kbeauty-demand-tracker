"""개요(체크리스트·플라이휠 고리), 데이터 한계, 부록(발표 질문 17개 ↔ 지표)."""
from __future__ import annotations

import pandas as pd

from lib.core import DATA

from .common import GRADES, direction, manual, obs


def fmt(v, unit="", d=1):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    return f"{v:,.{d}f}{unit}"


def row(cond, metric, tab, target, vals, lag, grade, read, *, unit="", good_up=True, d=1, tol=0.0, latest=None):
    dirn, delta = direction(vals, lag=lag, tol=tol)
    good = None
    if dirn in ("상승", "하락"):
        good = (dirn == "상승") == good_up
    return {"cond": cond, "metric": metric, "tab": tab, "target": target,
            "latest": latest if latest is not None else (fmt(vals[-1], unit, d) if vals else "수집 전"),
            "dir": dirn if vals else "", "good": good, "read": read, "grade": grade, "gradeLabel": GRADES[grade]}


def q4q3_row(ratio: dict) -> dict:
    """회사별 {연도: 4Q÷3Q} → ODM 평균의 연도별 추이 문자열."""
    years = sorted({y for d in ratio.values() for y in d})
    avg = {y: sum(d[y] for d in ratio.values() if y in d) / sum(1 for d in ratio.values() if y in d) for y in years}
    vals = [avg[y] for y in years]
    r = row("이익의 질", "계절성: 4Q÷3Q 매출 비율 (ODM 4사 평균, 연도별)", "rerating", "odm_q4q3", vals, 1, "auto",
            "1에 가까워질수록 비수기 감소가 약해져 이익 변동성 축소. 방향은 직전 연도 대비", unit="배", d=2,
            latest=" → ".join(f"{y[2:]}년 {avg[y]:.2f}" for y in years) if years else "수집 전")
    return r


def demand_tiles(k2: dict) -> list[dict]:
    """개요 상단 전방수요 요약 4개 (기존 트래커 첫 화면의 요약 수치 자리)."""
    tiles = []
    tot = k2.get("exp_total", pd.Series(dtype=float))
    if len(tot) >= 15:
        t = tot.copy()
        t.index = pd.PeriodIndex(t.index, freq="M")
        cur = [t.index.max() - i for i in range(3)]
        prev = [p - 12 for p in cur]
        if all(p in t.index for p in cur + prev):
            tiles.append({"label": f"화장품 수출 YoY (3개월 합 {cur[-1].strftime('%Y-%m')}~{cur[0].strftime('%Y-%m')})",
                          "value": f"{(t[cur].sum() / t[prev].sum() - 1) * 100:+.1f}%", "tab": "d_exports", "target": "exp_total_m"})
    ct = obs("comtrade")
    if not ct.empty:
        parts = ct["구분"].str.split("|", expand=True)
        us = ct[(parts[0] == "미국") & (parts[1] == "3304")].assign(pc=parts[2])
        p = us.pivot_table(index="기준일", columns="pc", values="값", aggfunc="sum").sort_index().fillna(0)
        if "410" in p and len(p) >= 12:
            tot12 = (p["0"] if "0" in p else p.sum(axis=1)).iloc[-12:].sum()
            part = p.drop(columns=["0"], errors="ignore").iloc[-12:].sum()
            rank = int(part.rank(ascending=False)["410"])
            tiles.append({"label": f"미국 화장품 수입 중 한국 (12개월, ~{p.index[-1]})", "value": f"{part['410'] / tot12 * 100:.1f}% · {rank}위",
                          "tab": "d_exports", "target": "ct_us_share"})
    amz = k2.get("amz_스킨케어", [])
    if amz:
        tiles.append({"label": "아마존 스킨케어 Top50 한국 생산 비중 (최신)", "value": f"{amz[-1]:.0f}%", "tab": "d_amazon", "target": "amz_스킨케어"})
    f = DATA / "news_items.csv"
    if f.exists():
        n = pd.read_csv(f, dtype=str, encoding="utf-8-sig")
        last = pd.to_datetime(n["날짜"], errors="coerce")
        cut = last.max() - pd.Timedelta(days=6)
        tiles.append({"label": f"최근 7일 뉴스 (~{last.max():%m-%d})", "value": f"{int((last >= cut).sum())}건", "tab": "news", "target": ""})
    return tiles


def overview(k1: dict, k2: dict) -> dict:
    g, eq, val = k1["geo"], k1["eq"], k1["val"]

    def avg_series(d: dict) -> list[float]:
        """회사별 리스트 → 같은 위치끼리 평균 (길이가 다르면 뒤에서부터 맞춤)."""
        lists = [v for v in d.values() if v]
        if not lists:
            return []
        n = min(len(v) for v in lists)
        return [sum(v[-n + i] for v in lists) / len(lists) for i in range(n)]

    odm_q = k2.get("odm_sum", {})
    keys = sorted(odm_q)
    odm_yoy = [(odm_q[k] / odm_q[f"{int(k[:4]) - 1}{k[4:]}"] - 1) * 100 for k in keys if f"{int(k[:4]) - 1}{k[4:]}" in odm_q]
    eps = manual("consensus_eps")
    checklist = [
        row("성장의 지역 분산", "수출 국가 집중도 HHI (12개월 누적)", "rerating", "geo_hhi", g.get("hhi", []), 12, "auto",
            "낮아질수록 특정 국가 의존이 줄어 할인 요인 축소", good_up=False, d=0),
        row("성장의 지역 분산", "중국 수출 비중 (12개월 누적)", "rerating", "geo_share", g.get("china", []), 12, "auto",
            "중국 비중 하락 = 과거 멀티플을 깎던 중국 리스크 감소", unit="%", good_up=False),
        row("성장의 지속성", "ODM 단계 합산 매출 YoY (분기)", "flywheel", "vc_yoy", odm_yoy, 4, "auto",
            "성장률 자체보다 몇 분기 연속 유지되는지가 핵심", unit="%"),
        row("이익의 질", "ODM 4사 평균 영업이익률", "rerating", "odm_opm", avg_series(eq.get("opm", {})), 4, "auto",
            "Q 증가가 이익률로 이어지는지(영업 레버리지)", unit="%"),
        row("이익의 질", "ODM 4사 평균 인당 매출 (별도, TTM)", "rerating", "odm_pc", avg_series(eq.get("pc", {})), 2, "auto",
            "인력보다 매출이 빨리 늘면 영업 레버리지", unit="억원", d=2),
        q4q3_row(eq.get("ratio", {})),
        row("시장 반영 정도", "ODM PER ÷ 브랜드 PER (주간)", "rerating", "per_gap", val.get("gap", []), 52, "auto",
            "상승 = ODM 할인 축소(상대 리레이팅이 진행 중)", unit="배", d=2, tol=0.02),
        {"cond": "이익 상향 대 멀티플", "metric": "주가 변화 분해 (EPS 기여 대 PER 기여)", "tab": "rerating", "target": "decomp",
         "latest": f"{len(eps)}행 입력" if not eps.empty else "수동 입력 대기", "dir": "", "good": None,
         "read": "EPS 기여가 크면 이익 상향, PER 기여가 크면 멀티플 확장", "grade": "manual", "gradeLabel": GRADES["manual"]},
        {"cond": "주문 가시성", "metric": "선주문 기간", "tab": "rerating", "target": "visibility", "latest": "IR 확인(비공개 자료)",
         "dir": "", "good": None, "read": "공개 데이터 없음 — 재고 회전·계약부채는 약한 대용", "grade": "none",
         "gradeLabel": GRADES["none"]},
    ]
    chain = dict(k2.get("chain") or {})
    if chain:
        chain["link_tab"] = "flywheel"  # 개요에서 상자를 누르면 플라이휠 탭의 해당 단계 상세로
    return {"checklist": checklist, "chain": chain}


LIMITS = [
    ("ODM 출고단가", "ODM이 브랜드에 받는 단가는 계약 정보라 공시·통계 어디에도 없다. 수출단가(달러/kg)는 소비자·브랜드 가격, 제품 믹스, 용량이 섞인 값.",
     "콜마 IR: '코스트플러스 모델, 의도적 단가 인상은 단 한 번도 없다'. 매출총이익률 추이로 간접 확인, 나머지는 IR·전화"),
    ("주문 가시성(선주문 기간)", "수주잔고를 공시하지 않는다.", "IR 확인(비공개 자료). 재고 회전·계약부채는 약한 대용"),
    ("채널 수수료", "올리브영·아마존·세포라 수수료율은 비공개다.",
     "IR 확인(비공개 자료)"),
    ("브랜드사의 단가 인하 요구", "협상 내용은 관측 불가. 판관비율 상승은 볼 수 있지만 그것이 ODM 단가 압박으로 이어졌는지는 모른다.",
     "3-8 브랜드 판관비율 + ODM 매출총이익률을 함께 보고 IR 확인(비공개 자료)"),
    ("수출단가 상승 원인", "달러/kg 상승이 가격 인상인지, 고가 제품 비중 증가인지, 가벼운 포장인지 분해할 데이터가 없다.",
     "HS 10단위(기초·메이크업) 단가를 나눠 믹스 효과만 일부 확인"),
    ("메드스파 증가 원인", "미국 메드스파는 업종 코드가 없어 점포 수 공식 통계조차 없다. 원인은 규제·인력·수요 요인이 섞여 숫자로 분해 불가.",
     "AmSpa 보고서 수동 입력, 'Medical spa' 위키 조회수(관심도 대용)"),
    ("관세청 수출 ≠ 현지 실판매", "관세청 수출은 한국에서 배가 떠난 시점의 출하 기준이다. 유럽은 선적 후 도착까지 수개월이 걸리고 재고로 쌓일 수 있다. "
     "네덜란드·벨기에처럼 물류 허브를 거치면 최종 소비국도 다르다.", "플라이휠 '재고 쌓기 대 실수요' 구간 표시, 아마존 순위와 함께 읽기"),
    ("선케어 수출액", "선크림 전용 HS 10단위 코드가 없어 기초·기타에 섞인다.", "아마존 선케어 Top50 K 비중(2026-09부터 직접 수집)"),
    ("미국 온라인·오프라인 비중", "발표 자료는 '오프라인 70%·온라인 30%'인데, 이와 다른 수치도 있어(IR 확인(비공개 자료)) 출처끼리 맞지 않는다. "
     "아마존 Top50은 온라인 일부만 보여준다.", "비중 수치는 출처를 맞춘 뒤 사용. 이 사이트는 방향(순위·비중 변화)만 추적"),
    ("스킨부스터 제품별 매출·가격", "비상장·병원 채널 매출은 공개되지 않는다. 검색량은 관심도이지 시술 건수가 아니다.",
     "네이버 검색지수, 파마리서치 공시 매출"),
    ("인디 브랜드 실적", "대부분 비상장. 구다이글로벌 등은 IPO 전까지 공시 없음.", "아마존 순위, 뉴스, ODM 매출"),
    ("컨센서스 EPS·글로벌 피어", "무료 공식 API가 없다(Stooq는 JS 검증 페이지, Yahoo는 429 — 2026-09 확인).", "manual/ CSV에 월 1회 수동 입력"),
    ("네이버 검색 트렌드 자동 수집", "네이버 개발자센터 신규 신청이 2026-07-31 종료돼 데이터랩 API 키를 받을 수 없다. "
     "다운로드 값도 조회마다 '최댓값=100'이라 조회끼리 직접 비교할 수 없다.", "월 1회 데이터랩 엑셀을 raw/에 넣기(README), 조회마다 공통 키워드 포함"),
    ("해외 인지도(Google Trends)", "공식 무료 API가 없고 비공식 경로는 클라우드 서버에서 자주 막힌다.", "위키 조회수(대용) + Trends 월 1회 수동 입력"),
    ("아마존 과거 공백", "엑셀 스냅샷은 2022-03~2026-02(2024년은 2개뿐)이고, 직접 수집은 2026-09부터라 그 사이가 비어 있다. "
     "선케어·뷰티 전체는 과거 값이 없다.", "시차 분석 옆 표본 수와 '참고용' 경고 확인"),
]


def limits() -> dict:
    return {"intro": "여기 적힌 항목은 이 사이트가 숫자로 답하지 않는다. 대용 지표가 있으면 오른쪽 열에 적었고, 나머지는 IR·전화 확인 영역이다.",
            "items": [{"item": a, "why": b, "alt": c} for a, b, c in LIMITS]}


def L(label, tab, target):
    return {"label": label, "tab": tab, "target": target}


APPENDIX = [
    (1, "미국 오프라인 70%·온라인 30%인데, 온라인 판매가 오프라인으로 전환된다는 근거가 있나",
     [L("오프라인 진출: 입점 기사·이벤트 로그", "flywheel", "store_news"), L("아마존 K 비중", "flywheel", "amz_스킨케어")],
     "부분 — 입점 사례는 로그로, 오프라인 매출은 데이터 없음. 온·오프 비중은 출처끼리 다름(데이터 한계)"),
    (2, "ECM 스킨부스터 10개가 모두 같은 성격인가, 그러면 마진 압박이 없나",
     [L("스킨부스터 검색지수·상승 시점(수동)", "indicators", "booster"), L("메디컬 매출총이익률", "indicators", "med_gpm")],
     "부분 — 관심도와 공시 마진은 숫자로, 제품별 성분·가격은 IR·전화"),
    (3, "화장품 매출 증가가 여드름 패치 매출 감소로 이어지지 않나",
     [L("접착성 드레싱 수출(대용)", "indicators", "exp_300510"), L("아마존 스킨케어 Top50", "flywheel", "amz_스킨케어")],
     "부분 — 대용 지표만, 기업 제품별 매출은 IR 확인(비공개 자료)"),
    (4, "미국향 단가가 낮은데, 미국 수출이 늘면 브랜드사(바이어)가 유리해지나",
     [L("국가별 수출단가", "indicators", "px_country"), L("지역별 수출 비중", "rerating", "geo_share")],
     "부분 — 단가·의존도는 숫자로, 협상력은 IR·전화"),
    (5, "ODM 4사 CAPA·가동률, 증설 후에도 가격협상력이 유지되나",
     [L("CAPA·가동률(수동 입력)", "rerating", "manual_CAPA"), L("ODM 영업이익률", "rerating", "odm_opm")],
     "수동 입력 시 가능 — 사업보고서 값 입력 필요. 협상력은 이익률로 간접 확인"),
    (6, "인디 브랜드가 늘면 Q만 늘고 영업 레버리지는 제한되지 않나",
     [L("인당 매출", "rerating", "odm_pc"), L("매출 대 인력 증가율", "rerating", "lev_한국콜마"), L("영업이익률", "rerating", "odm_opm")],
     "가능 — 인력 대비 매출·이익률로 레버리지 여부 확인 (SKU 수는 비공개)"),
    (7, "미국 아마존 선케어 Top50에 한국 브랜드가 거의 없는 이유",
     [L("아마존 선케어 K 비중(UV 필터 승인선)", "indicators", "sun_amz")],
     "가능(2026-09부터) — 승인 전후 비교는 몇 달 쌓여야 의미. 규제 배경은 IR 확인(비공개 자료)"),
    (8, "파마리서치가 'one of them'이 되는 것 아닌가, 화장품 비중 확대로 마진이 희석되지 않나",
     [L("스킨부스터 검색지수", "indicators", "booster"), L("메디컬 매출총이익률", "indicators", "med_gpm"), L("그룹별 PER", "rerating", "per_group")],
     "부분 — 관심도 점유와 공시 GP는 숫자로, 사업부 믹스는 IR 확인(비공개 자료)"),
    (9, "ODM과 메디컬 에스테틱을 왜 같이 분석했나", [L("그룹별 PER", "rerating", "per_group")],
     "논리 문제 — 박리다매(Q) 대 고마진(P) 대비. 데이터는 두 그룹 밸류에이션 비교로 보조"),
    (10, "브랜드사 판관비가 늘면 ODM 발주를 줄이거나 단가 인하를 요구하지 않나",
     [L("브랜드사 판관비율", "indicators", "brand_sga"), L("밸류체인 단계별 매출 YoY", "flywheel", "vc_yoy")],
     "부분 — 판관비율과 발주(ODM 매출)는 숫자로, 단가 인하 요구는 불가(데이터 한계)"),
    (11, "브랜드사가 환율 변동을 방어하는 방법은",
     [L("환율 지수", "indicators", "fx"), L("환 노출 대비(파마 대 콜마)", "indicators", "fx_exposure")],
     "부분 — 환율만 숫자로. 환 노출 구조·헤지 수단은 IR 확인(비공개 자료)·사업보고서 주석"),
    (12, "ODM과 메디컬 에스테틱 매출을 결정하는 변수(P·Q)는",
     [L("수출단가(P 대용)", "indicators", "px_product"), L("지역별 수출액(Q)", "flywheel", "exp_region"), L("방한 외국인", "indicators", "tour_total")],
     "부분 — P는 대용(달러/kg), 메디컬 P 인상 여부는 IR 확인(비공개 자료)"),
    (13, "여드름 패치는 왜 한국에서 경쟁력이 높은가", [L("접착성 드레싱 수출(대용)", "indicators", "exp_300510")],
     "불가(원인) — 기술·생산 이유는 티앤엘 등 IR·전화"),
    (14, "리쥬란 피어를 어떻게 잡았나, 후발 제품이 크기까지 시간차는",
     [L("제품별 검색 상승 시점", "indicators", "booster_onset"), L("의료기기 수출(HS 9018)", "indicators", "exp_9018")],
     "부분 — 검색 관심 기준 시간차. 유럽 재주문은 IR 확인(비공개 자료)"),
    (15, "기초화장품 수출단가가 오른 구조는", [L("품목별 수출단가", "indicators", "px_product")],
     "부분 — 단가 추이는 숫자로, 원인 분해는 불가(데이터 한계)"),
    (16, "왜 앞으로도 소비재·K뷰티가 성장한다고 보나",
     [L("밸류체인 5단계", "flywheel", "vc_diagram"), L("인접 단계 간 시차", "flywheel", "vc_lags"), L("지역 분산", "rerating", "geo_hhi")],
     "부분 — 플라이휠이 반복되는지 추적하는 것이 이 사이트의 목적. 결론은 표본이 쌓인 뒤"),
    (17, "미국 Med Spa 수가 왜 늘고 있나", [L("메드스파 관심도 대용", "indicators", "medspa_proxy")],
     "불가(원인) — 공식 통계 없음, 대용 지표만"),
]


def appendix() -> dict:
    return {"intro": "260929 발표 질문 17개와 이 사이트의 지표를 연결했다. 링크를 누르면 해당 그래프로 이동.",
            "rows": [{"no": n, "q": q, "links": links, "level": lv} for n, q, links, lv in APPENDIX]}
