"""쉬운 화면(홈·질문 페이지)용 카드. 판정 기준·문장 템플릿은 config/story.yml, 용어는 config/glossary.csv.

원칙
- 문장 속 숫자는 카드에 같이 싣는 데이터(spark·rows)에서만 계산한다. 빌드 끝에 verify()가 같은 데이터로 다시 계산해
  문장 숫자와 맞는지 검사하고, 틀리면 빌드를 실패시킨다.
- 데이터가 없으면 추측하지 않고 "아직 데이터가 없어요(이유)".
- 인과를 단정하지 않는다("~때문" 대신 "~와 같은 시기").
"""
from __future__ import annotations

import csv
import math
import re

import pandas as pd
import yaml

from lib.core import ROOT

from .axis1 import customs_pivot, fin_table, is_quarters, region_of, standalone_quarters, ttm, valuation
from .axis2 import STAGES, amazon_share, amazon_table, company_quarters, prev_q
from .common import obs

S = yaml.safe_load((ROOT / "config" / "story.yml").read_text(encoding="utf-8"))
S["indicators"].update(S.get("more_indicators") or {})
LV = S["levels"]


# ── 판정 ─────────────────────────────────────────────────────────
def judge(ind: str, change: float | None) -> str:
    """변화량 → good/weak/flat/bad/unknown (story.yml 기준)."""
    if change is None or (isinstance(change, float) and math.isnan(change)):
        return "unknown"
    spec = S["indicators"][ind]
    band = float(spec["band"])
    signed = change if spec["good"] == "up" else -change
    if abs(change) <= band:
        return "flat"
    if signed < 0:
        return "bad"
    return "good" if signed >= 2 * band else "weak"


def aggregate(levels: list[str]) -> str:
    known = [x for x in levels if x != "unknown"]
    if not known:
        return "unknown"
    n, m = len(known), S["aggregate_majority"]
    good, weak, bad = known.count("good"), known.count("weak"), known.count("bad")
    if good / n > m:
        return "good"
    if (good + weak) / n > m:
        return "weak"
    if bad / n > m:
        return "bad"
    return "flat"


def status(level: str) -> dict:
    return {"level": level, **LV[level]}


def word(pct: float) -> str:
    for w in S["change_words"]:
        if pct >= w["min"]:
            return w["text"]
    return ""


def pct(x: float, signed: bool = True) -> str:
    return f"{x:+.1f}%".replace("+", "+" if signed else "") if signed else f"{x:.1f}%"


def usd(x: float) -> str:
    """달러 → '11억 달러'처럼. 1억 달러 미만은 '5,300만 달러'."""
    if abs(x) >= 1e8:
        return f"{x / 1e8:,.1f}억 달러"
    return f"{x / 1e4:,.0f}만 달러"


def ieyo(word_: str) -> str:
    """받침이 있으면 '이에요', 없으면 '예요'. 숫자·기호로 끝나면 읽는 소리 기준(일·월·년은 받침 있음)."""
    ch = word_.rstrip(")")[-1:] if word_ else ""
    if "가" <= ch <= "힣":
        return word_ + ("이에요" if (ord(ch) - 0xAC00) % 28 else "예요")
    return word_ + ("이에요" if ch in "013678LMNlmn" else "예요")  # 영·일·삼·육·칠·팔, 엘·엠·엔 받침


def no_data(reason: str) -> str:
    return S["templates"]["no_data"].format(reason=reason)


def fill(tpl: str, **kw) -> str:
    return S["templates"][tpl].format(**kw)


# ── 공통 데이터 ───────────────────────────────────────────────────
def monthly_total(hs: str) -> pd.Series:
    p, _ = customs_pivot(hs)
    if p.empty:
        return pd.Series(dtype=float)
    s = p.sum(axis=1)
    s.index = pd.PeriodIndex(s.index, freq="M")
    return s.sort_index()


def yoy3(s: pd.Series) -> tuple[float | None, list, list]:
    """최근 3개월 합의 YoY(%)와 (현재 3개월, 1년 전 3개월)."""
    if len(s) < 15:
        return None, [], []
    last = s.index.max()
    cur = [last - i for i in range(3)]
    prev = [p - 12 for p in cur]
    if not all(p in s.index for p in cur + prev) or not s[prev].sum():
        return None, [], []
    return (s[cur].sum() / s[prev].sum() - 1) * 100, cur, prev


def spark_monthly(s: pd.Series, years: int = 3) -> list:
    """스파크라인: 3개월 평균, 최근 N년. [[YYYY-MM-01, 값], …]"""
    if s.empty:
        return []
    m = s.rolling(3).mean().dropna()
    m = m[m.index >= m.index.max() - 12 * years]
    return [[f"{p.year}-{p.month:02d}-01", round(float(v), 2)] for p, v in m.items()]


def ser(pairs: dict) -> list:
    return [[k, round(float(v), 4)] for k, v in sorted(pairs.items()) if v is not None and not math.isnan(v)]


def q_first_day(q: str) -> str:
    return f"{q[:4]}-{(int(q[-1]) - 1) * 3 + 1:02d}-01"


# ── 지표 계산 (홈·페이지가 같은 함수를 쓴다) ────────────────────────
def m_exports() -> dict:
    s = monthly_total("3304")
    y, cur, prev = yoy3(s)
    if y is None:
        return {"ok": False, "reason": "관세청 수출 수집 전"}
    return {"ok": True, "yoy": y, "cur_sum": float(s[cur].sum()), "prev_sum": float(s[prev].sum()),
            "cur": [str(p) for p in sorted(cur)], "last": str(s.index.max()), "raw": s, "spark": spark_monthly(s)}


def m_geo(cfg) -> dict:
    p, _ = customs_pivot("3304")
    if p.empty:
        return {"ok": False, "reason": "관세청 수출 수집 전"}
    r12 = p.rolling(12).sum().dropna(how="all")
    share = r12.div(r12.sum(axis=1), axis=0) * 100
    hhi = (share ** 2).sum(axis=1)
    reg = share.T.groupby(lambda c: region_of(cfg).get(c, "기타")).sum().T
    if len(hhi) < 13:
        return {"ok": False, "reason": "12개월 누적이 1년 치 쌓이기 전"}
    return {"ok": True, "hhi": float(hhi.iloc[-1]), "hhi_prev": float(hhi.iloc[-13]), "month": hhi.index[-1],
            "month_prev": hhi.index[-13], "china": float(reg["중국"].iloc[-1]), "china_prev": float(reg["중국"].iloc[-13]),
            "reg": reg, "hhi_s": hhi}


def m_amazon() -> dict:
    tab, _ = amazon_table()
    sh = amazon_share(tab)
    s = sh[sh["cat"] == "스킨케어"].set_index("date").sort_index() if not sh.empty else pd.DataFrame()
    if s.empty:
        return {"ok": False, "reason": "아마존 수집 전"}
    last = s.index[-1]
    target = pd.Timestamp(last) - pd.DateOffset(years=1)
    past = s[pd.to_datetime(s.index) <= target + pd.Timedelta(days=45)]
    past = past[pd.to_datetime(past.index) >= target - pd.Timedelta(days=45)]
    if past.empty:
        return {"ok": False, "reason": "1년 전 근처(±45일) 스냅샷 없음"}
    prev = past.index[(abs(pd.to_datetime(past.index) - target)).argmin()]
    un = tab[(tab["cat"] == "스킨케어") & (tab["date"] == last) & (~tab["mapped"])]
    return {"ok": True, "last": last, "prev": prev, "kb": int(s.loc[last, "kb"]), "kb_prev": int(s.loc[prev, "kb"]),
            "kb_pct": float(s.loc[last, "kb_pct"]), "kb_pct_prev": float(s.loc[prev, "kb_pct"]),
            "unmapped": [(int(r["rank"]), r["asin"], r["title"][:60]) for _, r in un.iterrows()],
            "spark": [[d, int(v)] for d, v in s["kb"].items()]}


def m_stage_opm(cfg) -> dict:
    """단계별 영업이익률 TTM (매출 가중), 최근 분기와 1년 전."""
    comps = company_quarters(cfg, fin_table())
    out = {}
    for st in STAGES:
        R, O = {}, {}
        for v in comps.values():
            if v["stage"] != st:
                continue
            tr, to = ttm(v["rev"]), ttm(v["op"])
            for k in tr:
                if k in to:
                    R[k] = R.get(k, 0) + tr[k]
                    O[k] = O.get(k, 0) + to[k]
        opm = {k: O[k] / R[k] * 100 for k in R if R[k]}
        if opm:
            k = max(opm)
            out[st] = {"q": k, "now": opm[k], "prev": opm.get(prev_q(k)), "series": opm}
    return out


def m_odm(cfg) -> dict:
    ft = fin_table()
    odm = [c["name"] for c in cfg["companies"] if c["group"] == "ODM"]
    R, O, RQ = {}, {}, {}
    for n in odm:
        q, _ = is_quarters(ft, n, "매출액")
        o, _ = is_quarters(ft, n, "영업이익")
        for k, v in q.items():
            RQ[k] = RQ.get(k, 0) + v
        tr, to = ttm(q), ttm(o)
        for k in tr:
            if k in to:
                R[k] = R.get(k, 0) + tr[k]
                O[k] = O.get(k, 0) + to[k]
    yoy = {k: (RQ[k] / RQ[prev_q(k)] - 1) * 100 for k in RQ if prev_q(k) in RQ and RQ[prev_q(k)]}
    opm = {k: O[k] / R[k] * 100 for k in R if R[k]}
    # 인당 매출: 같은 분기에 직원 수가 있는 회사만 합산 (분모·분자 같은 회사 집합)
    per = {}
    rows = []
    for n in odm:
        e = ft.get((n, "별도", "직원수"), {})
        ro = ttm(standalone_quarters(ft.get((n, "별도", "매출액"), {})))
        for k in e:
            if k in ro:
                a = per.setdefault(k, [0.0, 0.0, set()])
                a[0] += ro[k]
                a[1] += e[k]
                a[2].add(n)
        rows.append((n, e, ro))
    kq = max(yoy) if yoy else None
    ko = max(opm) if opm else None
    kp = max((k for k in per if prev_q(k) in per and per[k][2] == per[prev_q(k)][2]), default=None)
    comp_rows = []
    if kp:
        for n, e, ro in rows:
            if kp in e and prev_q(kp) in e and kp in ro and prev_q(kp) in ro:
                comp_rows.append([n, int(e[kp]), int(e[prev_q(kp)]), round((e[kp] / e[prev_q(kp)] - 1) * 100, 1),
                                  round(ro[kp] / e[kp] / 1e8, 2), round(ro[prev_q(kp)] / e[prev_q(kp)] / 1e8, 2),
                                  round((ro[kp] / e[kp]) / (ro[prev_q(kp)] / e[prev_q(kp)]) * 100 - 100, 1)])
    return {"yoy": yoy, "yoy_q": kq, "opm": opm, "opm_q": ko,
            "per_emp": {k: v[0] / v[1] / 1e8 for k, v in per.items()}, "per_emp_q": kp,
            "emp_tot": {k: v[1] for k, v in per.items()}, "comp_rows": comp_rows}


def m_seasonality(cfg) -> dict:
    ft = fin_table()
    odm = [c["name"] for c in cfg["companies"] if c["group"] == "ODM"]
    out = {}
    for basis in ("연결", "별도"):
        r = {}
        for y in range(2019, 2031):
            q4 = q3 = 0.0
            for n in odm:
                q = standalone_quarters(ft.get((n, basis, "매출액"), {}))
                if f"{y}Q4" in q and f"{y}Q3" in q:
                    q4 += q[f"{y}Q4"]
                    q3 += q[f"{y}Q3"]
            if q3:
                r[str(y)] = q4 / q3
        out[basis] = r
    return out


def m_per(cfg) -> dict:
    _, kv, _ = valuation(cfg)
    per, gap = kv.get("per", {}), kv.get("gap", [])
    if len(gap) < 53 or "ODM" not in per or "브랜드" not in per:
        return {"ok": False, "reason": "주가·재무 1년 치 필요"}
    o, b = per["ODM"], per["브랜드"]
    return {"ok": True, "odm": o[-1], "odm_prev": o[-53], "brand": b[-1], "brand_prev": b[-53],
            "gap": gap[-1], "gap_prev": gap[-53]}


def per_reason(p: dict) -> str:
    """PER 비율 변화가 어느 쪽 때문인지 (로그 기여로 나눔)."""
    lo = math.log(p["odm"] / p["odm_prev"])
    lb = math.log(p["brand"] / p["brand_prev"])
    up = p["gap"] > p["gap_prev"]
    past = {True: "올랐", False: "내렸"}      # 올랐지만 / 내렸지만
    conn = {True: "올라서", False: "내려서"}   # 더 많이 올라서 / 내려서
    res = "비율이 올랐어요." if up else "비율이 내렸어요."
    if (lo > 0) == (lb > 0):  # 둘이 같은 방향
        if abs(lb) > abs(lo):
            return f"ODM PER도 {past[lo > 0]}지만 브랜드 PER이 더 많이 {conn[lb > 0]} {res}"
        return f"브랜드 PER도 {past[lb > 0]}지만 ODM PER이 더 많이 {conn[lo > 0]} {res}"
    return f"ODM PER은 {past[lo > 0]}고 브랜드 PER은 {past[lb > 0]}어서 {res}".replace("내렸어서", "내려서").replace("올랐어서", "올라서")


def m_booster() -> dict:
    s = monthly_total("901890")
    y, cur, prev = yoy3(s)
    if y is None:
        return {"ok": False, "reason": "9018.90 수출 수집 전"}
    return {"ok": True, "yoy": y, "cur_sum": float(s[cur].sum()), "raw": s, "spark": spark_monthly(s)}


# ── 주가 조건표 ───────────────────────────────────────────────────
def stock_conditions(cfg) -> list[dict]:
    g, o, se, p = m_geo(cfg), m_odm(cfg), m_seasonality(cfg), m_per(cfg)
    rows = []

    def row(cond, metric, now, prev, change, ind, why, note=""):
        lvl = judge(ind, change) if change is not None else "unknown"
        rows.append({"cond": cond, "metric": metric, "now": now, "prev": prev, "change": change, "ind": ind,
                     "band": S["indicators"][ind]["band"] if ind else None, "level": lvl, "why": why, "note": note})
    if g["ok"]:
        row("지역 분산", "수출 HHI (12개월 누적)", f"{g['hhi']:.0f}", f"{g['hhi_prev']:.0f}", g["hhi"] - g["hhi_prev"], "hhi",
            "한 나라에 덜 쏠릴수록 실적이 덜 흔들려 시장이 할인을 덜 해요.",
            f"중국 비중 {g['china_prev']:.1f}% → {g['china']:.1f}%")
    k = o["yoy_q"]
    if k and prev_q(k) in o["yoy"]:
        row("성장의 지속성", "ODM 합산 매출 YoY", pct(o["yoy"][k]), pct(o["yoy"][prev_q(k)]), o["yoy"][k] - o["yoy"][prev_q(k)],
            "odm_yoy", "성장이 길게 이어질수록 높은 PER을 받기 쉬워요.", f"{k} vs {prev_q(k)}")
    k = o["opm_q"]
    if k and prev_q(k) in o["opm"]:
        row("이익의 질", "ODM 영업이익률 (TTM, 매출 가중)", f"{o['opm'][k]:.2f}%", f"{o['opm'][prev_q(k)]:.2f}%",
            o["opm"][k] - o["opm"][prev_q(k)], "odm_opm_ttm", "많이 팔수록 남는 돈이 늘면 이익의 질이 좋아져요.", f"{k} vs {prev_q(k)}")
    k = o["per_emp_q"]
    if k:
        a, b = o["per_emp"][k], o["per_emp"][prev_q(k)]
        row("이익의 질", "ODM 인당 매출 (별도 합산 TTM)", f"{a:.2f}억원", f"{b:.2f}억원", (a / b - 1) * 100, "odm_rev_per_emp",
            "사람보다 매출이 빨리 늘면 이익이 더 크게 늘어요.", f"{k} vs {prev_q(k)}")
    ch = []
    for basis in ("연결", "별도"):
        r = se.get(basis, {})
        ys = sorted(r)
        if len(ys) >= 2:
            ch.append((basis, r[ys[-1]], r[ys[-2]], ys[-1]))
    if ch:
        least = min(ch, key=lambda t: abs(t[1] - t[2]))  # 변화가 더 작은 쪽 (유리하게 읽지 않기)
        row("이익의 질", "계절성 4Q÷3Q (합산)", " / ".join(f"{b} {n:.3f}" for b, n, _, _ in ch),
            " / ".join(f"{b} {p:.3f}" for b, _, p, _ in ch), least[1] - least[2], "seasonality",
            "4분기 매출이 덜 줄면 1년 내내 이익이 고르게 나와요.", f"{ch[0][3]}년 vs 전년, 판정은 변화가 작은 {least[0]} 기준")
    if p["ok"]:
        row("시장 반영", "ODM PER ÷ 브랜드 PER", f"{p['gap']:.2f}배", f"{p['gap_prev']:.2f}배", p["gap"] - p["gap_prev"], "per_ratio",
            "ODM이 브랜드보다 덜 할인받으면 리레이팅이 진행 중이라는 뜻이에요.",
            f"ODM PER {p['odm_prev']:.1f}→{p['odm']:.1f}배, 브랜드 PER {p['brand_prev']:.1f}→{p['brand']:.1f}배. " + per_reason(p))
    rows.append({"cond": "이익 상향 대 멀티플", "metric": "주가 분해 (EPS 기여 대 PER 기여)", "now": "–", "prev": "–", "change": None,
                 "ind": None, "band": None, "level": "unknown", "why": "", "note": "수동 입력 컨센서스가 2개월 이상 쌓여야 계산"})
    rows.append({"cond": "주문 가시성", "metric": "선주문 기간", "now": "–", "prev": "–", "change": None, "ind": None,
                 "band": None, "level": "unknown", "why": "", "note": "공개 데이터 없음 — IR 확인(비공개 자료)"})
    return rows


# ── 홈 ───────────────────────────────────────────────────────────
def home(cfg) -> dict:
    ex, g, am, so, bo = m_exports(), m_geo(cfg), m_amazon(), m_stage_opm(cfg), m_booster()
    conds = stock_conditions(cfg)
    cards = []

    # 1 수출
    if ex["ok"]:
        ans = fill("exports_card", exports_yoy=f"{abs(ex['yoy']):.1f}%", exports_word=word(ex["yoy"]))
        cards.append({"id": "h_exports", "page": "exports", "question": "K뷰티, 해외에서 잘 팔리고 있나요?", "answer": ans,
                      "status": status(judge("exports_yoy", ex["yoy"])), "spark": {"data": ex["spark"], "unit": "usd"},
                      "facts": [{"text": f"{abs(ex['yoy']):.1f}%", "check": {"op": "yoy3_sum", "value": round(ex["yoy"], 1)}}],
                      "check_data": {"monthly": [[str(k), float(v)] for k, v in ex["raw"].items()]}})
    else:
        cards.append({"id": "h_exports", "page": "exports", "question": "K뷰티, 해외에서 잘 팔리고 있나요?",
                      "answer": no_data(ex["reason"]), "status": status("unknown")})
    # 2 해외 소비자
    if am["ok"]:
        d = am["kb"] - am["kb_prev"]
        cards.append({"id": "h_consumers", "page": "consumers", "question": "해외 소비자들이 실제로 사고 있나요?",
                      "answer": fill("consumer_card", kb_now=am["kb"], kb_prev=am["kb_prev"]),
                      "status": status(judge("kbrand_count", d)), "spark": {"data": am["spark"], "unit": "개"},
                      "sub": f"아마존 {am['last']} 기준, 1년 전은 {am['prev']} 스냅샷."
                             + (f" 판별 못 한 제품 {len(am['unmapped'])}개는 세지 않았어요." if am["unmapped"] else ""),
                      "facts": [{"text": f"{am['kb']}개", "check": {"op": "spark_at", "date": am["last"], "value": am["kb"]}},
                                {"text": f"{am['kb_prev']}개", "check": {"op": "spark_at", "date": am["prev"], "value": am["kb_prev"]}}]})
    else:
        cards.append({"id": "h_consumers", "page": "consumers", "question": "해외 소비자들이 실제로 사고 있나요?",
                      "answer": no_data(am["reason"]), "status": status("unknown")})
    # 3 밸류체인 (영업이익률 TTM)
    known = {st: v for st, v in so.items() if v.get("prev") is not None}
    if known:
        top = max(known, key=lambda s: known[s]["now"])
        rise = max(known, key=lambda s: known[s]["now"] - known[s]["prev"])
        lv = [judge("stage_opm_ttm", v["now"] - v["prev"]) for v in known.values()]
        lv += ["unknown"] * (len(STAGES) - len(known))
        rows = [[st, round(known[st]["now"], 2), round(known[st]["prev"], 2), round(known[st]["now"] - known[st]["prev"], 2),
                 LV[judge("stage_opm_ttm", known[st]["now"] - known[st]["prev"])]["text"]] for st in known]
        cards.append({"id": "h_chain", "page": "chain", "question": "누가 돈을 벌고 있나요?",
                      "answer": fill("chain_card", top_stage=top, top_opm=f"{known[top]['now']:.1f}%", rise_stage=rise,
                                     rise_pp=f"{known[rise]['now'] - known[rise]['prev']:+.1f}%p"),
                      "status": status(aggregate(lv)),
                      "spark": {"data": ser({q_first_day(k): v for k, v in known[top]["series"].items() if k >= prev_q(known[top]["q"], 12)}),
                                "unit": "%", "label": f"{top} 영업이익률(TTM)"},
                      "rows": {"columns": ["단계", "영업이익률(TTM)", "1년 전", "변화(%p)", "판정"], "data": rows},
                      "facts": [{"text": f"{known[top]['now']:.1f}%", "check": {"op": "row_max", "col": 1, "value": round(known[top]["now"], 1)}},
                                {"text": f"{known[rise]['now'] - known[rise]['prev']:+.1f}%p", "check": {"op": "row_max", "col": 3,
                                 "value": round(known[rise]["now"] - known[rise]["prev"], 1)}}]})
    else:
        cards.append({"id": "h_chain", "page": "chain", "question": "누가 돈을 벌고 있나요?", "answer": no_data("DART 재무 수집 전"),
                      "status": status("unknown")})
    # 4 주가
    lv = [c["level"] for c in conds]
    known_n = sum(1 for x in lv if x != "unknown")
    a, b = lv.count("good"), lv.count("weak")
    p = m_per(cfg)
    cards.append({"id": "h_stocks", "page": "stocks", "question": "화장품 주식이 리레이팅될 근거가 있나요?",
                  "answer": fill("stock_card", n=known_n, a=a, b=b) if known_n else no_data("판단 가능한 조건 없음"),
                  "status": status(aggregate(lv)),
                  "sub": f"나머지 {len(lv) - known_n}개는 아직 데이터가 없어요." if len(lv) > known_n else "",
                  "rows": {"columns": ["조건", "지표", "현재", "1년 전", "판정"],
                           "data": [[c["cond"], c["metric"], c["now"], c["prev"], LV[c["level"]]["text"]] for c in conds]},
                  "facts": [{"text": f"{known_n}개", "check": {"op": "count_known", "value": known_n}},
                            {"text": f"것 {a}개", "check": {"op": "count_level", "level": "좋아지는 중", "value": a}},
                            {"text": f"약하게 {b}개", "check": {"op": "count_level", "level": "약하게 좋아지는 중", "value": b}}]})
    # 5 스킨부스터
    if bo["ok"]:
        cards.append({"id": "h_booster", "page": "booster", "question": "리쥬란 같은 스킨부스터 시장은 어때요?",
                      "answer": fill("booster_card", booster_yoy=f"{abs(bo['yoy']):.1f}%", booster_word=word(bo["yoy"])),
                      "status": status(judge("booster_yoy", bo["yoy"])), "spark": {"data": bo["spark"], "unit": "usd"},
                      "sub": "9018.90은 리쥬란 말고도 여러 의료기기가 섞인 대용 지표예요.",
                      "facts": [{"text": f"{abs(bo['yoy']):.1f}%", "check": {"op": "yoy3_sum", "value": round(bo["yoy"], 1)}}],
                      "check_data": {"monthly": [[str(k), float(v)] for k, v in bo["raw"].items()]}})
    else:
        cards.append({"id": "h_booster", "page": "booster", "question": "리쥬란 같은 스킨부스터 시장은 어때요?",
                      "answer": no_data(bo["reason"]), "status": status("unknown")})
    # 주가 카드 스파크라인: ODM PER ÷ 브랜드 PER (최근 3년, 주간)
    _, kv, _ = valuation(cfg)
    for c in cards:
        if c["id"] == "h_stocks" and kv.get("gap_s"):
            c["spark"] = {"data": kv["gap_s"][-156:], "unit": "배", "label": "ODM PER ÷ 브랜드 PER"}

    summary = (fill("home_summary", exports_yoy=f"{ex['yoy']:.0f}%", exports_word="늘었고" if ex["yoy"] > 3 else ("줄었고" if ex["yoy"] < -3 else "비슷했고"),
                    china_share=f"{g['china']:.0f}%") if ex["ok"] and g["ok"] else no_data("수출 데이터 부족"))
    return {"summary": summary, "cards": cards, "changes": changes(cfg, ex, g, am, so, bo, conds)}


def changes(cfg, ex, g, am, so, bo, conds) -> list[dict]:
    """지난 기간 대비 가장 크게 움직인 지표 N개: |변화| ÷ 기준이 큰 순."""
    cand = []
    if g["ok"]:
        for reg in ("중국", "미국", "유럽", "일본", "동남아", "중동"):
            if reg in g["reg"]:
                now, prev = float(g["reg"][reg].iloc[-1]), float(g["reg"][reg].iloc[-13])
                d = now - prev
                cand.append((abs(d) / 1.0, f"{reg} 수출 비중이 1년 새 {prev:.1f}%에서 {now:.1f}%로 {'늘었어요' if d > 0 else '줄었어요'}.", "exports"))
    for hs, nm in (("3304991000", "기초화장품"), ("3304992000", "메이크업"), ("3304999000", "기타 화장품(선크림 포함 가능)")):
        y, _, _ = yoy3(monthly_total(hs))
        if y is not None:
            cand.append((abs(y) / S["indicators"]["exports_yoy"]["band"], f"{nm} 수출이 최근 3개월 1년 전보다 {y:.1f}% {word(y)}.", "exports"))
    if am["ok"]:
        d = am["kb"] - am["kb_prev"]
        cand.append((abs(d) / S["indicators"]["kbrand_count"]["band"], f"아마존 스킨케어 Top50 K브랜드가 1년 전 {am['kb_prev']}개에서 {am['kb']}개가 됐어요.", "consumers"))
    for st, v in so.items():
        if v.get("prev") is not None:
            d = v["now"] - v["prev"]
            cand.append((abs(d) / S["indicators"]["stage_opm_ttm"]["band"],
                         f"{st} 영업이익률(TTM)이 1년 새 {v['prev']:.1f}%에서 {v['now']:.1f}%로 {'올랐어요' if d > 0 else '내렸어요'}.", "chain"))
    for c in conds:
        if c["change"] is not None and c["band"]:
            cand.append((abs(c["change"]) / float(c["band"]), f"{c['metric']}: {c['prev']} → {c['now']}.", "stocks"))
    cand.sort(key=lambda t: -t[0])
    return [{"text": t, "page": p, "score": round(sc, 1)} for sc, t, p in cand[: S["changes_top_n"]]]


# ── 검증 (문장 숫자 = 데이터에서 다시 계산한 값) ─────────────────────
def verify(story: dict) -> list[str]:
    errs = []
    for c in story["home"]["cards"]:
        for f in c.get("facts", []):
            if f["text"] not in c["answer"]:
                errs.append(f"{c['id']}: 문장에 '{f['text']}'가 없음")
            chk = f["check"]
            op = chk["op"]
            if op == "yoy3_sum":
                m = pd.Series({pd.Period(k): v for k, v in c["check_data"]["monthly"]})
                y, _, _ = yoy3(m)
                got = round(y, 1) if y is not None else None
            elif op == "spark_at":
                got = next((v for d, v in c["spark"]["data"] if d == chk["date"]), None)
            elif op == "row_max":
                got = round(max(r[chk["col"]] for r in c["rows"]["data"]), 1)
            elif op == "count_known":
                got = sum(1 for r in c["rows"]["data"] if r[-1] != LV["unknown"]["text"])
            elif op == "count_level":
                got = sum(1 for r in c["rows"]["data"] if r[-1] == chk["level"])
            else:
                got = None
            if got != chk["value"]:
                errs.append(f"{c['id']}: '{f['text']}' 검사 실패 — 데이터에서 다시 계산한 값 {got}, 문장 값 {chk['value']}")
    return errs


# ── 용어 ─────────────────────────────────────────────────────────
def glossary() -> list[dict]:
    with (ROOT / "config" / "glossary.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("용어") or "").strip()]
    return [{"term": r["용어"].strip(), "desc": r["쉬운 뜻"].strip(), "ex": (r.get("비유·예시") or "").strip(),
             "alias": [a.strip() for a in (r.get("같이 찾을 표기") or "").split(";") if a.strip()]} for r in rows]


def build(cfg) -> dict:
    pages = {"exports": exports_page(cfg), "consumers": consumers_page(cfg), "chain": chain_page(cfg), "stocks": stocks_page(cfg), "booster": booster_page(cfg)}
    story = {"home": home(cfg), "pages": pages, "glossary": glossary(), "levels": LV}
    story["help"] = help_page(story)
    errs = verify(story)
    for p in pages.values():
        errs += verify_page(p)
    if errs:
        raise SystemExit("문장-데이터 불일치:\n  " + "\n  ".join(errs))
    return story


# ── 질문 페이지 공통 ─────────────────────────────────────────────
def events(page: str) -> list[dict]:
    f = ROOT / "manual" / "events.csv"
    if not f.exists():
        return []
    with f.open(encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("날짜") or "").strip() and not r["날짜"].startswith("#")]
    return [{"x": r["날짜"].strip(), "label": r["설명"].strip()} for r in rows
            if (r.get("페이지") or "all").strip() in (page, "all", "")]


def color(name: str) -> str:
    for k, v in S["colors"].items():
        if k != "default" and name.startswith(k):
            return v
    return S["colors"]["default"]


def mseries(name: str, s: pd.Series, role: str = "context", smooth: bool = True) -> dict:
    """월 시리즈 → 그래프 계열. role: main(진한 색) / context(회색) / extra('다른 항목 보기'로 켬)."""
    return {"name": name, "role": role, "smooth": smooth, "color": color(name),
            "data": [[f"{p.year}-{p.month:02d}-01", round(float(v), 4)] for p, v in s.dropna().items()]}


def gseries(name: str, pairs, role: str = "context", smooth: bool = False) -> dict:
    """임의 날짜 계열 (분기·주·일·스냅샷). pairs: {날짜 문자열: 값} 또는 [[날짜, 값]]."""
    items = pairs.items() if isinstance(pairs, dict) else pairs
    data = [[str(d)[:10], round(float(v), 4)] for d, v in sorted(items) if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return {"name": name, "role": role, "smooth": smooth, "color": color(name), "data": data}


def qpairs(d: dict) -> dict:
    """{'2025Q2': v} → {'2025-04-01': v}"""
    return {q_first_day(k): v for k, v in d.items()}


def card_nodata(cid: str, q: str, reason: str) -> dict:
    return {"id": cid, "question": q, "answer": no_data(reason), "status": status("unknown")}


def last_updated(collector: str) -> str:
    import json
    from lib.core import STATUS
    st = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
    return st.get(collector, {}).get("최근성공", "")


def exports_brand_corr(cfg) -> tuple[float | None, int]:
    """관세청 수출 YoY(분기 합) 대 브랜드사 단계 매출 YoY, 같은 분기 상관계수."""
    from .axis2 import stage_series
    comps = company_quarters(cfg, fin_table())
    bs = stage_series(comps, "브랜드사")["yoy"]
    s = monthly_total("3304")
    q = {}
    for p_, g in s.groupby(s.index.asfreq("Q")):
        if len(g) == 3:
            q[f"{p_.year}Q{p_.quarter}"] = float(g.sum())
    ey = {k: (v / q[prev_q(k)] - 1) * 100 for k, v in q.items() if prev_q(k) in q and q[prev_q(k)]}
    common = sorted(set(ey) & set(bs))
    if len(common) < 8:
        return None, len(common)
    return float(pd.Series([ey[k] for k in common]).corr(pd.Series([bs[k] for k in common]))), len(common)


# ── 수출 페이지 ───────────────────────────────────────────────────
def exports_page(cfg) -> dict:
    ev = events("exports")
    upd = last_updated("customs")
    src = "관세청 품목별·국가별 수출입실적(공공데이터포털). 배에 실린(출하) 기준"
    cards = []

    # ① 전체 추세
    ex = m_exports()
    if ex["ok"]:
        r, n = exports_brand_corr(cfg)
        why = (f"수출은 브랜드사 매출과 같은 방향으로 움직여 왔어요(분기 상관계수 {r:.2f}, {n}분기)." if r is not None and r >= 0.5
               else "수출은 회사 실적보다 한두 달 먼저 나오는 공개 숫자예요.")
        cards.append({"id": "e_total", "question": "화장품 수출, 계속 늘고 있나요?",
                      "answer": f"최근 3개월 수출은 {usd(ex['cur_sum'])}로, 1년 전보다 {abs(ex['yoy']):.1f}% {word(ex['yoy'])}.",
                      "status": status(judge("exports_yoy", ex["yoy"])), "why": why,
                      "chart": {"unit": "usd", "freq": "M", "events": ev, "series": [mseries("화장품 수출", ex["raw"], "main")]},
                      "more": {"정의": "HS 코드 3304(화장품) 전체의 월별 수출액", "계산": "최근 3개월 합 ÷ 1년 전 같은 3개월 합 − 1. 그래프 진한 선은 3개월 평균, 옅은 선은 월 원래 값",
                               "출처": src, "갱신": upd, "등급": "자동 수집"},
                      "facts": [{"text": usd(ex["cur_sum"]), "check": {"op": "sum3_usd", "series": "화장품 수출"}},
                                {"text": f"{abs(ex['yoy']):.1f}%", "check": {"op": "yoy3_series", "series": "화장품 수출", "value": round(ex["yoy"], 1)}}]})
    else:
        cards.append({"id": "e_total", "question": "화장품 수출, 계속 늘고 있나요?", "answer": no_data(ex["reason"]), "status": status("unknown")})

    # ② 나라별 비중
    g = m_geo(cfg)
    if g["ok"]:
        reg = g["reg"].copy()
        reg.index = pd.PeriodIndex(reg.index, freq="M")
        sl = [mseries("중국", reg["중국"], "main", smooth=False), mseries("미국", reg["미국"], smooth=False),
              mseries("유럽", reg["유럽"], smooth=False)]
        sl += [mseries(n, reg[n], "extra", smooth=False) for n in ("일본", "동남아", "중동") if n in reg]
        d_cn = g["china"] - g["china_prev"]
        cards.append({"id": "e_geo", "question": "중국에 기대는 정도가 줄고 있나요?",
                      "answer": f"중국 비중이 1년 새 {g['china_prev']:.1f}%에서 {g['china']:.1f}%로 {'줄었어요' if d_cn < 0 else '늘었어요'}.",
                      "status": status(judge("hhi", g["hhi"] - g["hhi_prev"])),
                      "why": f"한 나라에 쏠리면 그 나라 규제에 실적이 흔들려요. HHI도 {g['hhi_prev']:.0f}에서 {g['hhi']:.0f}로 {'낮아졌어요' if g['hhi'] < g['hhi_prev'] else '높아졌어요'}.",
                      "chart": {"unit": "%", "freq": "M", "events": ev, "series": sl},
                      "more": {"정의": "나라(지역)별 수출액 ÷ 전체 수출액. 최근 12개월 누적 기준",
                               "계산": "HHI = 나라별 비중(%)의 제곱을 모두 더한 값. 0~10,000, 낮을수록 고르게 퍼짐. 판정은 HHI 1년 변화(기준 ±50)",
                               "출처": src, "갱신": upd, "등급": "자동 수집"},
                      "facts": [{"text": f"{g['china_prev']:.1f}%", "check": {"op": "value_at", "series": "중국", "date": f"{g['month_prev']}-01", "value": round(g["china_prev"], 1)}},
                                {"text": f"{g['china']:.1f}%", "check": {"op": "value_at", "series": "중국", "date": f"{g['month']}-01", "value": round(g["china"], 1)}}]})
    else:
        cards.append({"id": "e_geo", "question": "중국에 기대는 정도가 줄고 있나요?", "answer": no_data(g["reason"]), "status": status("unknown")})

    # ③ 제품별
    prods = [("기초화장품", "3304991000", "main"), ("메이크업", "3304992000", "context"), ("기타", "3304999000", "context"),
             ("입술화장용", "330410", "extra"), ("눈화장용", "330420", "extra"), ("파우더", "330491", "extra")]
    sl, ys = [], {}
    for nm, hs, role in prods:
        s = monthly_total(hs)
        if not s.empty:
            sl.append(mseries(nm, s, role))
            ys[nm] = yoy3(s)[0]
    if ys.get("기초화장품") is not None and ys.get("메이크업") is not None:
        a, b = ys["기초화장품"], ys["메이크업"]
        wa = word(a)
        cards.append({"id": "e_items", "question": "어떤 제품이 늘었나요?",
                      "answer": f"기초화장품은 {abs(a):.1f}% {wa[:-3] + '고' if wa.endswith('었어요') else wa + ','} 메이크업은 {abs(b):.1f}% {word(b)}.",
                      "status": status(judge("exports_yoy", a)),
                      "why": f"선크림은 따로 집계되지 않아 '기타'에 섞여 있어요(기타 {ys.get('기타', 0):+.1f}%).",
                      "chart": {"unit": "usd", "freq": "M", "events": ev, "series": sl},
                      "more": {"정의": "HS 코드 3304.99의 10단위 품목(기초 -1000, 메이크업 -2000, 기타 -9000)과 3304.10·20·91",
                               "계산": "최근 3개월 합 YoY. 선크림 전용 코드는 없어요", "출처": src, "갱신": upd, "등급": "자동 수집"},
                      "facts": [{"text": f"{abs(a):.1f}%", "check": {"op": "yoy3_series", "series": "기초화장품", "value": round(a, 1)}},
                                {"text": f"{abs(b):.1f}%", "check": {"op": "yoy3_series", "series": "메이크업", "value": round(b, 1)}}]})
    else:
        cards.append({"id": "e_items", "question": "어떤 제품이 늘었나요?", "answer": no_data("품목별 수출 수집 전"), "status": status("unknown")})

    # ④ 1kg당 수출단가
    v, _ = customs_pivot("3304")
    w, _ = customs_pivot("3304", "수출중량")
    if not v.empty and not w.empty:
        rv, rw = v.rolling(12).sum(), w.reindex_like(v).fillna(0).rolling(12).sum()
        up = (rv.sum(axis=1) / rw.sum(axis=1)).dropna()
        up.index = pd.PeriodIndex(up.index, freq="M")
        regmap = region_of(cfg)

        def grp(ccs):
            cols = [c for c in ccs if c in rv]
            s = (rv[cols].sum(axis=1) / rw[cols].sum(axis=1)).dropna()
            s.index = pd.PeriodIndex(s.index, freq="M")
            return s
        sl = [mseries("전체 단가", up, "main", smooth=False), mseries("미국 단가", grp(["US"]), smooth=False)]
        sl += [mseries(f"{n} 단가", grp([c for c, r in regmap.items() if r == n]), "extra", smooth=False) for n in ("중국", "일본", "유럽")]
        now, prev = float(up.iloc[-1]), float(up.iloc[-13])
        ch = (now / prev - 1) * 100
        verb = "비슷해요" if abs(ch) <= S["indicators"]["exports_yoy"]["band"] else ("올랐어요" if ch > 0 else "내렸어요")
        cards.append({"id": "e_price", "question": "비싸게 팔리고 있나요?",
                      "answer": (f"1kg당 수출단가는 {now:.1f}달러로 1년 전({prev:.1f}달러)과 {verb}." if verb == "비슷해요"
                                 else f"1kg당 수출단가는 {now:.1f}달러로 1년 전({prev:.1f}달러)보다 {verb}."),
                      "status": status(judge("exports_yoy", ch)),
                      "why": "단가가 오르면 같은 양을 팔아도 매출이 커져요. 제품 구성이 바뀌어도 움직여요.",
                      "chart": {"unit": "usd_kg", "freq": "M", "events": ev, "series": sl},
                      "more": {"정의": "수출단가 = 수출액 ÷ 수출 무게(kg). 최근 12개월 누적", "계산": "판정은 1년 전 대비 변화율(기준 ±3%)",
                               "출처": src, "갱신": upd, "등급": "자동 수집"},
                      "facts": [{"text": f"{now:.1f}달러", "check": {"op": "value_at", "series": "전체 단가", "date": f"{up.index[-1]}-01", "value": round(now, 1)}},
                                {"text": f"{prev:.1f}달러", "check": {"op": "value_at", "series": "전체 단가", "date": f"{up.index[-13]}-01", "value": round(prev, 1)}}]})
    return {"key": "exports", "question": "K뷰티, 해외에서 잘 팔리고 있나요?", "label": "전방수요 모니터", "cards": cards}


def verify_page(page: dict) -> list[str]:
    errs = []
    for c in page["cards"]:
        ser_by = {s["name"]: s for s in (c.get("chart") or {}).get("series", [])}
        for f in c.get("facts", []):
            if f["text"] not in c["answer"]:
                errs.append(f"{page['key']}/{c['id']}: 문장에 '{f['text']}'가 없음")
            chk = f["check"]
            if chk["op"].startswith("t_"):  # 표에서 다시 세기
                lv_col = [r[4]["level"] for r in c["table"]["rows"]]
                got = (sum(1 for x in lv_col if x != "unknown") if chk["op"] == "t_count_known"
                       else sum(1 for x in lv_col if x == chk["level"]))
                if got != chk["value"]:
                    errs.append(f"{page['key']}/{c['id']}: '{f['text']}' 검사 실패 — 표에서 다시 센 값 {got}")
                continue
            s = ser_by.get(chk.get("series"))
            if s is None:
                errs.append(f"{page['key']}/{c['id']}: 계열 '{chk.get('series')}' 없음")
                continue
            m = pd.Series({pd.Period(d[:7]): v for d, v in s["data"]}) if (c.get("chart") or {}).get("freq") == "M" else pd.Series(dtype=float)
            if chk["op"] == "yoy3_series":
                y, _, _ = yoy3(m)
                ok = y is not None and round(y, 1) == chk["value"]
                got = None if y is None else round(y, 1)
            elif chk["op"] == "sum3_usd":
                y, cur, _ = yoy3(m)
                got = usd(m[cur].sum()) if cur else None
                ok = got == f["text"]
            elif chk["op"] == "value_at":
                got = round(next((v for d, v in s["data"] if d == chk["date"]), float("nan")), 1)
                ok = got == chk["value"]
            elif chk["op"] == "diff_year":  # 마지막 값 − 1년(4분기) 전 값
                pts = s["data"]
                last_d = pts[-1][0]
                prev_d = f"{int(last_d[:4]) - 1}{last_d[4:]}"
                pv = next((v for d, v in pts if d == prev_d), None)
                got = None if pv is None else round(pts[-1][1] - pv)
                ok = got == chk["value"]
            elif chk["op"] == "last_fmt0":
                got = round(s["data"][-1][1])
                ok = got == chk["value"]
            elif chk["op"] == "last":
                got = round(s["data"][-1][1], 1)
                ok = got == chk["value"]
            elif chk["op"] == "last_minus100":
                got = round(s["data"][-1][1] - 100, 1)
                ok = got == chk["value"]
            elif chk["op"] == "sum_last":
                got = round(sum(v for _, v in s["data"][-chk["n"]:]), 1)
                ok = got == chk["value"]
            else:
                ok, got = False, "알 수 없는 검사"
            if not ok:
                errs.append(f"{page['key']}/{c['id']}: '{f['text']}' 검사 실패 — 그래프 데이터로 다시 계산한 값 {got}")
    return errs


# ── 해외 소비자 페이지 ────────────────────────────────────────────
def news_store_weekly(cfg) -> tuple[dict, dict]:
    """입점 기사 주간 건수 (전체, 채널별). '채널·입점'(신규)·'브랜드·채널'(기존 트래커 이관) 그룹에서 입점 키워드가 제목에 있는 기사."""
    from lib.core import DATA
    f = DATA / "news_items.csv"
    if not f.exists():
        return {}, {}
    pat = re.compile("|".join(re.escape(k) for k in cfg["news"]["store_keywords"]), re.I)
    chans = {ch: re.compile("|".join(re.escape(k) for k in kws), re.I) for ch, kws in cfg["news"].get("channels", {}).items()}
    tot, by = {}, {ch: {} for ch in chans}
    with f.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if not any(g in r["그룹"] for g in ("채널·입점", "브랜드·채널")):
                continue
            text = f"{r['제목']} {r.get('제목_ko', '')}"
            if not pat.search(text):
                continue
            try:
                d = pd.Timestamp(r["날짜"])
            except ValueError:
                continue
            wk = (d - pd.Timedelta(days=d.weekday())).strftime("%Y-%m-%d")
            tot[wk] = tot.get(wk, 0) + 1
            for ch, pt in chans.items():
                if pt.search(text):
                    by[ch][wk] = by[ch].get(wk, 0) + 1
    weeks = sorted(tot)
    news_start = ""  # 지금 수집 방식(신규 뉴스 수집기)이 시작된 날 — 이관 기사의 수집일은 '기존 트래커 이관'
    with f.open(encoding="utf-8-sig", newline="") as fh:
        ds = [r["수집일"] for r in csv.DictReader(fh) if re.match(r"\d{4}-\d{2}-\d{2}$", r.get("수집일", ""))]
    news_start = min(ds) if ds else ""
    tot["_start"] = news_start
    if weeks:  # 기사가 없는 주는 0으로 채운다
        allw = pd.date_range(weeks[0], weeks[-1], freq="7D").strftime("%Y-%m-%d")
        tot = {**{w: tot.get(w, 0) for w in allw}, "_start": news_start}
        by = {ch: {w: v.get(w, 0) for w in allw} for ch, v in by.items()}
    return tot, by


def consumers_page(cfg) -> dict:
    cards = []
    tab, un = amazon_table()
    sh = amazon_share(tab)
    am = m_amazon()
    src_am = "아마존 US 베스트셀러 Top50 (2022~2026-02는 과거 스냅샷, 2026-09부터 직접 수집)"
    crit = "브랜드 국적·생산지는 config/brand_master.csv 수동 판정(README '아마존 지표'). 판별 못 한 제품은 세지 않아요"
    if am["ok"]:
        def cnt(cat, col):
            s = sh[sh["cat"] == cat].set_index("date")
            return {d: float(v) for d, v in s[col].items()}
        d = am["kb"] - am["kb_prev"]
        unm = "; ".join(f"{r}위 {t}" for r, _, t in am["unmapped"]) or "없음"
        cards.append({"id": "c_kbrand", "question": "아마존에서 K브랜드가 늘고 있나요?",
                      "answer": fill("consumer_card", kb_now=am["kb"], kb_prev=am["kb_prev"]),
                      "status": status(judge("kbrand_count", d)),
                      "why": "아마존은 미국 화장품 온라인 판매의 대표 채널이고, 베스트셀러 순위는 판매량으로 매겨져요.",
                      "chart": {"unit": "개", "freq": "S", "events": events("consumers"),
                                "series": [gseries("K브랜드", cnt("스킨케어", "kb"), "main")] +
                                          [gseries(f"{c} K브랜드", cnt(c, "kb"), "extra") for c in ("선케어", "페이셜 트리트먼트", "뷰티 전체") if (sh["cat"] == c).any()]},
                      "more": {"정의": "스킨케어 Top50 안의 한국 브랜드 제품 수 (비율로는 %.0f%%, 1년 전 %.0f%%)" % (am["kb_pct"], am["kb_pct_prev"]),
                               "판별": crit, "판별 못 한 제품": unm, "비교": f"{am['last']} 대 1년 전 가장 가까운 스냅샷 {am['prev']}. 기준 ±3개 이내면 '비슷해요'",
                               "출처": src_am, "갱신": last_updated("amazon"), "등급": "자동 수집"},
                      "facts": [{"text": f"{am['kb']}개", "check": {"op": "value_at", "series": "K브랜드", "date": am["last"], "value": am["kb"]}},
                                {"text": f"{am['kb_prev']}개", "check": {"op": "value_at", "series": "K브랜드", "date": am["prev"], "value": am["kb_prev"]}}]})
        s = sh[sh["cat"] == "스킨케어"].set_index("date")
        mik, mik_p, fb = int(s.loc[am["last"], "mik"]), int(s.loc[am["prev"], "mik"]), int(s.loc[am["last"], "fb"])
        cards.append({"id": "c_kprod", "question": "한국에서 만든 제품도 늘고 있나요?",
                      "answer": f"스킨케어 Top50 중 한국생산 제품은 {mik}개예요(1년 전 {mik_p}개). 그중 외국 브랜드는 {fb}개예요.",
                      "status": status(judge("kprod_count", mik - mik_p)),
                      "why": "외국 브랜드가 한국 ODM에 생산을 맡기면 K브랜드가 아니어도 한국생산으로 잡혀요.",
                      "chart": {"unit": "개", "freq": "S", "events": events("consumers"),
                                "series": [gseries("한국생산", cnt("스킨케어", "mik"), "main"), gseries("K브랜드", cnt("스킨케어", "kb")),
                                           gseries("그중 외국 브랜드", cnt("스킨케어", "fb"))]},
                      "more": {"정의": "한국생산 = 한국에서 만든 제품(브랜드 국적 무관). 외국 브랜드 = 한국생산이지만 K브랜드가 아닌 것",
                               "판별": crit, "출처": src_am, "갱신": last_updated("amazon"), "등급": "자동 수집"},
                      "facts": [{"text": f"{mik}개", "check": {"op": "value_at", "series": "한국생산", "date": am["last"], "value": mik}},
                                {"text": f"{mik_p}개", "check": {"op": "value_at", "series": "한국생산", "date": am["prev"], "value": mik_p}},
                                {"text": f"{fb}개", "check": {"op": "value_at", "series": "그중 외국 브랜드", "date": am["last"], "value": fb}}]})
    else:
        cards += [card_nodata("c_kbrand", "아마존에서 K브랜드가 늘고 있나요?", am["reason"]),
                  card_nodata("c_kprod", "한국에서 만든 제품도 늘고 있나요?", am["reason"])]

    # 검색 관심도 (영문 위키 조회수, 대용)
    wk = obs("wiki")
    if not wk.empty:
        p = wk.pivot_table(index="기준일", columns="구분", values="값", aggfunc="sum")
        p.index = pd.to_datetime(p.index)
        m = p.resample("MS").sum(min_count=20)
        m = m.iloc[:-1] if m.index[-1].month == pd.Timestamp.now().month else m  # 이번 달(미완성) 제외
        m.index = m.index.to_period("M")
        kb = m["K-beauty"].dropna()
        y, cur, prev = yoy3(kb)
        if y is not None:
            avg = kb[cur].mean()
            sl = [mseries("K-beauty", kb, "main"), mseries("Laneige", m["Laneige"].dropna())]
            sl += [mseries(c, m[c].dropna(), "extra") for c in ("Innisfree", "Amorepacific") if c in m]
            cards.append({"id": "c_search", "question": "해외에서 K뷰티를 찾아보는 사람이 늘었나요?",
                          "answer": f"영문 위키백과 'K-beauty' 조회수는 최근 3개월 1년 전보다 {abs(y):.1f}% {word(y)}.",
                          "status": status(judge("wiki_yoy", y)),
                          "why": "찾아보는 사람 수는 관심을 대신 재는 대용 지표예요. 실제 구매와는 달라요.",
                          "chart": {"unit": "회", "freq": "M", "events": events("consumers"), "series": sl},
                          "more": {"정의": f"영문 위키백과 문서 월 조회수(최근 3개월 월평균 {avg:,.0f}회)", "계산": "최근 3개월 합 YoY. 기준 ±10%",
                                   "한계": "Google Trends는 공식 무료 API가 없어 월 1회 수동 입력(manual/google_trends.csv, 지금 비어 있음)",
                                   "출처": "Wikimedia Pageviews API", "갱신": last_updated("wiki"), "등급": "대용 지표"},
                          "facts": [{"text": f"{abs(y):.1f}%", "check": {"op": "yoy3_series", "series": "K-beauty", "value": round(y, 1)}}]})
    if not any(c["id"] == "c_search" for c in cards):
        cards.append(card_nodata("c_search", "해외에서 K뷰티를 찾아보는 사람이 늘었나요?", "위키 조회수 수집 전"))

    # 입점 소식
    tot, by = news_store_weekly(cfg)
    start = tot.pop("_start", "")
    ws = sorted(tot)
    if len(ws) >= 4:
        cur4 = sum(tot[w] for w in ws[-4:])
        # 두 4주 구간이 모두 지금 수집 방식 시작 이후일 때만 비교 (방식이 바뀌면 기사 수가 달라짐)
        comparable = len(ws) >= 8 and start and ws[-8] >= (pd.Timestamp(start) - pd.Timedelta(days=pd.Timestamp(start).weekday())).strftime("%Y-%m-%d")
        prev4 = sum(tot[w] for w in ws[-8:-4]) if comparable else None
        ch = (cur4 / prev4 - 1) * 100 if prev4 else None
        ans = (f"최근 4주 입점 관련 기사는 {cur4}건으로, 직전 4주({prev4}건)보다 {'늘었어요' if cur4 > prev4 else ('줄었어요' if cur4 < prev4 else '같아요')}."
               if prev4 is not None else
               f"최근 4주 입점 관련 기사는 {cur4}건이에요. 수집 방식이 {pd.Timestamp(start):%Y년 %-m월 %-d일}부터 바뀌어 과거와는 아직 비교하지 않아요.")
        cards.append({"id": "c_store", "question": "세포라·코스트코 입점 소식이 늘고 있나요?", "answer": ans,
                      "status": status(judge("store_news", ch) if ch is not None else "unknown"),
                      "why": "매장 입점은 온라인에서 뜬 브랜드가 오프라인으로 넓어지는 신호예요. 기사 수는 입점 자체가 아니라 보도량이에요.",
                      "chart": {"unit": "건", "freq": "W", "events": events("consumers"),
                                "series": [gseries("입점 기사", tot, "main")] + [gseries(ch_, v, "extra") for ch_, v in by.items()]},
                      "more": {"정의": "Google News 기사 중 제목에 입점 키워드(세포라·울타·코스트코·틱톡샵·입점 등)가 있는 기사 수, 주 단위",
                               "한계": "RSS는 과거를 다시 받을 수 없어 2026-06부터만 있어요. 틱톡샵 매출은 공개되지 않아요",
                               "출처": "Google News RSS (config/tracker.yml의 news)", "갱신": last_updated("news"), "등급": "대용 지표"},
                      "facts": [{"text": f"{cur4}건", "check": {"op": "sum_last", "series": "입점 기사", "n": 4, "value": cur4}}]})
    else:
        cards.append(card_nodata("c_store", "세포라·코스트코 입점 소식이 늘고 있나요?", "입점 기사가 4주 이상 쌓이기 전"))
    return {"key": "consumers", "question": "해외 소비자들이 실제로 사고 있나요?", "label": "전방수요 모니터", "cards": cards}


# ── 밸류체인 페이지 ───────────────────────────────────────────────
def chain_page(cfg) -> dict:
    from .axis2 import stage_series
    comps = company_quarters(cfg, fin_table())
    so = m_stage_opm(cfg)
    agg = {st: stage_series(comps, st) for st in STAGES}
    members = {st: [n for n, v in comps.items() if v["stage"] == st] for st in STAGES}
    src = "DART 정기보고서(연결, 연결재무제표가 없는 회사는 별도) + 수동 입력(연우 2025~, CJ올리브영)"
    note_w = "단계 값은 매출 가중(합산). 두 시점 모두 값이 있는 회사만 비교"
    cards = []
    # 단계 요약 (신호등)
    boxes = []
    for st in STAGES:
        o = so.get(st)
        a = agg[st]
        ky = max(a["yoy"]) if a["yoy"] else None
        ki = max(a["inv"]) if a["inv"] else None
        m = []
        if o and o.get("prev") is not None:
            lv = judge("stage_opm_ttm", o["now"] - o["prev"])
            m.append({"label": "영업이익률(TTM)", "value": f"{o['now']:.1f}%", "sub": f"1년 전 {o['prev']:.1f}%", **status(lv)})
        if ky:
            m.append({"label": "매출 YoY", "value": f"{a['yoy'][ky]:+.1f}%", "sub": f"{ky[:4]} {ky[-1]}Q", **status(judge("stage_yoy", a["yoy"][ky]))})
        if ki and prev_q(ki) in a["inv"]:
            m.append({"label": "재고회전일수", "value": f"{a['inv'][ki]:.0f}일", "sub": f"1년 전 {a['inv'][prev_q(ki)]:.0f}일",
                      **status(judge("inv_days", a["inv"][ki] - a["inv"][prev_q(ki)]))})
        if not m:
            m.append({"label": "재무", "value": "데이터 없음", "sub": "올리브영 수동 입력 전", **status("unknown")})
        boxes.append({"name": st, "members": members[st], "metrics": m})
    known = {st: v for st, v in so.items() if v.get("prev") is not None}
    top = max(known, key=lambda s: known[s]["now"]) if known else None
    rise = max(known, key=lambda s: known[s]["now"] - known[s]["prev"]) if known else None
    lv_opm = [judge("stage_opm_ttm", v["now"] - v["prev"]) for v in known.values()] + ["unknown"] * (len(STAGES) - len(known))
    ans_opm = fill("chain_card", top_stage=top, top_opm=f"{known[top]['now']:.1f}%", rise_stage=rise,
                   rise_pp=f"{known[rise]['now'] - known[rise]['prev']:+.1f}%p") if known else no_data("DART 재무 수집 전")
    def names(lvl):
        return [st for st in known if judge("stage_opm_ttm", known[st]["now"] - known[st]["prev"]) in lvl]
    good_s, bad_s = names(("good", "weak")), names(("bad",))
    ans_chain = ("영업이익률이 좋아지는 단계는 " + ("·".join(good_s) if good_s else "없고") +
                 (", 나빠지는 단계는 " + ieyo("·".join(bad_s)) + "." if bad_s else ", 나빠지는 단계는 없어요.")) if known else no_data("DART 재무 수집 전")
    ans_chain = ans_chain.replace("없고,", "없고")
    cards.append({"id": "v_chain", "question": "원료에서 소비자까지, 각 단계는 어떤가요?", "answer": ans_chain,
                  "status": status(aggregate(lv_opm)), "chain": boxes, "wide": True,
                  "why": "물건은 왼쪽에서 오른쪽으로, 주문 신호는 오른쪽에서 왼쪽으로 흘러요. 소비자 쪽이 먼저 움직일 때가 많아요.",
                  "more": {"판정": "영업이익률은 1년 전 대비 ±0.5%p, 매출 YoY는 0 대비 ±3%, 재고회전일수는 1년 전 대비 ±5일(늘면 나쁨)",
                           "계산": note_w, "출처": src, "갱신": last_updated("dart"), "등급": "자동 수집 (채널은 수동 입력)"}})

    def stage_chart_series(key_fn, main, context):
        sl = []
        for st in STAGES:
            d = key_fn(st)
            if not d:
                continue
            role = "main" if st == main else ("context" if st in context else "extra")
            sl.append(gseries(st, qpairs(d), role))
        return sl
    if known:
        ctx = [x for x in (top, "ODM") if x != rise]
        cards.append({"id": "v_opm", "question": "이익은 어느 단계에 남나요?", "answer": ans_opm, "status": status(aggregate(lv_opm)),
                      "why": "영업이익률이 높은 단계일수록 같은 매출에서 더 많은 이익을 가져가요.",
                      "chart": {"unit": "%", "freq": "Q", "events": events("chain"),
                                "series": stage_chart_series(lambda st: so.get(st, {}).get("series"), rise, ctx)},
                      "more": {"정의": "영업이익률(TTM) = 최근 4분기 합산 영업이익 ÷ 최근 4분기 합산 매출", "계산": note_w, "출처": src,
                               "갱신": last_updated("dart"), "등급": "자동 수집"},
                      "facts": [{"text": f"{known[top]['now']:.1f}%", "check": {"op": "value_at", "series": top, "date": q_first_day(known[top]["q"]), "value": round(known[top]["now"], 1)}}]})
    ys = {st: (max(agg[st]["yoy"]), agg[st]["yoy"][max(agg[st]["yoy"])]) for st in STAGES if agg[st]["yoy"]}
    if ys:
        fast = max(ys, key=lambda s: ys[s][1])
        slow = min(ys, key=lambda s: ys[s][1])
        lv = [judge("stage_yoy", v) for _, v in ys.values()] + ["unknown"] * (len(STAGES) - len(ys))
        cards.append({"id": "v_yoy", "question": "매출은 어느 단계가 가장 빨리 늘고 있나요?",
                      "answer": f"최근 분기 매출이 가장 빨리 느는 단계는 {fast}({ys[fast][1]:+.1f}%), 가장 느린 단계는 {slow}({ys[slow][1]:+.1f}%)예요.",
                      "status": status(aggregate(lv)),
                      "why": "단계마다 성장 속도가 다르면, 어느 단계에 수요가 먼저 닿는지 볼 수 있어요.",
                      "chart": {"unit": "%", "freq": "Q", "events": events("chain"),
                                "series": stage_chart_series(lambda st: agg[st]["yoy"], fast, [x for x in ("ODM", slow) if x != fast])},
                      "more": {"정의": "매출 YoY = 단계 합산 분기 매출 ÷ 1년 전 같은 분기 − 1", "계산": note_w, "출처": src,
                               "갱신": last_updated("dart"), "등급": "자동 수집"},
                      "facts": [{"text": f"{ys[fast][1]:+.1f}%", "check": {"op": "value_at", "series": fast, "date": q_first_day(ys[fast][0]), "value": round(ys[fast][1], 1)}}]})
    inv = {st: agg[st]["inv"] for st in STAGES if agg[st]["inv"]}
    dif = {st: (max(d), d[max(d)] - d[prev_q(max(d))]) for st, d in inv.items() if prev_q(max(d)) in d}
    if dif:
        up_ = max(dif, key=lambda s: dif[s][1])
        lv = [judge("inv_days", v) for _, v in dif.values()] + ["unknown"] * (len(STAGES) - len(dif))
        verb = "늘어난" if dif[up_][1] > 0 else "적게 줄어든"
        cards.append({"id": "v_inv", "question": "재고는 어디에 쌓이고 있나요?",
                      "answer": f"재고회전일수가 1년 새 가장 많이 {verb} 단계는 {ieyo(f'{up_}({dif[up_][1]:+.0f}일)')}.",
                      "status": status(aggregate(lv)),
                      "why": "재고일수가 늘면 만든 물건이 팔리기까지 오래 걸린다는 뜻이에요. 미리 쌓아 두는 경우도 있어요.",
                      "chart": {"unit": "일", "freq": "Q", "events": events("chain"),
                                "series": stage_chart_series(lambda st: inv.get(st), up_, [x for x in ("ODM",) if x != up_])},
                      "more": {"정의": "재고회전일수 = 분기말 재고자산 ÷ 최근 4분기 매출 × 365", "계산": note_w + ". 판정 ±5일, 늘면 나빠지는 쪽",
                               "출처": src, "갱신": last_updated("dart"), "등급": "자동 수집"},
                      "facts": [{"text": f"{dif[up_][1]:+.0f}일", "check": {"op": "diff_year", "series": up_, "value": round(dif[up_][1])}}]})
    return {"key": "chain", "question": "누가 돈을 벌고 있나요?", "label": "새로 추가", "cards": cards}


# ── 주가 페이지 ───────────────────────────────────────────────────
def stocks_page(cfg) -> dict:
    cards = []
    conds = stock_conditions(cfg)
    lv = [c["level"] for c in conds]
    kn = sum(1 for x in lv if x != "unknown")
    a, b = lv.count("good"), lv.count("weak")
    rows = [[c["cond"], c["metric"], c["now"], c["prev"], {"level": c["level"], **LV[c["level"]]},
             c["why"] or c["note"]] for c in conds]
    cards.append({"id": "s_check", "question": "리레이팅 조건, 몇 개나 갖춰지고 있나요?", "wide": True, "table_place": "body",
                  "answer": fill("stock_card", n=kn, a=a, b=b), "status": status(aggregate(lv)),
                  "sub": f"나머지 {len(lv) - kn}개는 아직 데이터가 없어요." if len(lv) > kn else "",
                  "table": {"columns": ["조건", "지표", "지금", "1년 전", "판정", "이게 왜 주가랑 관련 있어요?"], "rows": rows},
                  "why": "이익이 같아도 시장이 성장을 믿으면 더 높은 PER을 쳐줘요. 이 조건들이 그 믿음의 근거예요.",
                  "more": {"판정": "변화 ≤ 기준 비슷해요, 기준~2배 약하게 좋아지는 중, 2배 이상 좋아지는 중, 나쁜 쪽으로 기준 초과 나빠지는 중. "
                                  "종합은 판단 가능한 조건 중 '좋아지는 중'이 과반이면 좋아지는 중, '약하게' 포함 과반이면 약하게 좋아지는 중",
                           "기준값": "config/story.yml", "참고": "; ".join(f"{c['metric']}: {c['note']}" for c in conds if c["note"]),
                           "등급": "자동 수집 (주가 분해는 수동 입력, 주문 가시성은 데이터 없음)"},
                  "facts": [{"text": f"{kn}개", "check": {"op": "t_count_known", "value": kn}},
                            {"text": f"것 {a}개", "check": {"op": "t_count_level", "level": "good", "value": a}},
                            {"text": f"약하게 {b}개", "check": {"op": "t_count_level", "level": "weak", "value": b}}]})
    # PER 따로 보기
    _, kv, _ = valuation(cfg)
    p = m_per(cfg)
    if p["ok"] and kv.get("per_s"):
        ps = kv["per_s"]
        sl = [gseries("ODM PER", ps["ODM"], "main"), gseries("브랜드 PER", ps["브랜드"])]
        sl += [gseries(f"{g} PER", ps[g], "extra") for g in ("메디컬", "원부자재", "유통") if g in ps]
        cards.append({"id": "s_per", "question": "ODM이 브랜드보다 덜 할인받고 있나요?",
                      "answer": f"ODM PER은 {p['odm']:.1f}배, 브랜드 PER은 {p['brand']:.1f}배예요(1년 전 {p['odm_prev']:.1f}배·{p['brand_prev']:.1f}배).",
                      "status": status(judge("per_ratio", p["gap"] - p["gap_prev"])),
                      "why": per_reason(p) + f" 비율(ODM ÷ 브랜드)은 {p['gap_prev']:.2f}에서 {p['gap']:.2f}이 됐어요.",
                      "chart": {"unit": "배", "freq": "W", "events": events("stocks"), "series": sl},
                      "more": {"정의": "후행 PER = 시가총액 ÷ 최근 4분기 순이익. 그룹 PER = 흑자 기업 시총 합 ÷ 순이익 합",
                               "계산": "판정은 ODM ÷ 브랜드 비율의 52주 변화(기준 ±0.03)", "출처": "금융위원회 주식시세 + DART",
                               "갱신": last_updated("stocks"), "등급": "자동 수집"},
                      "facts": [{"text": f"{p['odm']:.1f}배", "check": {"op": "last", "series": "ODM PER", "value": round(p["odm"], 1)}},
                                {"text": f"{p['brand']:.1f}배", "check": {"op": "last", "series": "브랜드 PER", "value": round(p["brand"], 1)}}]})
    else:
        cards.append(card_nodata("s_per", "ODM이 브랜드보다 덜 할인받고 있나요?", p.get("reason", "주가 수집 전")))
    # ODM 이익률 + 회사별 인당 매출
    o = m_odm(cfg)
    k = o["opm_q"]
    if k and prev_q(k) in o["opm"]:
        now, prev = o["opm"][k], o["opm"][prev_q(k)]
        lvl = judge("odm_opm_ttm", now - prev)
        tail = f"1년 전({prev:.1f}%)과 비슷해요." if lvl == "flat" else f"1년 전({prev:.1f}%)보다 {'올랐어요' if now > prev else '내렸어요'}."
        cards.append({"id": "s_odm", "question": "ODM은 많이 팔수록 더 남기고 있나요?",
                      "answer": f"ODM 영업이익률(TTM)은 {now:.1f}%로 {tail}", "status": status(lvl),
                      "why": "매출이 늘 때 이익률도 오르면 영업 레버리지가 생긴 거예요. 회사마다 차이가 커서 아래 표도 같이 보세요.",
                      "chart": {"unit": "%", "freq": "Q", "events": events("stocks"), "series": [gseries("ODM 영업이익률", qpairs(o["opm"]), "main")]},
                      "table": {"columns": ["회사", "직원(명)", "1년 전", "직원 YoY", "인당 매출(억원)", "1년 전", "인당 매출 YoY"],
                                "rows": [[r[0], r[1], r[2], f"{r[3]:+.1f}%", r[4], r[5], f"{r[6]:+.1f}%"] for r in o["comp_rows"]]},
                      "more": {"정의": "ODM 4사 합산 최근 4분기 영업이익 ÷ 합산 매출(연결). 인당 매출 = 별도 최근 4분기 매출 ÷ 별도 직원 수",
                               "기준": f"영업이익률 ±0.5%p, 표는 {o['per_emp_q']} 대 1년 전", "출처": "DART 정기보고서",
                               "갱신": last_updated("dart"), "등급": "자동 수집"},
                      "facts": [{"text": f"{now:.1f}%", "check": {"op": "last", "series": "ODM 영업이익률", "value": round(now, 1)}}]})
    # 시장 대비 수익률 (1년 전 = 100)
    st = obs("stocks")
    ix = obs("index")
    if not st.empty and not ix.empty:
        px = st[st["지표"] == "종가"].pivot_table(index="기준일", columns="구분", values="값").sort_index()
        px.index = pd.to_datetime(px.index)
        wpx = px.resample("W-FRI").last()
        ix = ix.assign(pri=ix["출처"].str.startswith("금융위").astype(int)).sort_values(["기준일", "pri"]).drop_duplicates(["기준일", "구분"], keep="last")
        q = ix.pivot_table(index="기준일", columns="구분", values="값").sort_index()
        q.index = pd.to_datetime(q.index)
        wq = q.resample("W-FRI").last()
        base = wpx.index[-53] if len(wpx) > 53 else wpx.index[0]
        last_day = min(px.index.max(), q.index.max())

        def grp(g):
            cols = [c["name"] for c in cfg["companies"] if c["group"] == g and not c.get("delisted") and c["name"] in wpx]
            rel = wpx[cols].div(wpx[cols].loc[base]) * 100
            return rel.mean(axis=1)
        series_ = {"ODM (4사 평균)": grp("ODM"), "브랜드 (평균)": grp("브랜드")}
        if "KOSPI" in wq:
            series_["KOSPI"] = wq["KOSPI"] / wq["KOSPI"].loc[base] * 100
        lab = lambda s: {d.strftime("%Y-%m-%d") if d <= last_day else last_day.strftime("%Y-%m-%d"): v for d, v in s.dropna().items() if d >= base - pd.DateOffset(years=2)}  # noqa: E731
        sl = [gseries("ODM (4사 평균)", lab(series_["ODM (4사 평균)"]), "main"), gseries("KOSPI", lab(series_["KOSPI"]))]
        sl += [gseries("브랜드 (평균)", lab(series_["브랜드 (평균)"]), "extra")]
        r_odm = sl[0]["data"][-1][1] - 100
        r_k = sl[1]["data"][-1][1] - 100
        cards.append({"id": "s_rel", "question": "화장품 주가는 시장보다 잘 올랐나요?",
                      "answer": f"최근 1년 ODM 주가(4사 평균)는 {r_odm:+.1f}%, KOSPI는 {r_k:+.1f}%예요.",
                      "status": status(judge("rel_return", r_odm - r_k)),
                      "why": "주가가 시장보다 덜 올랐는데 이익이 늘었다면, 아직 멀티플에 덜 반영됐다는 뜻일 수 있어요.",
                      "chart": {"unit": "지수", "freq": "W", "events": events("stocks"), "series": sl},
                      "more": {"정의": f"{base:%Y-%m-%d} 주가 = 100. 그룹은 종목별 수익률의 단순 평균(동일가중)",
                               "계산": "판정은 ODM 수익률 − KOSPI 수익률(기준 ±5%p)", "출처": "금융위원회 주식시세·지수시세",
                               "갱신": last_updated("stocks"), "등급": "자동 수집"},
                      "facts": [{"text": f"{r_odm:+.1f}%", "check": {"op": "last_minus100", "series": "ODM (4사 평균)", "value": round(r_odm, 1)}},
                                {"text": f"{r_k:+.1f}%", "check": {"op": "last_minus100", "series": "KOSPI", "value": round(r_k, 1)}}]})
    return {"key": "stocks", "question": "화장품 주식이 리레이팅될 근거가 있나요?", "label": "새로 추가", "cards": cards}


# ── 스킨부스터 페이지 ─────────────────────────────────────────────
def booster_page(cfg) -> dict:
    from .axis3 import naver_queries
    cards = []
    upd = last_updated("customs")
    bo = m_booster()
    if bo["ok"]:
        s9018 = monthly_total("9018")
        cards.append({"id": "b_export", "question": "리쥬란 같은 의료기기 수출이 늘고 있나요?",
                      "answer": f"의료기기(9018.90) 수출은 최근 3개월 {usd(bo['cur_sum'])}로, 1년 전보다 {abs(bo['yoy']):.1f}% {word(bo['yoy'])}.",
                      "status": status(judge("booster_yoy", bo["yoy"])),
                      "why": "리쥬란 같은 주사형 의료기기가 이 품목에 들어가요. 다른 의료기기도 섞여 있어 방향만 봐요.",
                      "chart": {"unit": "usd", "freq": "M", "events": events("booster"),
                                "series": [mseries("의료기기 9018.90", bo["raw"], "main"), mseries("의료기기 전체 9018", s9018, "extra")]},
                      "more": {"정의": "HS 코드 9018.90(그 밖의 의료기기) 월 수출액", "계산": "최근 3개월 합 YoY. 기준 ±3%",
                               "출처": "관세청 품목별·국가별 수출입실적", "갱신": upd, "등급": "대용 지표"},
                      "facts": [{"text": usd(bo["cur_sum"]), "check": {"op": "sum3_usd", "series": "의료기기 9018.90"}},
                                {"text": f"{abs(bo['yoy']):.1f}%", "check": {"op": "yoy3_series", "series": "의료기기 9018.90", "value": round(bo["yoy"], 1)}}]})
    else:
        cards.append(card_nodata("b_export", "리쥬란 같은 의료기기 수출이 늘고 있나요?", bo["reason"]))
    # 스킨부스터 검색 (네이버 로우데이터, 한 조회 안에서만 비교)
    qs = [q for q in naver_queries() if "스킨부스터" in q["p"].columns and "30~34" in q["cond"]]
    if qs:
        q = qs[0]
        pz = q["p"].copy()
        pz.index = pd.to_datetime(pz.index)
        sb = pz["스킨부스터"].dropna()
        last = sb.index[-1]
        cur = sb[sb.index > last - pd.Timedelta(days=28)].mean()
        prev = sb[(sb.index > last - pd.DateOffset(years=1) - pd.Timedelta(days=28)) & (sb.index <= last - pd.DateOffset(years=1))].mean()
        ch = (cur / prev - 1) * 100 if prev else None
        def w(c):
            return {d.strftime("%Y-%m-%d"): v for d, v in pz[c].dropna().items()}
        sl = [gseries("스킨부스터", w("스킨부스터"), "main")] + [gseries(c, w(c)) for c in ("보톡스", "필러") if c in pz]
        sl += [gseries(c, w(c), "extra") for c in pz.columns if c not in ("스킨부스터", "보톡스", "필러")]
        cards.append({"id": "b_search", "question": "스킨부스터를 찾아보는 사람이 늘었나요?",
                      "answer": (f"네이버 '스킨부스터' 검색은 최근 4주 평균이 1년 전보다 {abs(ch):.1f}% {word(ch)}(30~34세 조회)." if ch is not None
                                 else no_data("1년 전 같은 기간 값 없음")),
                      "status": status(judge("search_yoy", ch)),
                      "why": "보톡스·필러와 한 조회에서 비교한 값이라 셋의 크기를 견줄 수 있어요. 다른 연령대와는 비교할 수 없어요.",
                      "chart": {"unit": "지수", "freq": "W", "events": events("booster"), "series": sl},
                      "more": {"정의": "네이버 데이터랩 검색지수(그 조회 안의 최댓값 = 100), 일간 값을 주간 평균으로", "기간": f"~{last:%Y-%m-%d} (수동 입력)",
                               "출처": q["src"], "갱신": "월 1회 수동 (README '네이버 검색 트렌드')", "등급": "수동 입력"}})
    else:
        cards.append(card_nodata("b_search", "스킨부스터를 찾아보는 사람이 늘었나요?", "네이버 데이터랩 엑셀 입력 전"))
    prod = [q for q in naver_queries() if set(cfg["naver_manual"]["booster_products"]) & set(q["p"].columns)]
    if not prod:
        cards.append(card_nodata("b_products", "리쥬란·쥬베룩 같은 제품별 관심은 어때요?",
                                 "제품 키워드로 조회한 네이버 데이터랩 엑셀이 아직 없어요 — 도움말의 수동 입력 안내 참고"))
    tr = obs("tourism")
    if tr.empty:
        cards.append(card_nodata("b_tourism", "한국에 오는 외국인이 늘고 있나요?", "출입국관광통계 사용 승인이 아직 반영되지 않아 매일 다시 시도하고 있어요"))
    # 환율
    fx = obs("fx")
    if not fx.empty:
        fx = fx.assign(pri=fx["출처"].str.startswith("한국은행").astype(int)).sort_values(["기준일", "pri"]).drop_duplicates(["기준일", "구분"], keep="last")
        pv = fx.pivot_table(index="기준일", columns="구분", values="값").sort_index()
        pv.index = pd.to_datetime(pv.index)
        usdk = pv["원/달러"].dropna()
        now_d = usdk.index[-1]
        prev_s = usdk[usdk.index <= now_d - pd.DateOffset(years=1)]
        now, prev = float(usdk.iloc[-1]), float(prev_s.iloc[-1])
        chg = (now / prev - 1) * 100
        rows = []
        for c in ("원/달러", "원/유로", "원/100엔", "원/위안"):
            s_ = pv[c].dropna()
            p_ = s_[s_.index <= s_.index[-1] - pd.DateOffset(years=1)]
            rows.append([c, f"{s_.iloc[-1]:,.1f}원", f"{p_.iloc[-1]:,.1f}원", f"{(s_.iloc[-1] / p_.iloc[-1] - 1) * 100:+.1f}%"])
        cards.append({"id": "b_fx", "question": "환율은 수출 회사에 유리한가요?",
                      "answer": f"원/달러 환율은 {now:,.0f}원으로 1년 전({prev:,.0f}원)보다 {abs(chg):.1f}% {'올랐어요(원화 약세)' if chg > 0 else '내렸어요(원화 강세)'}.",
                      "status": status(judge("fx_change", chg)),
                      "why": "파마리서치처럼 해외 매출이 많은 회사는 원화가 강해지면 원화로 받는 매출이 줄 수 있어요.",
                      "chart": {"unit": "원", "freq": "D", "events": events("booster"),
                                "series": [gseries("원/달러", {d.strftime("%Y-%m-%d"): v for d, v in usdk.items()}, "main")]},
                      "table": {"columns": ["통화", "지금", "1년 전", "변화"], "rows": rows},
                      "more": {"정의": "매매기준율(한국은행 ECOS). 환율이 오르면 원화 약세", "계산": "판정은 원/달러 1년 변화(기준 ±3%, 오르면 수출 회사에 유리한 쪽)",
                               "출처": "한국은행 ECOS 731Y001", "갱신": last_updated("fx"), "등급": "자동 수집"},
                      "facts": [{"text": f"{now:,.0f}원", "check": {"op": "last_fmt0", "series": "원/달러", "value": round(now)}}]})
    return {"key": "booster", "question": "리쥬란 같은 스킨부스터 시장은 어때요?", "label": "새로 추가", "cards": cards}


# ── 도움말 ───────────────────────────────────────────────────────
UNKNOWNS = [
    ("현지에서 실제로 팔린 양", "관세청 수출은 배에 실린 양이라, 현지에서 실제로 팔린 양과는 달라요. 창고에 쌓여 있을 수도 있어요."),
    ("ODM이 받는 가격(출고단가)", "ODM과 브랜드사 사이의 계약 가격은 어디에도 공개되지 않아요. 1kg당 수출단가는 소비자 가격·제품 구성이 섞인 값이에요."),
    ("주문을 얼마나 미리 받는지(선주문 기간)", "수주잔고는 공시되지 않아요. 회사 설명회에서만 들을 수 있어요."),
    ("판매채널 수수료", "올리브영·아마존·세포라가 가져가는 몫은 공개되지 않아요."),
    ("브랜드사가 ODM에 가격을 깎아 달라고 하는지", "협상 내용은 볼 수 없어요. 판관비율과 ODM 이익률을 함께 보며 짐작만 할 수 있어요."),
    ("수출단가가 오른 이유", "가격을 올렸는지, 비싼 제품이 많이 팔렸는지, 포장이 가벼워졌는지 나눠 볼 수 없어요."),
    ("선크림 수출액", "선크림만 따로 세는 품목 번호가 없어서 '기타'에 섞여 있어요."),
    ("비상장 회사 실적", "구다이글로벌·CJ올리브영처럼 상장하지 않은 회사는 분기 실적이 공개되지 않아요."),
    ("틱톡샵 매출", "플랫폼 매출은 공개되지 않아요. 입점 기사 수만 셀 수 있어요."),
    ("미국 메드스파가 늘어난 이유", "메드스파는 업종 통계가 따로 없어 개수조차 공식 숫자가 없어요."),
    ("검색량 = 구매량?", "검색·조회수는 관심을 대신 재는 값이에요. 실제로 산 사람 수와는 달라요."),
    ("아마존 순위 = 미국 전체 판매?", "아마존 한 곳의 순위예요. 매장 판매나 다른 온라인몰은 들어 있지 않아요."),
]

MANUAL_FILES = [
    ("manual/consensus_eps.csv", "컨센서스 EPS (12개월 선행)", "월 1회", "기준월"),
    ("manual/reinvest.csv", "CAPA·가동률·연구개발비", "반기·연간", "기준일"),
    ("manual/company_quarterly.csv", "연우(2025~)·CJ올리브영 분기 실적", "분기 1회", "분기"),
    ("manual/global_peers.csv", "해외 경쟁사(인터코스 등)", "월 1회", "기준일"),
    ("manual/store_events.csv", "입점 이벤트 로그", "수시", "날짜"),
    ("manual/google_trends.csv", "Google Trends", "월 1회", "기준월"),
    ("manual/events.csv", "그래프 사건 세로선", "수시", "날짜"),
    ("config/brand_master.csv", "아마존 브랜드 국적·생산지 판정", "미분류가 생길 때", None),
]


def help_page(story: dict) -> dict:
    texts = {"홈": [story["home"]["summary"]] + [f"{c['question']} {c['answer']} {c.get('sub', '')}" for c in story["home"]["cards"]]}
    names = {"exports": "수출", "consumers": "해외 소비자", "chain": "밸류체인", "stocks": "주가", "booster": "스킨부스터"}
    for k, p in story["pages"].items():
        t = [p["question"]]
        for c in p["cards"]:
            t += [c["question"], c["answer"], c.get("why", ""), c.get("sub", "")] + [str(v) for v in (c.get("more") or {}).values()]
            t += [str(x) for r in (c.get("table") or {}).get("rows", []) for x in r if not isinstance(x, dict)]
        texts[names[k]] = t
    rows = []
    for g in story["glossary"]:
        where = [pg for pg, ts in texts.items() if any(any(a in t for a in [g["term"], *g["alias"]]) for t in ts)]
        rows.append([g["term"], g["desc"], g["ex"], ", ".join(where) or "–"])
    man = []
    for path, what, cadence, col in MANUAL_FILES:
        f = ROOT / path
        n, last = 0, ""
        if f.exists():
            with f.open(encoding="utf-8-sig", newline="") as fh:
                rr = [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]
            n = len(rr)
            if col and rr:
                last = max((r.get(col) or "").strip() for r in rr)
        man.append([what, path, cadence, f"{n}행", last or ("아직 입력 없음" if not n else "–")])
    from .axis3 import naver_queries
    nq = naver_queries()
    man.append(["네이버 검색 트렌드(데이터랩 엑셀)", "raw/*.xlsx → data/obs/naver_excel.csv", "월 1회", f"조회 {len(nq)}개",
                max((q["p"].index.max() for q in nq), default="아직 입력 없음")])
    return {"glossary": {"columns": ["용어", "쉬운 뜻", "비유·예시", "이 사이트 어디에 나오는지"], "rows": rows},
            "unknowns": [{"q": a, "a": b} for a, b in UNKNOWNS],
            "manual": {"columns": ["항목", "파일", "입력 주기", "행 수", "마지막 입력"], "rows": man}}
