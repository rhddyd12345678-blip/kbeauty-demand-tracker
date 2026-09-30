"""UN Comtrade — 미국·일본 화장품(HS 3304) 수입의 국가별 구성, 미국 HS 3002.49(보툴리눔 톡신 등) 수입 (월)
(기존 kbeauty-demand-tracker의 fetch_industry.py에서 이관. 한국 수출 상대국 시리즈는 관세청과 중복이라 제외)

GET https://comtradeapi.un.org/public/v1/preview/C/M/HS  reporterCode, period(YYYYMM), cmdCode, flowCode (키 불필요)
요청당 1개월. 429면 Retry-After만큼 대기. 보고국 공표가 1~2개월 늦어 최근 4개월은 매번 다시 받는다.
저장: 지표 '수입액'(USD), 구분 '보고국|HS|상대국코드|상대국명', 기준일 YYYY-MM.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.core import CollectError, append_obs, http_get, month_range, now_kst, read_obs  # noqa: E402

NAME = "comtrade"
API = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
SOURCE = "UN Comtrade 공개 API"
START = "201901"
REFRESH = 4
MAX_EMPTY_TAIL = 3
SERIES = {"미국": ("842", "M", ["3304", "300249"]), "일본": ("392", "M", ["3304"])}

# M49 국가코드 → 이름 (기존 트래커 build_summary.py의 표)
PARTNERS = {
    "0": "전체", "410": "한국", "251": "프랑스", "842": "미국", "392": "일본", "156": "중국", "124": "캐나다",
    "380": "이탈리아", "826": "영국", "276": "독일", "724": "스페인", "756": "스위스", "757": "스위스", "344": "홍콩", "704": "베트남",
    "764": "태국", "490": "대만", "458": "말레이시아", "702": "싱가포르", "643": "러시아", "699": "인도",
    "360": "인도네시아", "608": "필리핀", "36": "호주", "784": "UAE", "528": "네덜란드", "56": "벨기에",
    "616": "폴란드", "398": "카자흐스탄", "76": "브라질", "372": "아일랜드", "484": "멕시코", "792": "튀르키예",
    "682": "사우디", "376": "이스라엘", "752": "스웨덴", "40": "오스트리아", "208": "덴마크", "554": "뉴질랜드",
    "446": "마카오", "496": "몽골", "860": "우즈베키스탄", "417": "키르기스스탄", "112": "벨라루스", "804": "우크라이나",
    "203": "체코", "348": "헝가리", "620": "포르투갈", "300": "그리스", "32": "아르헨티나", "152": "칠레", "170": "콜롬비아",
    "604": "페루", "710": "남아공", "818": "이집트", "566": "나이지리아", "414": "쿠웨이트", "634": "카타르",
    "512": "오만", "48": "바레인", "400": "요르단", "422": "레바논", "586": "파키스탄", "50": "방글라데시",
    "144": "스리랑카", "104": "미얀마", "116": "캄보디아", "418": "라오스", "268": "조지아", "51": "아르메니아",
    "31": "아제르바이잔", "498": "몰도바", "233": "에스토니아", "428": "라트비아", "440": "리투아니아",
    "246": "핀란드", "579": "노르웨이", "703": "슬로바키아", "705": "슬로베니아", "191": "크로아티아",
    "642": "루마니아", "100": "불가리아", "688": "세르비아", "196": "키프로스", "470": "몰타", "442": "룩셈부르크",
}


def fetch_month(rep: str, flow: str, cmd: str, period: str):
    r = None
    for attempt in range(6):
        r = http_get(API, params={"reporterCode": rep, "period": period, "cmdCode": cmd, "flowCode": flow}, tries=1)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After") or 2) + 0.5 * attempt)
            continue
        break
    if r is None or r.status_code != 200:
        raise CollectError(f"HTTP {getattr(r, 'status_code', '?')}")
    rows = r.json().get("data") or []
    out = []
    for d in rows:  # 합계 행만 (관세제도·운송수단·2차 상대국 구분 없는 행)
        if d.get("customsCode") not in (None, "C00") or d.get("motCode") not in (None, 0) or d.get("partner2Code") not in (None, 0):
            continue
        if d.get("primaryValue") is not None:
            out.append((str(d["cmdCode"]), str(d["partnerCode"]), float(d["primaryValue"])))
    return out


def run() -> tuple[int, str]:
    now = now_kst()
    allm = month_range(START, f"{now.year}{now.month:02d}")
    have: dict[str, set] = {}
    for r in read_obs(NAME):
        rep = r["구분"].split("|")[0]
        have.setdefault(rep, set()).add(r["기준일"].replace("-", ""))
    rows, notes, errs = [], [], 0
    for rep_name, (rep, flow, cmds) in SERIES.items():
        got_m = sorted(have.get(rep_name, set()))
        last = got_m[-1] if got_m else None
        todo = [p for p in allm if p not in have.get(rep_name, set())]
        if last:
            todo += [p for p in allm if p <= last][-REFRESH:]
        todo = sorted(set(todo))
        empty_tail, newest = 0, last
        for p in todo:
            if last and p > last and empty_tail >= MAX_EMPTY_TAIL:
                break
            try:
                data = fetch_month(rep, flow, ",".join(cmds), p)
            except CollectError:
                errs += 1
                continue
            if not data:
                if last and p > last:
                    empty_tail += 1
                continue
            for cmd, pc, v in data:
                rows.append({"기준일": f"{p[:4]}-{p[4:]}", "축": "수입시장", "지표": "수입액",
                             "구분": f"{rep_name}|{cmd}|{pc}|{PARTNERS.get(pc, pc)}", "값": v, "단위": "USD", "출처": SOURCE})
            newest = max(newest or p, p)
            time.sleep(1.0)
        notes.append(f"{rep_name} 수입 ~{newest[:4]}-{newest[4:]}" if newest else f"{rep_name} 없음")
    if not rows and errs:
        raise CollectError(f"요청 실패 {errs}건")
    return append_obs(NAME, rows), ", ".join(notes) + (f", 실패 {errs}건" if errs else "")


if __name__ == "__main__":
    print(run())
