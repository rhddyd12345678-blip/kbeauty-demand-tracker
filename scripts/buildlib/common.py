"""빌드 공통: 관측값 읽기(키별 최신 수집일), 차트 명세, 분기 처리."""
from __future__ import annotations

import csv
import math
from datetime import date

import pandas as pd

from lib.core import MANUAL, OBS, load_status

GRADES = {"auto": "자동 수집", "manual": "수동 입력", "proxy": "대용 지표", "none": "데이터 없음 — IR·전화 확인 영역"}


def obs(name: str) -> pd.DataFrame:
    """수집일 기준 최신 값만 남긴 긴 형식 DataFrame. 파일이 없으면 빈 DataFrame."""
    f = OBS / f"{name}.csv"
    cols = ["기준일", "축", "지표", "구분", "값", "단위", "출처", "수집일"]
    if not f.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(f, dtype=str, encoding="utf-8-sig").fillna("")
    df = df.drop_duplicates(subset=["기준일", "지표", "구분", "출처"], keep="last")
    df["값"] = pd.to_numeric(df["값"], errors="coerce")
    return df.dropna(subset=["값"])


def manual(name: str) -> pd.DataFrame:
    f = MANUAL / f"{name}.csv"
    if not f.exists():
        return pd.DataFrame()
    with f.open(encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]
    return pd.DataFrame(rows)


def clean(v):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, 4)


def series(name: str, s: pd.Series, **extra) -> dict:
    """s: index=날짜 문자열(YYYY-MM-DD), 값. None/NaN은 빠진다."""
    data = [[str(k), clean(v)] for k, v in s.items() if clean(v) is not None]
    return {"name": name, "data": data, **extra}


def chart(cid: str, title: str, *, grade: str, unit: str, freq: str, series_list: list[dict], source: str,
          collectors: list[str] = (), note: str = "", yoy: str = "pct", kind: str = "line", marks: list[dict] = (),
          empty_reason: str = "", sample: str = "") -> dict:
    """프런트가 그대로 그리는 차트 명세.
    freq: D·W·M·Q·Y (툴팁 날짜 표기와 YoY 비교 간격)
    yoy: 'pct'(증감률) | 'diff'(차이, %p 등) | '' (전환 버튼 숨김)
    empty_reason: 시리즈가 비었을 때 '수집 불가' 사유 (없으면 수집기 상태에서 가져옴)
    """
    has = any(s["data"] for s in series_list)
    status = load_status()
    reason = empty_reason
    if not has and not reason:
        msgs = [f"{c}: {status[c]['메모']}" for c in collectors if c in status and status[c]["상태"] != "정상"]
        reason = "; ".join(msgs) or "아직 수집된 값 없음"
    last = max((s["data"][-1][0] for s in series_list if s["data"]), default="")
    updated = max((status.get(c, {}).get("최근성공", "") for c in collectors), default="")
    return {"id": cid, "title": title, "grade": grade, "gradeLabel": GRADES[grade], "unit": unit, "freq": freq,
            "series": [s for s in series_list if s["data"]] if has else [], "source": source, "note": note,
            "yoy": yoy, "kind": kind, "marks": list(marks), "empty": "" if has else reason, "sample": sample,
            "lastPoint": last, "updated": updated}


def card(cid: str, title: str, grade: str, body: str, *, memo: str = "", source: str = "") -> dict:
    """그래프 없이 설명만 있는 카드 (데이터 없음 등)."""
    return {"id": cid, "title": title, "grade": grade, "gradeLabel": GRADES[grade], "card": True, "body": body,
            "memo": memo, "source": source}


# ── 분기 ──────────────────────────────────────────────────────────
def q_date(p: str) -> str:
    """'2025Q3' → '2025-07-01' (분기 첫날). 프런트는 freq=Q로 '2025 3Q'라고 표기."""
    y, q = int(p[:4]), int(p[-1])
    return f"{y}-{(q - 1) * 3 + 1:02d}-01"


def standalone_quarters(vals: dict[str, float]) -> dict[str, float]:
    """{'2025Q1':..,'2025Q2':..,'2025Q3':..,'2025FY':..} → 분기 단독 {'2025Q1'..'2025Q4'}.
    4Q = 연간 − (1Q+2Q+3Q), 세 분기가 모두 있을 때만."""
    out = {k: v for k, v in vals.items() if not k.endswith("FY")}
    for k, v in vals.items():
        if k.endswith("FY"):
            y = k[:4]
            qs = [vals.get(f"{y}Q{i}") for i in (1, 2, 3)]
            if all(x is not None for x in qs):
                out[f"{y}Q4"] = v - sum(qs)
    return dict(sorted(out.items()))


def ttm(q: dict[str, float]) -> dict[str, float]:
    """연속된 4개 분기 합."""
    keys = sorted(q)
    out = {}
    for i, k in enumerate(keys):
        y, n = int(k[:4]), int(k[-1])
        need = []
        for j in range(4):
            yy, nn = y, n - j
            while nn <= 0:
                yy, nn = yy - 1, nn + 4
            need.append(f"{yy}Q{nn}")
        if all(x in q for x in need):
            out[k] = sum(q[x] for x in need)
    return out


def available_from(p: str) -> date:
    """분기 실적을 시장이 알 수 있게 되는 날(법정 제출기한 기준): 1Q 5/15, 2Q 8/14, 3Q 11/14, 4Q 다음해 3/31."""
    y, q = int(p[:4]), int(p[-1])
    return {1: date(y, 5, 15), 2: date(y, 8, 14), 3: date(y, 11, 14), 4: date(y + 1, 3, 31)}[q]


def yoy_pct(q: dict[str, float]) -> dict[str, float]:
    out = {}
    for k, v in q.items():
        prev = q.get(f"{int(k[:4]) - 1}{k[4:]}")
        if prev and prev > 0:
            out[k] = (v / prev - 1) * 100
    return out


def direction(values: list[float], *, lag: int, tol: float = 0.0) -> tuple[str, float | None]:
    """마지막 값과 lag 전 값 비교 → ('상승'|'하락'|'보합'|'판단 불가', 변화량)."""
    vals = [v for v in values if v is not None]
    if len(vals) <= lag:
        return "판단 불가", None
    d = vals[-1] - vals[-1 - lag]
    if abs(d) <= tol:
        return "보합", d
    return ("상승" if d > 0 else "하락"), d
