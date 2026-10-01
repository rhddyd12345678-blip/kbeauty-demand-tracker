"""활용신청 반영 대기 중인 API를 로컬에서 30분마다 재시도 (최대 12시간).

대상: index(금융위 지수시세 15094807), tourism(출입국관광통계 — GW 15158830 → 원래 15000297 순서로 시도, 통과한 주소를
data/tourism_endpoint.txt에 고정). 한 API가 통과하면 그 API만 백필 → build.py → 키 값 검사 → 데이터만 커밋·푸시하고
그 API는 더 부르지 않는다. 둘 다 통과하거나 12시간이 지나면 끝.

작업 폴더를 건드리지 않도록 별도 작업 사본(git worktree, ~/kbeauty/.kbeauty-retry)에서만 수집·커밋·푸시한다.
(작업 폴더에서 pull/autostash를 하면 편집 중인 파일과 충돌할 수 있음 — 2026-09-30 실제로 status.json 충돌)
로그: logs/retry.log (시각 | API | 결과), 키 값은 가림. logs/는 git 제외.
실행:   nohup caffeinate -i .venv/bin/python scripts/retry_local.py >> logs/retry.out 2>&1 &
확인:   tail -f logs/retry.log
중지:   kill $(cat logs/retry.pid)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.core import load_env, mask  # noqa: E402

WT = ROOT.parent / ".kbeauty-retry"   # 스크립트 전용 작업 사본
LOG = ROOT / "logs" / "retry.log"
PID = ROOT / "logs" / "retry.pid"
PY = str(ROOT / ".venv" / "bin" / "python")
TARGETS = {"index": "금융위 지수시세(15094807)", "tourism": "출입국관광통계(GW 15158830 / 원래 15000297)"}
INTERVAL_MIN = 30
MAX_HOURS = 12
COMMIT_PATHS = ["data/obs", "data/status.json", "data/tourism_endpoint.txt", "site"]


def log(api: str, result: str) -> None:
    LOG.parent.mkdir(exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {api} | {mask(result)}"
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(WT), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(mask(f"git {' '.join(args)}: {r.stderr.strip()[:200]}"))
    return r


def status_of(name: str) -> dict:
    f = WT / "data" / "status.json"
    return json.loads(f.read_text(encoding="utf-8")).get(name, {}) if f.exists() else {}


def key_values() -> list[bytes]:
    vals = []
    for n in ("DATA_GO_KR_KEY", "DART_KEY", "ECOS_KEY"):
        v = (os.environ.get(n) or "").strip()
        if len(v) >= 8:
            vals += [v.encode(), urllib.parse.quote(v, safe="").encode()]
    return vals


def publish(api: str) -> None:
    """build → 커밋 대상 키 값 검사 → 데이터·site만 커밋 → 푸시 (거절되면 pull --rebase 후 한 번 더)."""
    b = subprocess.run([PY, "scripts/build.py"], cwd=WT, capture_output=True, text=True)
    if b.returncode:
        log(api, f"build 실패 — 커밋 안 함: {b.stderr.strip()[-200:]}")
        return
    git("add", "--", *[p for p in COMMIT_PATHS if (WT / p).exists()])
    staged = [f for f in git("diff", "--cached", "--name-only").stdout.split("\n") if f]
    if not staged:
        log(api, "커밋할 변경 없음")
        return
    vals = key_values()
    hits = [f for f in staged if (WT / f).is_file() and any(v in (WT / f).read_bytes() for v in vals)]
    if hits:
        git("reset", "-q")
        log(api, f"키 값 발견 {len(hits)}개 파일 — 커밋 중단: {hits}")
        return
    git("commit", "-q", "-m", f"data: {TARGETS[api]} 첫 수집·백필 (로컬 재시도)")
    if git("push", "origin", "HEAD:main", check=False).returncode:
        git("fetch", "-q", "origin", check=False)
        # 그사이 Actions가 올린 커밋 위로 다시 얹기. 충돌은 status.json 뿐이라 이 커밋(성공 기록) 쪽을 택한다
        r = git("rebase", "-X", "theirs", "origin/main", check=False)
        if r.returncode:
            git("rebase", "--abort", check=False)
            log(api, f"푸시 거절·재배치 충돌 — 작업 사본({WT})에 커밋만 남김, 수동 확인 필요")
            return
        if git("push", "origin", "HEAD:main", check=False).returncode:
            log(api, "푸시 실패 — 작업 사본에 커밋만 남김, 수동 확인 필요")
            return
    log(api, f"커밋·푸시 완료 ({len(staged)}개 파일, 키 값 0건)")


def prepare_worktree() -> None:
    """스크립트 전용 작업 사본을 원격 main과 똑같이 맞춘다(이 사본은 스크립트만 쓴다)."""
    if not WT.exists():
        subprocess.run(["git", "-C", str(ROOT), "fetch", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "-q", "--detach", str(WT), "origin/main"], check=True)
    env = ROOT / ".env"
    if env.exists():
        (WT / ".env").write_bytes(env.read_bytes())  # .gitignore 대상이라 커밋되지 않음
    git("fetch", "-q", "origin")
    git("reset", "-q", "--hard", "origin/main")


def main() -> None:
    load_env()
    PID.parent.mkdir(exist_ok=True)
    PID.write_text(str(os.getpid()))
    end = datetime.now() + timedelta(hours=MAX_HOURS)
    prepare_worktree()  # 대상은 원격 최신 상태(작업 사본)의 성공 기록으로 정한다
    pending = [n for n in TARGETS if not status_of(n).get("최근성공")]
    log("시작", f"대상 {pending}, {INTERVAL_MIN}분 간격, {end:%m-%d %H:%M}까지")
    while pending and datetime.now() < end:
        try:
            prepare_worktree()  # Actions가 올린 변경 먼저 받기 (작업 사본만)
            ready = True
        except Exception as e:  # noqa: BLE001
            ready = False
            log("git", mask(f"작업 사본 준비 실패 — 이번 회차 건너뜀: {e}"))
        for name in list(pending) if ready else []:
            r = subprocess.run([PY, "scripts/collect_all.py", "--only", name], cwd=WT, capture_output=True, text=True)
            st = status_of(name)
            if st.get("상태") == "정상":
                log(name, f"통과 +{st.get('추가행', 0)}행 ({st.get('메모', '')})")
                publish(name)
                pending.remove(name)
            else:
                msg = st.get("메모") or r.stdout.strip()[-200:]
                log(name, msg)  # 메모에 "코드 30" 등 원인이 그대로 들어 있음
        if pending:
            time.sleep(INTERVAL_MIN * 60)
    log("종료", "모두 통과" if not pending else f"12시간 경과 — 미통과 {pending}")
    PID.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
