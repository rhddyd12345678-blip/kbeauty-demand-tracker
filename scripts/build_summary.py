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
    }
    save_json("summary.json", summary)


if __name__ == "__main__":
    main()
