"""Exercise measured-time boundaries and persistent alerts without changing the Windows clock."""
import argparse
import csv
import tempfile
from pathlib import Path

from agent.policy import Scope
from agent.screen_time import CounterStore, ScreenClock
from agent.simulate import demo_policy


def run(folder):
    reference = 1_800_000_000
    policy, scope = demo_policy(), Scope(True, True, True)
    result = []
    for offset in (-10800, -91, -90, 0, 90, 91, 10800):
        store = CounterStore(folder / "clock.sqlite3", f"synthetic-{offset}")
        clock = ScreenClock()
        clock.sample(policy, monotonic=0, wall=reference, server_time=reference, scope=scope)
        first = clock.sample(policy, monotonic=1, wall=reference + 1 + offset, server_time=reference + 1, scope=scope)
        # A tick between heartbeats advances from monotonic time while civil time is skewed.
        next_tick = clock.sample(policy, monotonic=2, wall=reference + 2 + offset, scope=scope)
        store.save(clock.state)
        resumed = ScreenClock(store.load())
        after = resumed.sample(policy, monotonic=0, wall=reference + 2 + offset, server_time=reference + 2, scope=scope)
        if (first.clock_alert != (abs(offset) > 90) or next_tick.clock_alert or after.clock_alert
                or resumed.state.clock_alerts != int(first.clock_alert) or resumed.state.used != 2
                or resumed.state.logical_ts != reference + 2):
            raise AssertionError(f"Unexpected clock state at offset {offset}")
        result.append(dict(wall_offset_s=offset, first_alert=first.clock_alert, duplicate_after_restart=after.clock_alert,
                           durable_alerts=resumed.state.clock_alerts, billed_s=resumed.state.used,
                           logical_elapsed_s=resumed.state.logical_ts - reference))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="openguard-clock-") as folder:
        rows = run(Path(folder))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} clock-skew boundaries verified with a temporary SQLite checkpoint")
