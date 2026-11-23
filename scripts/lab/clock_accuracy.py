"""Compare one hour of a VM agent's persisted totals with an operator's external stopwatch."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
from typing import Callable
from uuid import UUID, uuid4

HOUR = 3600
PERIOD = 15
MAX_JITTER = 2.0
MAX_EDGE_AGE = 8.0  # The visible coordinator normally checkpoints about every five seconds.
EDGE_POLL = 1.0
LIMIT = 30.0


@dataclass(frozen=True)
class Counter:
    device: str
    day: str
    used: float
    logical: float


class CounterReader:
    def __init__(self, path: Path, device: str | None):
        if path.drive.startswith("\\\\"):
            raise ValueError("Cần chọn kho agent cục bộ trong VM.")
        self.path = path.resolve(strict=True)
        if not self.path.is_file() or self.path.drive.startswith("\\\\"):
            raise ValueError("Cần chọn tệp SQLite cục bộ đã tồn tại.")
        self.uri = self.path.as_uri() + "?mode=ro"
        if device:
            self.device = str(UUID(device))
        else:
            with closing(self.connect()) as db:
                rows = db.execute("SELECT device_id FROM screen_clock LIMIT 2").fetchall()
            if len(rows) != 1:
                raise ValueError("Kho phải có đúng một bộ đếm hoặc cần truyền --device UUID.")
            self.device = str(UUID(rows[0][0]))
        if self.read() is None:
            raise ValueError("Chưa có checkpoint của thiết bị được chọn.")

    def connect(self):
        # SQLite may update shared-memory sidecars even with mode=ro on WAL databases.
        # The shipped agent uses the rollback journal. Check before each live open.
        with self.path.open("rb") as source:
            header = source.read(20)
        if len(header) != 20 or header[:16] != b"SQLite format 3\0" or header[18:20] != b"\x01\x01":
            raise ValueError("Cần kho SQLite của agent dùng rollback journal để bảo đảm chỉ đọc.")
        db = sqlite3.connect(self.uri, uri=True, timeout=0.5)
        db.execute("PRAGMA query_only=ON")
        db.set_authorizer(self._authorize)
        return db

    @staticmethod
    def _authorize(action, table, column, database, origin):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if (action == sqlite3.SQLITE_READ and database == "main" and origin is None
                and table == "screen_clock" and column in {"device_id", "day", "used", "logical_ts"}):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def read(self) -> Counter | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT device_id, day, used, logical_ts FROM screen_clock WHERE device_id=?",
                             (self.device,)).fetchone()
        if row is None:
            return None
        device, day, used, logical = row
        if (not isinstance(day, str) or not day or not isinstance(used, (float, int))
                or not isinstance(logical, (float, int)) or not math.isfinite(used) or not math.isfinite(logical)
                or not 0 <= used <= 86400 or logical <= 0):
            raise ValueError("Checkpoint không đủ trường hợp lệ.")
        date.fromisoformat(day)
        return Counter(device, day, used, logical)


@dataclass(frozen=True)
class Observation:
    tick: float
    wall: float
    counter: Counter | None
    checkpoint_age_bound: float | None = None


def collect(read: Callable[[], Counter | None], cue: Callable[[str], None], *,
            monotonic=time.monotonic, wall=time.time, sleep=time.sleep) -> list[Observation]:
    previous = None
    previous_start = lower_bound = None

    def observe():
        nonlocal previous, previous_start, lower_bound
        started = monotonic()
        try:
            row = read()
        except (ValueError, OSError, sqlite3.Error):
            row = None
        tick = monotonic()
        if row is not None:
            if previous is None or row.device != previous.device or row.day != previous.day:
                lower_bound = None
            elif row.logical > previous.logical and row.used >= previous.used:
                # The earlier SELECT still saw the old checkpoint. Its start is a
                # conservative lower bound on when the new checkpoint became visible.
                lower_bound = previous_start
            elif row != previous:
                lower_bound = None
            previous, previous_start = row, started
        age = tick - lower_bound if row is not None and lower_bound is not None else None
        return Observation(tick, wall(), row, age)

    first = observe()
    warmup_deadline = first.tick + MAX_EDGE_AGE
    while first.checkpoint_age_bound is None or first.checkpoint_age_bound > MAX_EDGE_AGE:
        wait = warmup_deadline - monotonic()
        if wait <= 0:
            raise ValueError("Chưa thấy checkpoint mới trước mốc đầu. Hãy kiểm tra phiên lab và đo lại.")
        sleep(min(EDGE_POLL, wait))
        first = observe()
    origin = first.tick
    observations = [first]
    cue("BẮT ĐẦU")
    slot = 1
    while slot <= HOUR // PERIOD:
        target = origin + slot * PERIOD
        if slot == HOUR // PERIOD:
            # Bound the final snapshot age on the observer's monotonic clock. The
            # intermediate edge reads never replace one of the 241 scheduled samples.
            while target - monotonic() > EDGE_POLL:
                sleep(EDGE_POLL)
                if monotonic() < target:
                    observe()
        delay = target - monotonic()
        if delay > 0:
            sleep(delay)
        observation = observe()
        observations.append(observation)
        if observation.tick >= origin + HOUR:
            break
        # A suspended VM must not manufacture readings for the missed intervals.
        slot = min(HOUR // PERIOD, max(slot + 1, math.floor((observation.tick - origin) / PERIOD) + 1))
    cue("KẾT THÚC")
    return observations


def evaluate(samples: list[Observation], device: str, reference_elapsed: float, reference_active: float) -> dict:
    reasons = []
    expected = HOUR // PERIOD + 1
    if not samples:
        raise ValueError("Không có mốc đo.")
    first, last = samples[0], samples[-1]
    elapsed = last.tick - first.tick
    if len(samples) != expected or any(s.counter is None for s in samples):
        reasons.append("missing_samples")
    if abs(elapsed - HOUR) > MAX_JITTER or any(
        not 0 < b.tick - a.tick <= PERIOD + MAX_JITTER for a, b in zip(samples, samples[1:])
    ):
        reasons.append("observer_timing")
    if any(abs((s.wall - first.wall) - (s.tick - first.tick)) > MAX_JITTER for s in samples):
        reasons.append("host_clock_shift")
    if not (math.isfinite(reference_elapsed) and math.isfinite(reference_active)
            and 0 <= reference_active <= reference_elapsed and abs(reference_elapsed - elapsed) <= MAX_JITTER):
        reasons.append("external_reference")
    elif reference_active == 0:
        reasons.append("no_reference_activity")
    counters = [s.counter for s in samples if s.counter is not None]
    if any(c.device != device for c in counters):
        reasons.append("device_changed")
    if len({c.day for c in counters}) > 1:
        reasons.append("day_changed")
    if any(b.used < a.used or b.logical < a.logical for a, b in zip(counters, counters[1:])):
        reasons.append("counter_reset")
    delta = error = uncertainty = None
    if first.counter is not None and last.counter is not None:
        ages = [s.checkpoint_age_bound for s in (first, last)]
        if any(age is None or not math.isfinite(age) or not 0 <= age <= MAX_EDGE_AGE for age in ages):
            reasons.append("unbounded_checkpoint_edge")
        if not reasons:
            delta = last.counter.used - first.counter.used
            error = delta - reference_active
            # Bound the measured window mismatch at both checkpoint edges; do not turn a borderline run into a pass.
            uncertainty = sum(ages) + abs(reference_elapsed - elapsed)
    if reasons:
        result = "inconclusive"
    elif abs(error) + uncertainty <= LIMIT:
        result = "within_tolerance"
    elif abs(error) - uncertainty > LIMIT:
        result = "outside_tolerance"
    else:
        result = "inconclusive"
        reasons.append("boundary_uncertainty")
    return {
        "protocol": "F1.3", "result": result, "reasons": reasons, "limit_sec_per_hour": LIMIT,
        "start_utc": datetime.fromtimestamp(first.wall, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(last.wall, timezone.utc).isoformat(),
        "samples_expected": expected, "samples_observed": len(samples), "samples_with_counter": len(counters),
        "observer_elapsed_sec": round(elapsed, 3),
        "external_elapsed_sec": reference_elapsed if math.isfinite(reference_elapsed) else None,
        "external_active_sec": reference_active if math.isfinite(reference_active) else None,
        "agent_active_sec": None if delta is None else round(delta, 3),
        "agent_minus_reference_sec": None if error is None else round(error, 3),
        "checkpoint_and_elapsed_margin_sec": None if uncertainty is None else round(uncertainty, 3),
    }


def save(report: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"clock-hour-{uuid4()}.json"
    with target.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")
    return target


def reference_reading(prompt: str) -> float:
    while True:
        try:
            value = input(prompt).strip().replace(",", ".")
        except EOFError:
            return math.nan
        if not value:
            return math.nan
        try:
            seconds = float(value)
        except ValueError:
            continue
        if math.isfinite(seconds) and seconds >= 0:
            return seconds


def main():
    parser = argparse.ArgumentParser(description="Nghiệm thu F1.3 trong một giờ trên VM thử nghiệm.")
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--device", help="UUID thiết bị. Có thể bỏ qua khi kho chỉ có một bộ đếm.")
    parser.add_argument("--output-dir", type=Path, default=Path(".local/lab-clock"))
    args = parser.parse_args()
    try:
        source = CounterReader(args.store, args.device)
        print(f"Kho đọc: {source.path}\nThiết bị: {source.device}\nThư mục biên bản mới: {args.output_dir.resolve()}")
        print("Dùng hồ sơ giả trên VM. Chuẩn bị đồng hồ ngoài máy đang ở 0. "
              "Ghi riêng thời lượng theo quy tắc hoạt động, khóa và idle 5 phút.")
        if input("Gõ DO 1 GIO để xác nhận đúng kho, thiết bị và bắt đầu: ").strip() != "DO 1 GIO":
            parser.exit(1, "Đã hủy trước khi đo.\n")
        samples = collect(source.read, lambda mark: print("\a" + mark, flush=True))
        print("Gõ số giây hoặc Enter nếu thiếu số đo. Thiếu mốc sẽ cho kết quả inconclusive.")
        elapsed = reference_reading("Tổng giây trên đồng hồ độc lập từ mốc đầu đến mốc cuối: ")
        active = reference_reading("Trong khoảng đó, số giây hoạt động do người quan sát xác định: ")
        report = evaluate(samples, source.device, elapsed, active)
        path = save(report, args.output_dir)
        print(f"{report['result']} · Sai số (giây): {report['agent_minus_reference_sec']} · {path}")
        if report["result"] != "within_tolerance":
            parser.exit(2)
    except (OSError, ValueError, sqlite3.Error, EOFError) as error:
        parser.exit(1, f"Chưa hoàn tất phép đo. {error}\n")


if __name__ == "__main__":
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8")
    main()
