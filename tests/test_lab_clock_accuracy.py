from contextlib import closing
from dataclasses import replace
import json
import math
import sqlite3
import sys
from uuid import uuid4

import pytest

from scripts.lab import clock_accuracy as clock


DEVICE = str(uuid4())
OTHER = str(uuid4())
EPOCH = 1_800_000_000.0


class Timer:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        assert seconds > 0
        self.now += seconds

    def read(self):
        return clock.Counter(DEVICE, "2027-01-15", 50 + self.now, EPOCH + self.now)


@pytest.fixture
def hour():
    timer = Timer()
    cues = []
    samples = clock.collect(timer.read, lambda mark: cues.append((timer.now, mark)), monotonic=lambda: timer.now,
                            wall=lambda: EPOCH + timer.now, sleep=timer.sleep)
    assert cues == [(1, "BẮT ĐẦU"), (3601, "KẾT THÚC")]
    return samples


@pytest.mark.parametrize(("active", "status", "error"), [
    (3600, "within_tolerance", 0),
    (3572, "within_tolerance", 28),
    (3570, "inconclusive", 30),
    (3567, "outside_tolerance", 33),
])
def test_hour_against_external_reference(hour, active, status, error):
    report = clock.evaluate(hour, DEVICE, 3600, active)
    assert report["result"] == status
    assert report["samples_with_counter"] == report["samples_expected"] == 241
    assert report["agent_minus_reference_sec"] == error
    assert report["checkpoint_and_elapsed_margin_sec"] == 2


def test_late_checkpoint_cannot_turn_borderline_run_into_a_pass(hour):
    for i in (0, -1):
        hour[i] = replace(hour[i], checkpoint_age_bound=5)
    report = clock.evaluate(hour, DEVICE, 3600, 3572)
    assert report["agent_minus_reference_sec"] == 28 and report["checkpoint_and_elapsed_margin_sec"] == 10
    assert report["result"] == "inconclusive" and report["reasons"] == ["boundary_uncertainty"]


def test_server_five_seconds_ahead_cannot_hide_the_last_stale_checkpoint():
    timer = Timer()

    def read():
        tick = int(timer.now)
        saved = min(tick, 1) if tick < 2 else 2 + ((tick - 2) // 6) * 6
        return clock.Counter(DEVICE, "2027-01-15", saved, EPOCH + saved + 5)

    samples = clock.collect(read, lambda _: None, monotonic=lambda: timer.now,
                            wall=lambda: EPOCH + timer.now, sleep=timer.sleep)
    first, last = samples[0], samples[-1]
    assert last.wall == last.counter.logical  # Wall-clock subtraction hides five seconds here.
    assert first.checkpoint_age_bound == 1 and last.checkpoint_age_bound == 6
    delta = last.counter.used - first.counter.used
    report = clock.evaluate(samples, DEVICE, 3600, delta - 24)
    assert report["checkpoint_and_elapsed_margin_sec"] == 7
    assert report["result"] == "inconclusive" and report["reasons"] == ["boundary_uncertainty"]


def test_no_progress_before_start_cannot_arm_external_stopwatch():
    timer = Timer()
    stale = timer.read()
    with pytest.raises(ValueError, match="checkpoint mới"):
        clock.collect(lambda: stale, lambda _: pytest.fail("issued a start cue for a stalled agent"),
                      monotonic=lambda: timer.now, wall=lambda: EPOCH + timer.now, sleep=timer.sleep)
    assert timer.now == clock.MAX_EDGE_AGE


def test_agent_can_undercount_active_time(hour):
    for i, observation in enumerate(hour):
        row = observation.counter
        hour[i] = replace(observation, counter=replace(row, used=50 + observation.tick * 3500 / 3600))
    report = clock.evaluate(hour, DEVICE, 3600, 3600)
    assert report["result"] == "outside_tolerance" and report["agent_minus_reference_sec"] == -100


@pytest.mark.parametrize(("problem", "reason"), [
    ("replaced_device", "device_changed"),
    ("midnight", "day_changed"),
    ("rollback", "counter_reset"),
    ("missing", "missing_samples"),
    ("stale", "unbounded_checkpoint_edge"),
    ("wall", "host_clock_shift"),
    ("late", "observer_timing"),
    ("gap", "missing_samples"),
])
def test_invalid_window_has_no_accuracy_verdict(hour, problem, reason):
    row = hour[-1].counter
    if problem == "replaced_device":
        hour[-1] = replace(hour[-1], counter=replace(row, device=OTHER))
    elif problem == "midnight":
        hour[-1] = replace(hour[-1], counter=replace(row, used=1, day="2027-01-16"))
    elif problem == "rollback":
        hour[-1] = replace(hour[-1], counter=replace(row, used=33))
    elif problem == "missing":
        hour[120] = replace(hour[120], counter=None)
    elif problem == "stale":
        hour[-1] = replace(hour[-1], checkpoint_age_bound=12)
    elif problem == "wall":
        hour[10] = replace(hour[10], wall=hour[10].wall + 60)
    elif problem == "late":
        hour[10] = replace(hour[10], tick=hour[10].tick + 5)
    else:
        del hour[10]
    report = clock.evaluate(hour, DEVICE, 3600, 3600)
    assert report["result"] == "inconclusive" and reason in report["reasons"]
    assert report["agent_minus_reference_sec"] is None


@pytest.mark.parametrize(("elapsed", "active", "reason"), [
    (3597, 3500, "external_reference"), (3600, math.nan, "external_reference"),
    (3600, 3601, "external_reference"), (3600, 0, "no_reference_activity"),
])
def test_external_markers_required(hour, elapsed, active, reason):
    report = clock.evaluate(hour, DEVICE, elapsed, active)
    assert report["reasons"] == [reason] and report["result"] == "inconclusive"
    json.dumps(report, allow_nan=False)


def test_delayed_or_unreadable_samples_are_not_invented():
    timer = Timer()

    def delay(seconds):
        timer.sleep(seconds + (45 if timer.now == 16 else 0))

    def read():
        if timer.now == 121:
            raise sqlite3.OperationalError("busy")
        return timer.read()

    samples = clock.collect(read, lambda _: None, monotonic=lambda: timer.now, wall=lambda: EPOCH + timer.now,
                            sleep=delay)
    report = clock.evaluate(samples, DEVICE, 3600, 3600)
    assert report["samples_observed"] == 238 and report["samples_with_counter"] == 237
    assert {"observer_timing", "missing_samples"}.issubset(report["reasons"])


def build_store(path, *, wal=False):
    with closing(sqlite3.connect(path)) as db, db:
        if wal:
            assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        db.execute("CREATE TABLE screen_clock(device_id TEXT, day TEXT, used REAL, logical_ts REAL, warned INT)")
        db.execute("CREATE TABLE private_subject(subject TEXT)")
        db.execute("INSERT INTO screen_clock VALUES (?, '2027-01-15', 50, ?, 0)", (DEVICE, EPOCH))


def test_reader_only_accesses_selected_counter_and_leaves_source_intact(tmp_path):
    path = tmp_path / "agent #1.sqlite3"
    build_store(path)
    before = path.read_bytes()
    reader = clock.CounterReader(path, None)
    assert reader.device == DEVICE and reader.read().used == 50
    with closing(reader.connect()) as db:
        for sql in ("SELECT warned FROM screen_clock", "SELECT subject FROM private_subject",
                    "UPDATE screen_clock SET used=1", "ATTACH 'more.db' AS other"):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(sql)
    with pytest.raises(ValueError, match="checkpoint"):
        clock.CounterReader(path, OTHER)
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]
    absent = tmp_path / "absent.sqlite3"
    with pytest.raises(FileNotFoundError):
        clock.CounterReader(absent, DEVICE)
    assert not absent.exists()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO screen_clock VALUES (?, '2027-01-15', 13, ?, 0)", (OTHER, EPOCH))
    with pytest.raises(ValueError, match="--device"):
        clock.CounterReader(path, None)
    assert clock.CounterReader(path, OTHER).read().used == 13


def test_wal_source_never_opens_sqlite_reader_or_creates_sidecars(tmp_path, monkeypatch):
    path = tmp_path / "wal.sqlite3"
    build_store(path, wal=True)
    before = path.read_bytes()
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("WAL connection opened"))
    with pytest.raises(ValueError, match="rollback journal"):
        clock.CounterReader(path, DEVICE)
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


def test_new_reports_hold_aggregate_results_only(tmp_path, hour):
    report = clock.evaluate(hour, DEVICE, 3600, 3600)
    first, second = clock.save(report, tmp_path), clock.save(report, tmp_path)
    assert first != second and json.loads(first.read_text(encoding="utf-8")) == report
    body = second.read_text(encoding="utf-8")
    assert DEVICE not in body and "subject" not in body and "sqlite" not in body


def test_cli_cancel_does_not_start_measurement_or_write_report(tmp_path, monkeypatch):
    path = tmp_path / "agent.sqlite3"
    build_store(path)
    output = tmp_path / "reports"
    monkeypatch.setattr(sys, "argv", ["clock_accuracy", "--store", str(path), "--output-dir", str(output)])
    monkeypatch.setattr("builtins.input", lambda _: "cancel")
    monkeypatch.setattr(clock, "collect", lambda *a, **k: pytest.fail("measurement started"))
    with pytest.raises(SystemExit) as exit_info:
        clock.main()
    assert exit_info.value.code == 1 and not output.exists()


def test_cli_fake_hour_writes_a_new_summary(tmp_path, hour, monkeypatch):
    path = tmp_path / "lab.sqlite3"
    build_store(path)
    before = path.read_bytes()
    output = tmp_path / "reports"
    monkeypatch.setattr(sys, "argv", ["clock_accuracy", "--store", str(path), "--device", DEVICE,
                                     "--output-dir", str(output)])
    monkeypatch.setattr(clock, "collect", lambda *_: hour)
    answers = iter(["DO 1 GIO", "3600", "3600"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    clock.main()
    files = list(output.glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text(encoding="utf-8"))["result"] == "within_tolerance"
    assert path.read_bytes() == before


def test_operator_typo_is_recoverable_and_blank_is_inconclusive(monkeypatch):
    answers = iter(["abc", "NaN", "-2", "1500,25", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert clock.reference_reading("") == 1500.25 and math.isnan(clock.reference_reading(""))
