"""Has today's daily pull already succeeded, and may we try again?

The daily pull has two triggers: the 10:00 schedule, and a logon/unlock trigger
10 minutes after the laptop comes up (this machine is frequently asleep at 10:00,
and a missed pull used to mean no fresh shipments all day). Two triggers means
the same run can be asked for twice on one day, and the pull is the most
expensive thing this project does -- ~136k rows, several minutes, and enough
memory that it has been OOM-killed on this laptop.

So it is gated on a marker holding the date of the last SUCCESSFUL pull. The
marker is written only after the pull exits 0, which is the point: a killed or
failed run leaves the gate open so a later trigger retries it.

RETRY, added 2026-09-14
-----------------------
Leaving the gate open is only useful if something comes back and tries again,
and the daily's own triggers are hours apart. So the 15-minute refresh drives
the retry: at the end of every run it asks `--check`, and launches the daily if
today's has not succeeded. That works even when a pull is OOM-killed mid-flight,
because each retry is a fresh process rather than a loop inside the dead one.

Retrying every 15 minutes forever would hammer Uniware on a persistent failure,
so `--claim` counts attempts per day and refuses past `--max-attempts`. Attempts
reset on success and on a new day. Exit 2 means "give up for today, loudly" --
deliberately distinct from exit 1 ("already done"), so the caller can log the
difference rather than treating a broken pipeline as a healthy skip.

Local calendar date, deliberately -- "did we pull today" is a question about the
operator's day, not about UTC.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
DEFAULT_MARKER = ROOT / "state" / "last_daily_ok.txt"
DEFAULT_ATTEMPTS = ROOT / "state" / "daily_attempts.json"
DEFAULT_MAX_ATTEMPTS = 6


def _today() -> str:
    return dt.date.today().isoformat()


def _succeeded_today(marker: pathlib.Path) -> bool:
    try:
        return marker.read_text(encoding="utf-8").strip() == _today()
    except OSError:
        return False


def _read_attempts(path: pathlib.Path) -> int:
    """Attempts recorded for TODAY. A new day, or unreadable state, is zero.

    Never raises: a corrupt attempts file must not be able to block the pull.
    Failing open risks one extra run; failing closed risks a silent dead board.
    """
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return int(d.get("count", 0)) if d.get("date") == _today() else 0
    except Exception:
        return 0


def _write_attempts(path: pathlib.Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": _today(), "count": count}), encoding="utf-8")


def check(marker: pathlib.Path) -> int:
    """Read-only. Exit 0 = not done today, run it. Exit 1 = already done."""
    if _succeeded_today(marker):
        print(f"[daygate] daily pull already succeeded today ({_today()}) -- skipping")
        return 1
    try:
        last = marker.read_text(encoding="utf-8").strip() or "(blank)"
    except OSError:
        last = "(none)"
    print(f"[daygate] last successful daily pull was {last}, today is {_today()} -- running")
    return 0


def claim(marker: pathlib.Path, attempts: pathlib.Path, max_attempts: int) -> int:
    """Exit 0 = go (attempt recorded), 1 = already done, 2 = out of attempts."""
    if _succeeded_today(marker):
        print(f"[daygate] daily pull already succeeded today ({_today()}) -- skipping")
        return 1
    used = _read_attempts(attempts)
    if used >= max_attempts:
        print(f"[daygate] GIVING UP for today: {used} attempts already made "
              f"(max {max_attempts}) and none succeeded. Something is wrong -- "
              f"check the newest daily_*.log. Retries resume tomorrow, or after "
              f"deleting {attempts.name}.")
        return 2
    _write_attempts(attempts, used + 1)
    print(f"[daygate] attempt {used + 1} of {max_attempts} for {_today()}")
    return 0


def mark(marker: pathlib.Path, attempts: pathlib.Path) -> int:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(_today(), encoding="utf-8")
    try:
        attempts.unlink()
    except OSError:
        pass
    print(f"[daygate] recorded a successful daily pull for {_today()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--marker", default=str(DEFAULT_MARKER))
    ap.add_argument("--attempts-file", default=str(DEFAULT_ATTEMPTS))
    ap.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--claim", action="store_true")
    g.add_argument("--mark", action="store_true")
    a = ap.parse_args(argv)
    m = pathlib.Path(a.marker)
    at = pathlib.Path(a.attempts_file)
    if a.mark:
        return mark(m, at)
    if a.claim:
        return claim(m, at, a.max_attempts)
    return check(m)


if __name__ == "__main__":
    sys.exit(main())
