"""data/*.json → data/summary.json (대시보드 KPI·집계). 모든 수집 후 마지막에 실행."""
from __future__ import annotations

from collections import defaultdict

from common import load_json, save_json, now_kst


def trade_summary(trade: list[dict]) -> dict:
    out = {}
    by_hs = defaultdict(dict)
    for r in trade:
        if r["freq"] == "M":
            by_hs[r["hs"]][r["period"]] = r
    for hs, m in by_hs.items():
        periods = sorted(m)
        series = [{"period": p, "export": m[p]["export_usd_k"], "import": m[p]["import_usd_k"]} for p in periods]
        # YoY
        for s in series:
            y, mm = s["period"].split("-")
            prev = m.get(f"{int(y) - 1}-{mm}")
            s["yoy"] = round((s["export"] / prev["export_usd_k"] - 1) * 100, 1) if prev and prev["export_usd_k"] else None
        # 12개월 누적
        for i, s in enumerate(series):
            s["ttm"] = sum(x["export"] or 0 for x in series[max(0, i - 11):i + 1]) if i >= 11 else None
        out[hs] = {"item": m[periods[-1]]["item"], "series": series, "latest": series[-1]}
    return out


def amazon_summary(rows: list[dict]) -> list[dict]:
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)
    out = []
    for d in sorted(by_date):
        rs = by_date[d]
        n = len(rs)
        mik = sum(1 for r in rs if r["made_in_korea"] == 1)
        kb = sum(1 for r in rs if r["korean_brand"] == 1)
        top10_mik = sum(1 for r in rs if r["made_in_korea"] == 1 and (r["rank"] or 99) <= 10)
        brands_k = sorted({r["brand"] for r in rs if r["made_in_korea"] == 1})
        out.append({"date": d, "n": n, "made_in_korea": mik, "korean_brand": kb,
                    "mik_pct": round(mik / n * 100, 1), "kb_pct": round(kb / n * 100, 1),
                    "top10_mik": top10_mik, "korean_brands": brands_k,
                    "unmapped": sum(1 for r in rs if r["made_in_korea"] == -1)})
    return out


def naver_summary(nt: dict) -> dict:
    """주간 평균으로 다운샘플 (엑셀 시계열만, 'api:' 접두 제외)."""
    if not nt.get("dates"):
        return {}
    dates = nt["dates"]
    weeks: dict[str, list[int]] = defaultdict(list)
    from datetime import date as D
    for i, d in enumerate(dates):
        y, w, _ = D.fromisoformat(d).isocalendar()
        weeks[f"{y}-W{w:02d}"].append(i)
    wk = sorted(weeks)
    series = {}
    for k, vals in nt["series"].items():
        if k.startswith("api:"):
            continue
        out = []
        for w in wk:
            v = [vals[i] for i in weeks[w] if vals[i] is not None]
            out.append(round(sum(v) / len(v), 2) if v else None)
        series[k] = out
    # 주 대표 날짜 = 그 주의 첫 날짜
    return {"weeks": [dates[weeks[w][0]] for w in wk], "series": series}


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
CMD_NAME = {"3304": "화장품(HS 3304)", "300249": "보툴리눔 톡신 등(HS 3002.49)"}


def comtrade_summary(ct: dict) -> dict:
    """보고국·HS별로: 월별 전체, 상위 상대국 시계열, 한국 점유율·순위."""
    out = {}
    for key, months in ct.items():
        if key.startswith("_"):
            continue
        rep, flow = key.split("|")
        periods = sorted(p for p, v in months.items() if v)
        cmds = sorted({c for p in periods for c in months[p]})
        for cmd in cmds:
            ps = [p for p in periods if cmd in months[p]]
            if not ps:
                continue
            rows = [months[p][cmd] for p in ps]
            total = [r.get("0") for r in rows]
            # 최근 12개월 합계 기준 상위 상대국 8
            agg = defaultdict(float)
            for r in rows[-12:]:
                for k, v in r.items():
                    if k != "0" and v:
                        agg[k] += v
            top = [k for k, _ in sorted(agg.items(), key=lambda x: -x[1])[:8]]
            if flow == "M" and "410" not in top and "410" in agg:
                top = top[:7] + ["410"]
            series = {k: [r.get(k) for r in rows] for k in top}
            rec = {
                "reporter": rep, "flow": flow, "cmd": cmd, "cmd_name": CMD_NAME.get(cmd, cmd),
                "periods": [f"{p[:4]}-{p[4:]}" for p in ps], "total": total,
                "partners": {k: PARTNERS.get(k, k) for k in top}, "series": series,
                "order": top,  # 금액순 (JS 객체의 숫자 키는 자동 정렬되므로 순서를 따로 보관)
            }
            if flow == "M":  # 수입국 입장: 한국의 점유율·순위
                kr = [r.get("410") for r in rows]
                rec["kr_share"] = [round(k / t * 100, 2) if k and t else None for k, t in zip(kr, total)]
                rec["kr_rank"] = [1 + sum(1 for kk, v in r.items() if kk not in ("0", "410") and v and k and v > k) if k else None
                                  for r, k in zip(rows, kr)]
                rec["fr_share"] = [round(r["251"] / t * 100, 2) if r.get("251") and t else None for r, t in zip(rows, total)]
            out[f"{key}|{cmd}"] = rec
    return out


def companies_summary(co: dict) -> list[dict]:
    return [{"code": c["code"], "name": c["name"], "group": c["group"], **c.get("kpi", {}),
             "target_price": c.get("info", {}).get("target_price")} for c in co.get("companies", [])]


def main() -> None:
    trade = load_json("trade.json", [])
    amazon = load_json("amazon_top50.json", [])
    nt = load_json("naver_trend.json", {})
    news = load_json("news.json", [])
    summary = {
        "built": now_kst(),
        "meta": load_json("meta.json", {}),
        "trade": trade_summary(trade),
        "amazon": amazon_summary(amazon),
        "naver": naver_summary(nt),
        "news_count": len(news),
        "news_summarized": sum(1 for a in news if a.get("summary")),
        "comtrade": comtrade_summary(load_json("comtrade.json", {})),
        "companies": companies_summary(load_json("companies.json", {})),
    }
    save_json("summary.json", summary)


if __name__ == "__main__":
    main()
