"""방한 외국인 국적별 월별 (월) — 한국문화관광연구원 출입국관광통계 (공공데이터포털 활용신청 필요)

두 버전을 차례로 시도하고, 통과한 주소를 data/tourism_endpoint.txt에 적어 다음부터 그쪽을 먼저 쓴다(고정).
  ① _GW 버전(15158830): https://apis.data.go.kr/B551940/EdrcntTourismStatsService/getEdrcntTourismStatsList
  ② 원래 버전(15000297): http://openapi.tour.go.kr/openapi/service/EdrcntTourismStatsService/getEdrcntTourismStatsList
  serviceKey, YM(YYYYMM), ED_CD=E(방한), NAT_CD(생략 시 전체 국적)
응답 item: ym, natCd, natKorNm(국적), num(인원), ed
국적명은 config/tracker.yml의 tourism.nationalities와 같은 이름만 저장하고, 전체 합계도 함께 저장.
통상 다음 달 말쯤 공표 → 최근 3개월은 매번 다시 받는다.
"""
from __future__ import annotations

import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import DATA, CollectError, MissingKey, append_obs, check_gateway, config, http_get, month_range, need_key, now_kst, read_obs  # noqa: E402

NAME = "tourism"
ENDPOINTS = {
    "GW(15158830)": "https://apis.data.go.kr/B551940/EdrcntTourismStatsService/getEdrcntTourismStatsList",
    "원래(15000297)": "http://openapi.tour.go.kr/openapi/service/EdrcntTourismStatsService/getEdrcntTourismStatsList",
}
PINNED = DATA / "tourism_endpoint.txt"
SOURCE = "한국문화관광연구원 출입국관광통계(공공데이터포털)"


def fetch(key: str, ym: str, api: str) -> list[dict]:
    r = http_get(api, params={"serviceKey": key, "YM": ym, "ED_CD": "E", "numOfRows": 500, "pageNo": 1})
    text = r.text.strip()
    if text.startswith("{"):  # GW 버전이 JSON으로 줄 때
        check_gateway(text)
        body = r.json().get("response", {}).get("body", {})
        items = (body.get("items") or {}).get("item", []) if isinstance(body.get("items"), dict) else []
        return [items] if isinstance(items, dict) else items
    check_gateway(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise CollectError(f"XML 아님: {text[:150]}")
    msg = root.findtext(".//returnAuthMsg") or root.findtext(".//faultstring")
    if msg:
        raise CollectError(f"인증/요청 오류: {msg[:100]} — 활용신청 승인 여부 확인")
    code = root.findtext(".//resultCode")
    if code in ("30", "31", "32", "20", "22"):
        raise CollectError(f"인증 오류 {code} {root.findtext('.//resultMsg')} — 활용신청 승인/반영 여부 확인")
    if code not in (None, "0000", "00"):
        raise CollectError(f"resultCode={code} {root.findtext('.//resultMsg')}")
    return [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]


def run() -> tuple[int, str]:
    cfg = config()
    k = need_key("DATA_GO_KR_KEY")
    key = urllib.parse.unquote(k) if "%" in k else k
    wanted = set(cfg["tourism"]["nationalities"])
    # 주소 고르기: 고정된 쪽 먼저, 인증 오류면 다음 주소
    order = list(ENDPOINTS)
    if PINNED.exists() and PINNED.read_text().strip() in ENDPOINTS:
        order.remove(PINNED.read_text().strip())
        order.insert(0, PINNED.read_text().strip())
    probe = f"{now_kst().year - 1}{now_kst().month:02d}"
    api_name, errs = None, []
    for name in order:
        try:
            fetch(key, probe, ENDPOINTS[name])
            api_name = name
            break
        except MissingKey as e:
            errs.append(f"{name}: {e}")
    if not api_name:
        raise MissingKey("두 버전 모두 " + " / ".join(errs))
    PINNED.write_text(api_name)
    api = ENDPOINTS[api_name]
    now = now_kst()
    have = {r["기준일"] for r in read_obs(NAME)}
    months = month_range(cfg["backfill_start"], f"{now.year}{now.month:02d}")
    recent = set(months[-4:])
    rows, empty, last = [], [], ""
    for ym in months:
        label = f"{ym[:4]}-{ym[4:]}"
        if label in have and ym not in recent:
            continue
        items = fetch(key, ym, api)
        if not items:
            empty.append(label)
            continue
        total = 0
        for it in items:
            n = float((it.get("num") or "0").replace(",", ""))
            total += n
            if it.get("natKorNm") in wanted:
                rows.append({"기준일": label, "축": "개별지표", "지표": "방한 외국인", "구분": it["natKorNm"], "값": n,
                             "단위": "명", "출처": SOURCE})
        rows.append({"기준일": label, "축": "개별지표", "지표": "방한 외국인", "구분": "전체", "값": total,
                     "단위": "명", "출처": SOURCE})
        last = max(last, label)
        time.sleep(0.3)
    if not rows and len(empty) == len(months):
        raise CollectError("모든 달이 빈 응답 — NAT_CD 필수 여부 확인 필요")
    return append_obs(NAME, rows), f"{api_name} 주소 사용, ~{last}" + (f", 미공표 {empty[-1]}" if empty else "")


if __name__ == "__main__":
    from lib.core import load_env
    load_env()
    print(run())
