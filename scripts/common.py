"""공통 유틸: 데이터 경로, JSON 읽기/쓰기, 긴 형식(long) 레코드 병합."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = ROOT / "raw"
DATA.mkdir(exist_ok=True)

KST = timezone(timedelta(hours=9))


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def load_json(name: str, default):
    p = DATA / name
    if not p.exists():
        return default
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(name: str, obj) -> None:
    p = DATA / name
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print(f"[saved] {p.relative_to(ROOT)}")


def merge_long(existing: list[dict], new: list[dict], key_fields: tuple[str, ...]) -> list[dict]:
    """긴 형식 레코드를 키 기준으로 병합. 새 값이 기존 값을 덮어씀."""
    index = {tuple(r.get(k) for k in key_fields): r for r in existing}
    for r in new:
        index[tuple(r.get(k) for k in key_fields)] = r
    rows = list(index.values())
    rows.sort(key=lambda r: tuple(str(r.get(k)) for k in key_fields))
    return rows


def to_number(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


def update_meta(source: str, status: str, note: str = "") -> None:
    meta = load_json("meta.json", {})
    meta[source] = {"updated": now_kst(), "status": status, "note": note}
    save_json("meta.json", meta)
