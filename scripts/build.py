"""관측값(data/obs) + 수동 입력(manual) → 사이트용 JSON(site/data/site.json).

사용: python scripts/build.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from buildlib import axis1, axis2, axis3, demand, news, pages, story  # noqa: E402
from lib.core import OBS, SITE_DATA, config, load_status, now_kst  # noqa: E402


# 상단 내비게이션: 두 영역. demand = 기존 트래커에서 온 화면, analysis = 이번에 새로 만든 분석(NEW)
NAV = [
    {"area": "common", "label": "", "tabs": [["overview", "개요"]]},
    {"area": "demand", "label": "전방수요 모니터", "purpose": "수출·수입시장·아마존·검색·뉴스로 K뷰티 수요가 지금 어느 방향인지 매일 확인한다 (기존 트래커에서 옴).",
     "tabs": [["d_exports", "수출·수입시장"], ["d_amazon", "아마존"], ["d_search", "검색·인지도"], ["d_companies", "기업·주가"], ["news", "뉴스"]]},
    {"area": "analysis", "label": "리레이팅·밸류체인 분석", "new": True,
     "purpose": "멀티플이 오를 조건과 밸류체인 5단계의 흐름을 숫자로 검증하고, 숫자로 답할 수 없는 것을 구분한다 (이번에 새로 만듦).",
     "tabs": [["rerating", "리레이팅 조건"], ["flywheel", "밸류체인"], ["indicators", "개별 지표"], ["limits", "데이터 한계"], ["appendix", "부록"]]},
]


def main() -> None:
    cfg = config()
    a1, k1 = axis1.build(cfg)
    a2, k2 = axis2.build(cfg)
    a3 = axis3.build(cfg, a2["sections"])
    d = demand.build(cfg, a1, a2, a3)
    ov = pages.overview(k1, k2)
    ov["tiles"] = pages.demand_tiles(k2)
    ov["guide"] = demand.guide()
    out = {
        "generated": now_kst().strftime("%Y-%m-%d %H:%M"),
        "status": load_status(),
        "nav": NAV,
        "story": story.build(cfg),
        "tabs": {"overview": ov, **d, "news": news.build(cfg),
                 "rerating": a1, "flywheel": a2, "indicators": a3, "limits": pages.limits(), "appendix": pages.appendix()},
    }
    # 여러 탭이 같은 차트를 쓰면 본문은 shared에 한 번만 두고 탭에는 {"ref": id}만 남긴다(같은 데이터를 참조)
    seen: dict[str, int] = {}
    for t in out["tabs"].values():
        for sec in (t.get("sections") or []) if isinstance(t, dict) else []:
            for it in sec["items"]:
                seen[it["id"]] = seen.get(it["id"], 0) + 1
    shared = {}
    for t in out["tabs"].values():
        for sec in (t.get("sections") or []) if isinstance(t, dict) else []:
            for i, it in enumerate(sec["items"]):
                if seen[it["id"]] > 1:
                    shared[it["id"]] = it
                    sec["items"][i] = {"ref": it["id"]}
    out["shared"] = shared
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "site.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # 원자료 CSV도 사이트에서 내려받을 수 있게 복사
    raw = SITE_DATA / "obs"
    raw.mkdir(exist_ok=True)
    files = sorted(list(OBS.glob("*.csv")) + [f for f in OBS.parent.glob("*.csv")])
    for f in files:
        shutil.copy2(f, raw / f.name)
    links = "".join(f'<li><a href="{f.name}">{f.name}</a> ({f.stat().st_size // 1024:,}KB)</li>' for f in files)
    (raw / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>원자료 CSV</title><body style="font:15px system-ui;padding:16px;max-width:760px;margin:auto">'
        '<h1 style="font-size:18px">원자료 CSV</h1><p>긴 형식: 기준일 | 축 | 지표 | 구분 | 값 | 단위 | 출처 | 수집일. '
        '같은 키의 값이 바뀌면(예: 잠정치 정정) 새 수집일로 한 줄 더 쌓이고 과거 행은 지우지 않는다.</p>'
        f'<ul>{links}</ul><p><a href="../../">← 트래커</a></p>', encoding="utf-8")
    print(f"site.json {(SITE_DATA / 'site.json').stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
