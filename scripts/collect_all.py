"""수집기 일괄 실행. 각 수집기는 독립적으로 실패한다(하나가 실패해도 나머지와 빌드는 진행).

주기: 마지막 '성공' 후 INTERVAL일이 지났을 때만 실행. 월·분기 자료도 공표일이 들쭉날쭉하므로 주 1회 확인한다.
재시도: 한 번도 성공한 적 없는 소스(예: 활용신청 반영 전 코드 30)는 주기와 상관없이 매 실행(매일) 시도한다.
  첫 성공 때 백필까지 끝나면 그다음부터 원래 주기로 돌아간다. 성공 후 실패가 이어져도 '마지막 성공'이 주기보다 오래되면
  매일 다시 시도하므로 따로 재시도 목록을 둘 필요가 없다. 상태는 data/status.json(커밋됨)에 남는다.
사용:
  python scripts/collect_all.py              # 주기가 된 것만
  python scripts/collect_all.py --force      # 전부
  python scripts/collect_all.py --only fx,dart
"""
from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.core import CollectError, MissingKey, load_env, load_status, mask, set_status  # noqa: E402

# 이름: 실행 간격(일)
INTERVAL = {
    "fx": 1,
    "stocks": 1,
    "news": 1,
    "amazon": 7,
    "wiki": 7,
    "customs": 7,
    "tourism": 7,
    "dart": 7,
    "comtrade": 7,
    "index": 1,
}


def due(name: str, status: dict) -> bool:
    last = status.get(name, {}).get("최근성공", "")
    if not last:
        return True  # 아직 한 번도 성공 못 함 → 매일 재시도
    days = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M")).total_seconds() / 86400
    return days >= INTERVAL[name] - 0.2  # 크론 시각이 조금 흔들려도 같은 날엔 실행되게


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    load_env()
    status = load_status()
    names = [n for n in (a.only.split(",") if a.only else INTERVAL) if n]
    for name in names:
        if not a.force and not a.only and not due(name, status):
            print(f"[{name}] 건너뜀 (주기 전)")
            continue
        try:
            mod = importlib.import_module(f"collect.{name}")
        except ModuleNotFoundError:
            print(f"[{name}] 수집기 없음")
            continue
        try:
            added, note = mod.run()
            set_status(name, True, note, added)
            print(mask(f"[{name}] 정상 +{added}행 {note}"))
        except MissingKey as e:
            set_status(name, False, str(e), kind="인증 오류" if "인증" in str(e) else "키 없음")
            print(mask(f"[{name}] 키 없음: {e}"))
        except CollectError as e:
            set_status(name, False, str(e))
            print(mask(f"[{name}] 실패: {e}"))
        except Exception as e:  # 예상 못 한 오류도 다른 수집기를 막지 않는다
            set_status(name, False, f"{type(e).__name__}: {e}")
            print(mask(traceback.format_exc()))  # 예외 원문에 URL(키 포함)이 섞일 수 있어 가린 뒤 출력
    return 0


if __name__ == "__main__":
    sys.exit(main())
