from datetime import datetime

import pytest

from agent.policy import Scope
from agent.screen_time import CounterStore, ScreenClock, VIETNAM
from agent.simulate import demo_policy

MONDAY = datetime(2026, 11, 23, 8, tzinfo=VIETNAM).timestamp()
ACTIVE = Scope(True, True, True)


def sample(clock, policy, tick, **changes):
    inputs = dict(monotonic=tick, wall=MONDAY + tick, scope=ACTIVE)
    return clock.sample(policy, **(inputs | changes))


def test_exact_three_minute_quota_including_last_minute_to_save():
    clock, policy = ScreenClock(), demo_policy()
    readings = [sample(clock, policy, i) for i in range(181)]
    assert readings[119].action == "active"
    assert readings[120].action == "grace"
    assert readings[120].warnings == (60,)
    assert readings[179].action == "grace"
    assert readings[180].action == "lock"


def test_ten_five_one_minute_warnings_only_once():
    clock, policy = ScreenClock(), demo_policy() | {"weekday_minutes": 11}
    warnings = [(i, value) for i in range(661) for value in sample(clock, policy, i).warnings]
    assert warnings == [(60, 600), (360, 300), (600, 60)]


def test_schedule_transition_on_next_sample():
    policy, clock = demo_policy(), ScreenClock()
    policy["schedule"][0] = "1" * 17 + "0" * 31
    assert sample(clock, policy, 0, wall=MONDAY + 1799).action == "active"
    assert sample(clock, policy, 2, wall=MONDAY + 1801).action == "lock"


def test_break_lists_all_causes_and_finds_next_slot_across_sunday_midnight():
    sunday = datetime(2026, 11, 29, 23, 47, tzinfo=VIETNAM).timestamp()
    schedule = ["0" * 48 for _ in range(7)]
    schedule[0] = "0" * 16 + "1" + "0" * 31
    policy = demo_policy() | {"schedule": schedule, "weekend_minutes": 0}
    clock = ScreenClock()
    clock.state.parent_locked = True
    reading = sample(clock, policy, 1, wall=sunday)
    assert reading.rest_reasons == ("parent", "schedule", "quota")
    assert reading.next_slot == datetime(2026, 11, 30, 8, tzinfo=VIETNAM)
    policy["schedule"][0] = "0" * 48
    assert sample(clock, policy, 2, wall=sunday + 1).next_slot is None


def test_weekend_quota():
    saturday = datetime(2026, 11, 28, 8, tzinfo=VIETNAM).timestamp()
    policy = demo_policy() | {"weekend_minutes": 9}
    assert sample(ScreenClock(), policy, 0, wall=saturday).remaining_seconds == 540


def test_idle_over_five_minutes():
    clock, policy = ScreenClock(), demo_policy() | {"weekday_minutes": 90}
    for i in range(0, 401, 2):
        sample(clock, policy, i, idle_seconds=i)
    assert clock.state.used == 300


@pytest.mark.parametrize("transition", [
    {"scope": Scope(False, True, True)}, {"scope": Scope(True, False, True)},
    {"scope": Scope(True, True, False)}, {"locked": True},
])
def test_unattended_session_never_charged(transition):
    clock, policy = ScreenClock(), demo_policy()
    for i in range(10):
        sample(clock, policy, i, **transition)
    sample(clock, policy, 10)
    assert clock.state.used == 0
    sample(clock, policy, 12)
    assert clock.state.used == 2


def test_sleep_gap_and_restart_preserve_used_time(tmp_path):
    clock, policy = ScreenClock(), demo_policy()
    sample(clock, policy, 0)
    sample(clock, policy, 2)
    sample(clock, policy, 1800)
    store = CounterStore(tmp_path / "clock.db", "dev")
    store.save(clock.state)
    restarted = ScreenClock(store.load())
    assert sample(restarted, policy, 0, wall=MONDAY + 3600).remaining_seconds == 178
    assert sample(restarted, policy, 1, wall=MONDAY + 3601).remaining_seconds == 177


def test_rollback_three_hours_logged_once_even_after_restart(tmp_path):
    clock, policy = ScreenClock(), demo_policy()
    sample(clock, policy, 0)
    before = sample(clock, policy, 2)
    after = sample(clock, policy, 3, wall=MONDAY - 10800)
    assert after.clock_alert and after.remaining_seconds < before.remaining_seconds
    store = CounterStore(tmp_path / "clock.db", "dev")
    store.save(clock.state)
    restarted = ScreenClock(store.load())
    again = sample(restarted, policy, 0, wall=MONDAY - 10800)
    assert not again.clock_alert and restarted.state.clock_alerts == 1
    assert after.remaining_seconds == again.remaining_seconds


def test_server_reference_and_day_boundary():
    midnight = datetime(2026, 11, 24, tzinfo=VIETNAM).timestamp()
    clock, policy = ScreenClock(), demo_policy()
    sample(clock, policy, 0, wall=midnight - 10801, server_time=midnight - 1)
    sample(clock, policy, 2, wall=midnight - 10799)
    assert clock.state.day == "2026-11-24" and clock.state.used == 1


def test_continuous_simulated_hour():
    clock, policy = ScreenClock(), demo_policy() | {"weekday_minutes": 90}
    for i in range(0, 3601, 2):
        sample(clock, policy, i)
    assert clock.state.used == 3600


def test_cli_command_retries_without_resetting_credit_on_first_sample(tmp_path):
    from uuid import uuid4

    now = 1_800_000_000
    store = CounterStore(tmp_path / "time.sqlite3", "demo")
    command = {"id": str(uuid4()), "action": "add_15", "expires_at": now + 120}
    store.apply_simulation(command, now)
    store.apply_simulation(command, now + 1)
    clock = ScreenClock(store.load())
    reading = clock.sample(demo_policy(), monotonic=0, wall=now, scope=Scope(True, True, True))
    assert clock.state.extra_seconds == 900
    assert reading.remaining_seconds == 1080


def test_command_state_persists_and_retry_is_idempotent(tmp_path):
    clock, policy = ScreenClock(), demo_policy()
    sample(clock, policy, 0)
    store = CounterStore(tmp_path / "clock.db", "dev")
    command = {"id": "1", "action": "add_15", "expires_at": MONDAY + 60}
    store.apply_command(command, clock, MONDAY)
    restarted = ScreenClock(store.load())
    store.apply_command(command, restarted, MONDAY + 1)
    assert sample(restarted, policy, 0).remaining_seconds == 1080
    store.apply_command(command | {"id": "2", "action": "lock"}, restarted, MONDAY + 2)
    assert sample(restarted, policy, 1).action == "lock"
    store.apply_command(command | {"id": "3", "action": "unlock"}, restarted, MONDAY + 3)
    assert sample(restarted, policy, 2).action == "active"
    with pytest.raises(ValueError):
        store.apply_command(command, restarted, MONDAY + 60)


@pytest.mark.parametrize("cause", ["deadline", "hidden"])
def test_recheck_command_after_acquiring_writer(tmp_path, cause):
    from contextlib import contextmanager

    store = CounterStore(tmp_path / "time.sqlite3", "child")
    original = store.write_transaction
    elapsed = [0]
    visible = [True]

    @contextmanager
    def delayed():
        with original() as db:
            elapsed[0] = 2
            visible[0] = cause != "hidden"
            yield db

    store.write_transaction = delayed
    command = {"id": "1", "action": "add_15", "expires_at": MONDAY + (1 if cause == "deadline" else 10)}
    with pytest.raises(ValueError, match="hết hạn|tạm dừng"):
        store.apply_command(command, None, lambda: MONDAY + elapsed[0], permitted=lambda: visible[0])
    assert store.load().extra_seconds == 0
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM applied_time_commands").fetchone()[0] == 0
