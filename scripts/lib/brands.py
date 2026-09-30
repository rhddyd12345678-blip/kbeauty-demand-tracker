"""K브랜드 판별 — config/brand_master.csv

열: brand, made_in_korea(0/1), is_korean_brand(0/1), aliases(';'로 구분, 선택), memo
제목에서 브랜드를 찾는 규칙: 브랜드명·괄호 안 이름·괄호 앞 이름·aliases 중 하나가 제목에 단어로 들어 있으면 매칭,
여러 개가 맞으면 제목 앞쪽에 먼저 나오는 것. 못 찾으면 미분류(사이트에 목록으로 띄워 brand_master에 추가하게 함).
"""
from __future__ import annotations

import csv
import re

from .core import ROOT

FILE = ROOT / "config" / "brand_master.csv"


def load() -> list[dict]:
    with FILE.open(encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("brand") or "").strip()]
    for r in rows:
        b = r["brand"].strip()
        names = {b}
        m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", b)
        if m:
            names |= {m.group(1).strip(), m.group(2).strip()}
        names |= {a.strip() for a in (r.get("aliases") or "").split(";") if a.strip()}
        r["_pats"] = [re.compile(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])") for n in names if len(n) >= 2]
        r["mik"] = str(r.get("made_in_korea", "")).strip() == "1"
        r["kb"] = str(r.get("is_korean_brand", "")).strip() == "1"
    return rows


def match(title: str, master: list[dict]) -> dict | None:
    t = (title or "").lower()
    best, pos = None, 10**9
    for r in master:
        for p in r["_pats"]:
            m = p.search(t)
            if m and m.start() < pos:
                best, pos = r, m.start()
    return best
