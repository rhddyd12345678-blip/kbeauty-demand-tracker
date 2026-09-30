"""공통: 경로, .env, 설정, 긴 형식 CSV 누적 저장, 수집기 상태 기록.

저장 형식 (data/obs/<수집기>.csv, UTF-8 BOM — 엑셀에서 바로 열림)
  기준일 | 축 | 지표 | 구분 | 값 | 단위 | 출처 | 수집일

과거 값을 덮어쓰지 않는다. (기준일, 지표, 구분, 출처)가 같은 행이 이미 있고 값도 같으면 건너뛰고,
값이 달라졌으면(예: 관세청 잠정치 → 확정치) 새 수집일로 한 줄 더 쌓는다. 읽을 때는 키별 최신 수집일 값을 쓴다.
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
# 테스트 때만 TRACKER_DATA로 다른 폴더를 가리킨다 (실데이터 보호)
DATA = Path(os.environ["TRACKER_DATA"]) if os.environ.get("TRACKER_DATA") else ROOT / "data"
OBS = DATA / "obs"
STATUS = DATA / "status.json"
MANUAL = ROOT / "manual"
RAW = ROOT / "raw"
SITE_DATA = Path(os.environ["TRACKER_SITE"]) if os.environ.get("TRACKER_SITE") else ROOT / "site" / "data"
KST = timezone(timedelta(hours=9))

COLUMNS = ["기준일", "축", "지표", "구분", "값", "단위", "출처", "수집일"]
KEY = ("기준일", "지표", "구분", "출처")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


class CollectError(Exception):
    """수집 실패. 메시지는 사이트의 '수집 불가 사유'로 그대로 노출된다."""


class MissingKey(CollectError):
    pass


def now_kst() -> datetime:
    return datetime.now(KST)


def today() -> str:
    return now_kst().strftime("%Y-%m-%d")


def load_env() -> None:
    """로컬 실행용 .env 읽기 (이미 설정된 환경변수는 건드리지 않음). CI에서는 Secrets가 환경변수로 들어온다."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def need_key(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise MissingKey(f"API 키 없음({name}) — README의 키 발급 절차 참고")
    return v


def config() -> dict:
    return yaml.safe_load((ROOT / "config" / "tracker.yml").read_text(encoding="utf-8"))


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        return f"{v:.6f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return str(v)


def read_obs(name: str) -> list[dict]:
    f = OBS / f"{name}.csv"
    if not f.exists():
        return []
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def append_obs(name: str, rows: list[dict]) -> int:
    """rows: COLUMNS 중 수집일 외 필드를 가진 dict. 새로 쌓인 행 수를 돌려준다."""
    OBS.mkdir(parents=True, exist_ok=True)
    f = OBS / f"{name}.csv"
    latest: dict[tuple, str] = {}
    for r in read_obs(name):  # 수집일 오름차순으로 쌓이므로 마지막 값이 최신
        latest[tuple(r[k] for k in KEY)] = r["값"]
    stamp = today()
    new = []
    for r in rows:
        r = {k: fmt(r.get(k)) for k in COLUMNS if k != "수집일"}
        if r["값"] == "":
            continue
        key = tuple(r[k] for k in KEY)
        if latest.get(key) == r["값"]:
            continue
        latest[key] = r["값"]
        r["수집일"] = stamp
        new.append(r)
    if new:
        exists = f.exists()
        with f.open("a", encoding="utf-8-sig" if not exists else "utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            if not exists:
                w.writeheader()
            w.writerows(new)
    return len(new)


SECRET_NAMES = ("DATA_GO_KR_KEY", "DART_KEY", "ECOS_KEY")


def mask(text: str) -> str:
    """메시지에 섞인 키 값(원문·URL 인코딩형)을 *** 로 가린다. 상태 파일·콘솔에 쓰기 전에 반드시 거친다."""
    import urllib.parse
    text = str(text)
    for n in SECRET_NAMES:
        v = (os.environ.get(n) or "").strip()
        if len(v) >= 8:
            for f in {v, urllib.parse.quote(v, safe=""), urllib.parse.quote(urllib.parse.quote(v, safe=""), safe="")}:
                text = text.replace(f, "***")
    return text


def load_status() -> dict:
    return json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}


def set_status(name: str, ok: bool, note: str, added: int = 0, kind: str = "") -> None:
    s = load_status()
    prev = s.get(name, {})
    stamp = now_kst().strftime("%Y-%m-%d %H:%M")
    s[name] = {
        "상태": "정상" if ok else (kind or "실패"),
        "메모": mask(note),
        "추가행": added,
        "최근실행": stamp,
        "최근성공": stamp if ok else prev.get("최근성공", ""),
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(s, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def check_gateway(text: str) -> None:
    """공공데이터포털 게이트웨이 인증 오류를 짧은 메시지로 바꾼다 (응답 원문·키는 남기지 않음)."""
    for code, msg in (("SERVICE_KEY_IS_NOT_REGISTERED", "등록되지 않은 서비스키(코드 30) — 활용신청 승인/반영 대기(보통 1~2시간)"),
                      ("SERVICE_KEY_IS_NULL", "서비스키 없음"), ("LIMITED_NUMBER_OF_SERVICE_REQUESTS", "일일 호출 한도 초과"),
                      ("SERVICE ACCESS DENIED", "서비스 접근 거부(활용신청 안 됨)"), ("SERVICE KEY IS NOT REGISTERED", "등록되지 않은 서비스키(코드 30) — 활용신청 승인/반영 대기")):
        if code in text:
            raise MissingKey(f"인증 오류: {msg}")


def http_get(url: str, *, params=None, headers=None, timeout=60, tries=3, backoff=3.0) -> requests.Response:
    h = {"User-Agent": UA}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last = CollectError(f"HTTP {r.status_code}")
                time.sleep(float(r.headers.get("Retry-After") or backoff * (i + 1)))
                continue
            return r
        except requests.RequestException as e:
            last = CollectError(f"요청 실패: {type(e).__name__}")
            time.sleep(backoff * (i + 1))
    raise last or CollectError("요청 실패")


def month_range(start: str, end: str) -> list[str]:
    """'YYYYMM' 두 개 사이의 모든 월."""
    a = int(start[:4]) * 12 + int(start[4:6]) - 1
    b = int(end[:4]) * 12 + int(end[4:6]) - 1
    return [f"{m // 12}{m % 12 + 1:02d}" for m in range(a, b + 1)]
