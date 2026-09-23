"""화장품 산업 핵심 지표 수집 → data/comtrade.json (원자료), data/fx.json

1) UN Comtrade 공개 미리보기 API (키 불필요, 1회 1개월)
   - 미국·일본의 화장품(HS 3304) 수입 국가별 월간 → 수입시장 내 한국 점유율·순위 (한국 vs 프랑스)
   - 미국의 HS 3002.49(보툴리눔 톡신 등) 수입 국가별 → 한국산 톡신 미국 수출 대리지표
   - 한국의 3304·300249 수출 상대국별 (한국은 Comtrade 반영이 수개월 늦음)
   처음 실행 시 START 부터 백필(수십 분), 이후엔 누락 월 + 최근 REFRESH 개월만 다시 받음.
2) 환율 (ECB 기준, frankfurter.app): USD·CNY·JPY 대비 원화
사용: python scripts/fetch_industry.py
"""
from __future__ import annotations

import time
from datetime import date

import requests

from common import load_json, save_json, update_meta, now_kst

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
START = (2019, 1)
REFRESH = 4          # 최근 N개월은 수정치 반영 위해 재수집
MAX_EMPTY_TAIL = 3   # 최신 월에서 연속 N개월 비면 아직 미공표로 보고 중단

# (보고국, 흐름, HS) 조합. 키 = "보고국|흐름"
SERIES = {
    "842|M": {"name": "미국 수입", "cmd": "3304,300249"},
    "392|M": {"name": "일본 수입", "cmd": "3304"},
    "410|X": {"name": "한국 수출", "cmd": "3304,300249"},
}


def months(start: tuple[int, int], end: date) -> list[str]:
    y, m = start
    out = []
    while (y, m) <= (end.year, end.month):
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch_month(rep: str, flow: str, cmd: str, period: str) -> dict | None:
    """{cmd: {partnerCode: USD}} 또는 데이터 없으면 None"""
    url = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
    params = {"reporterCode": rep, "period": period, "cmdCode": cmd, "flowCode": flow}
    rows = None
    for attempt in range(8):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            if r.status_code == 429:  # 요청 제한 → 서버가 알려준 시간만큼 대기
                time.sleep(float(r.headers.get("Retry-After") or 2) + 0.5 * attempt)
                continue
            r.raise_for_status()
            rows = r.json().get("data") or []
            break
        except Exception as e:
            print(f"[warn] {rep}|{flow} {period}: {e}")
            time.sleep(5)
    if rows is None:
        return "error"
    if not rows:
        return None
    out: dict[str, dict[str, float]] = {}
    for d in rows:
        # 합계 행만 (관세제도·운송수단·2차 상대국 구분 없는 행)
        if d.get("customsCode") not in (None, "C00") or d.get("motCode") not in (None, 0) or d.get("partner2Code") not in (None, 0):
            continue
        out.setdefault(d["cmdCode"], {})[str(d["partnerCode"])] = d.get("primaryValue")
    return out


def update_series(key: str, spec: dict, store: dict) -> tuple[str, int]:
    rep, flow = key.split("|")
    data = store.setdefault(key, {})
    allm = months(START, date.today())
    have = sorted(p for p, v in data.items() if v)
    # 누락 월 + 최근 REFRESH 개월(데이터 있는 마지막 월 기준)
    last = have[-1] if have else None
    todo = [p for p in allm if p not in data or not data[p]]
    if last:
        todo += [p for p in allm if p <= last][-REFRESH:]
    todo = sorted(set(todo))
    got, empty_tail = 0, 0
    for p in todo:
        # 최신 미공표 구간: 마지막 보유월 이후 연속 공백이면 중단
        if last and p > last and empty_tail >= MAX_EMPTY_TAIL:
            break
        res = fetch_month(rep, flow, spec["cmd"], p)
        if res == "error":
            continue
        if res is None:
            if last and p > last:
                empty_tail += 1
            continue
        data[p] = res
        got += 1
        if got % 12 == 0:  # 중간 저장 (긴 백필 중 실패 대비)
            save_json("comtrade.json", store)
        time.sleep(1.0)
    return key, got


def fetch_fx() -> dict:
    old = load_json("fx.json", {})
    start = "2019-01-01"
    if old.get("dates"):
        start = old["dates"][-30] if len(old["dates"]) > 30 else old["dates"][0]
    r = requests.get(f"https://api.frankfurter.app/{start}..", params={"from": "USD", "to": "KRW,CNY,JPY"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    rates = r.json()["rates"]
    merged = {d: v for d, v in zip(old.get("dates", []), zip(old.get("usdkrw", []), old.get("cnykrw", []), old.get("jpykrw", [])))}
    for d, v in rates.items():
        usdkrw = v["KRW"]
        merged[d] = (round(usdkrw, 2), round(usdkrw / v["CNY"], 2), round(usdkrw / v["JPY"] * 100, 2))
    ds = sorted(merged)
    return {"dates": ds, "usdkrw": [merged[d][0] for d in ds], "cnykrw": [merged[d][1] for d in ds],
            "jpykrw": [merged[d][2] for d in ds], "note": "JPY는 100엔당 원화"}


def main() -> None:
    store = load_json("comtrade.json", {})
    results = []
    for key, spec in SERIES.items():  # 순차 실행 (공개 API 요청 제한)
        try:
            results.append(update_series(key, spec, store))
        except Exception as e:
            print(f"[warn] {key}: {e}")
            results.append((key, 0))
    store["_updated"] = now_kst()
    save_json("comtrade.json", store)
    notes = []
    for key, got in results:
        if key not in store:
            continue
        have = sorted(p for p, v in store[key].items() if v)
        notes.append(f"{SERIES[key]['name']} ~{have[-1][:4]}-{have[-1][4:]}" if have else f"{SERIES[key]['name']} 없음")
        print(f"{key}: +{got}개월, 보유 {len(have)}개월")
    update_meta("comtrade", "ok", ", ".join(notes))
    try:
        fx = fetch_fx()
        save_json("fx.json", fx)
        update_meta("fx", "ok", f"~{fx['dates'][-1]}")
    except Exception as e:
        print(f"[warn] fx: {e}")
        update_meta("fx", "error", str(e)[:80])


if __name__ == "__main__":
    main()
