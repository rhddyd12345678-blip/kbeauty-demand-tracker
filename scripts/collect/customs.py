"""관세청 품목별·국가별 수출 (월) — 공공데이터포털 '관세청_품목별 국가별 수출입실적(GW)'

GET https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList
  serviceKey, strtYymm, endYymm (최대 1년), hsSgn (2·4·6·10단위), cntyCd (생략 시 전체 국가)
응답 XML item: year('YYYY.MM' | '총계'), statCd(국가), statCdCntnKor1(국가명), hsCd, statKor(품목명),
               expDlr(수출 달러), expWgt(수출 kg), impDlr, impWgt, balPayments

API는 요청 코드의 한 단계 아래 하위 코드별로 행을 돌려준다(3304 → 330410…330499, 330499 → 3304991000…).
저장: 지표 '수출액'(USD)·'수출중량'(kg), 구분 '실제HS|국가코드|국가명'. config customs.queries의 store가
  children이면 하위 코드별로, total이면 요청 코드로 합산해 한 행. 값이 0인 행은 저장하지 않는다(없음 = 0).
상위 코드 합계·단가·비중·HHI는 build에서 계산(한 단계만 합산해 이중 집계 방지).
첫 실행은 backfill_start부터, 이후엔 최근 13개월만 다시 받아 잠정치 정정을 반영(값이 바뀐 경우만 새 행).
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import CollectError, MissingKey, append_obs, check_gateway, config, http_get, need_key, now_kst, read_obs  # noqa: E402

NAME = "customs"
API = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"
SOURCE = "관세청 수출입무역통계(공공데이터포털)"


def key() -> str:
    k = need_key("DATA_GO_KR_KEY")
    return urllib.parse.unquote(k) if "%" in k else k  # 포털 '인코딩' 키를 넣어도 동작


def fetch(k: str, hs: str, start: str, end: str) -> list[dict]:
    params = {"serviceKey": k, "strtYymm": start, "endYymm": end, "hsSgn": hs}
    r = http_get(API, params=params, timeout=90)
    text = r.text.strip()
    check_gateway(text)
    if r.status_code != 200:
        raise CollectError(f"HTTP {r.status_code}: {text[:120]}")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise CollectError(f"XML 아님: {text[:120]}")
    auth = root.findtext(".//returnAuthMsg")
    if auth:
        raise CollectError(f"인증 오류 {auth} — 활용신청 승인/반영(1~2시간) 여부 확인")
    code = root.findtext(".//header/resultCode")
    if code not in (None, "00"):
        raise CollectError(f"resultCode={code} {root.findtext('.//header/resultMsg')}")
    out = []
    for it in root.iter("item"):
        d = {c.tag: (c.text or "").strip() for c in it}
        if not d.get("year") or "총계" in d["year"] or not d.get("statCd"):
            continue
        out.append(d)
    return out


def chunks(start: str, end: str):
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1):
        a = start if y == y0 else f"{y}01"
        b = end if y == y1 else f"{y}12"
        yield a, b


def run() -> tuple[int, str]:
    cfg = config()
    k = key()
    now = now_kst()
    end = f"{now.year}{now.month:02d}"
    # 이미 받은 요청 코드: 저장된 실제 코드가 요청 코드로 시작하면 받은 것으로 본다
    stored = {r["구분"].split("|")[0] for r in read_obs(NAME)}
    have = {hs for hs in cfg["customs"]["queries"] if any(c.startswith(hs) for c in stored)}
    refresh_from = f"{now.year - 1}{now.month:02d}"
    rows, errors, last_month = [], [], ""
    first: dict[str, str] = {}
    for r in read_obs(NAME):
        c = r["구분"].split("|")[0]
        for hs in cfg["customs"]["queries"]:
            if c.startswith(hs):
                first[hs] = min(first.get(hs, "999999"), r["기준일"].replace("-", ""))
    long_start = cfg.get("long_backfill_start", cfg["backfill_start"])
    for hs, q in cfg["customs"]["queries"].items():
        ranges = []
        if hs in have:
            ranges.append((refresh_from, end))
            if first.get(hs, long_start) > long_start:  # 시작월을 앞당겼으면 그 앞 구간도
                f = first[hs]
                prev = f"{int(f[:4]) - 1}12" if f[4:] == "01" else f"{f[:4]}{int(f[4:]) - 1:02d}"
                ranges.append((long_start, prev))
        else:
            ranges.append((long_start, end))
        for a, b in [c for r_ in ranges for c in chunks(*r_)]:
            try:
                items = fetch(k, hs, a, b)
            except MissingKey:
                raise  # 키 문제면 나머지도 전부 실패하므로 바로 중단
            except CollectError as e:
                errors.append(f"{hs} {a}: {e}")
                continue
            agg: dict[tuple, list[float]] = {}
            for d in items:
                ym = d["year"].replace(".", "-")[:7]
                last_month = max(last_month, ym)
                code = d.get("hsCd") if q.get("store") == "children" and d.get("hsCd", "-") != "-" else hs
                kk = (ym, code, d["statCd"], d.get("statCdCntnKor1", ""))
                v = agg.setdefault(kk, [0.0, 0.0])
                v[0] += float((d.get("expDlr") or "0").replace(",", "") or 0)
                v[1] += float((d.get("expWgt") or "0").replace(",", "") or 0)
            for (ym, code, cc, nm), (dlr, wgt) in agg.items():
                g = f"{code}|{cc}|{nm}"
                if dlr:
                    rows.append({"기준일": ym, "축": "수출", "지표": "수출액", "구분": g, "값": dlr, "단위": "USD", "출처": SOURCE})
                if wgt and q.get("weight"):
                    rows.append({"기준일": ym, "축": "수출", "지표": "수출중량", "구분": g, "값": wgt, "단위": "kg", "출처": SOURCE})
            time.sleep(0.3)
    if not rows and errors:
        raise CollectError("; ".join(errors[:3]))
    added = append_obs(NAME, rows)
    note = f"~{last_month}" + (f", 일부 실패 {len(errors)}건: {errors[0]}" if errors else "")
    return added, note


if __name__ == "__main__":
    from lib.core import load_env
    load_env()
    print(run())
