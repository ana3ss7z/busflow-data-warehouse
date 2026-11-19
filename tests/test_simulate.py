import itertools
from collections import Counter
from datetime import date
from statistics import mean

import pytest
import yaml

from generate_reference import ROOT, build_reference, checksum
from simulate import load_calendar, previous_weekday, simulate

CFG = yaml.safe_load((ROOT / "config.yml").read_text())
START, END = CFG["window"]["start"], CFG["window"]["end"]


@pytest.fixture(scope="module")
def ref():
    return build_reference(CFG)


@pytest.fixture(scope="module")
def cal():
    return load_calendar(ROOT / "data" / "calendar_ma.csv")


@pytest.fixture(scope="module")
def full(ref, cal):
    return simulate(ref, cal, CFG, START, END)


def test_volumes_match_the_spec(full, cal):
    school_days = sum(cal.values())
    assert len(full["trips"]) == 14 * school_days
    assert 50_000 <= len(full["stop_events"]) <= 60_000
    assert 150_000 <= len(full["gps_pings"]) <= 200_000


def test_trips_only_on_school_days_and_unique(full, cal):
    assert all(cal[t["service_date"]] for t in full["trips"])
    keys = [(t["service_date"], t["route_id"], t["direction"]) for t in full["trips"]]
    assert len(keys) == len(set(keys))


def test_a_day_is_the_same_whatever_the_range(ref, cal, full):
    day = date(2025, 10, 14)
    alone = simulate(ref, cal, CFG, day, day)
    from_full = {
        "trips": [t for t in full["trips"] if t["service_date"] == day],
        "stop_events": [e for e in full["stop_events"] if e["trip_id"] // 100 == 20251014],
        "gps_pings": [p for p in full["gps_pings"] if p["trip_id"] // 100 == 20251014],
    }
    assert checksum(alone) == checksum(from_full)


def test_dates_outside_the_calendar_are_rejected(ref, cal):
    with pytest.raises(ValueError):
        simulate(ref, cal, CFG, date(2026, 3, 2), date(2026, 3, 2))


def test_event_rules(full):
    for e in full["stop_events"]:
        assert (e["event_type"] == "NO_SHOW") == (e["actual_ts"] is None)
        if e["actual_ts"] is not None:
            assert e["received_at"] >= e["actual_ts"]
    per_trip_student = Counter((e["trip_id"], e["student_id"]) for e in full["stop_events"])
    assert max(per_trip_student.values()) == 1  # no duplicates yet (they are scenario S06)


def test_trip_timeline_is_ordered(full):
    for t in full["trips"]:
        assert t["actual_start_ts"] < t["first_stop_ts"] <= t["last_stop_ts"]
        assert t["last_stop_ts"] < t["actual_arrival_ts"]
        assert t["odometer_end_km"] > t["odometer_start_km"]


def test_odometer_is_continuous_per_bus(full):
    last = {}
    for t in sorted(full["trips"], key=lambda t: t["trip_id"]):
        if t["bus_id"] in last:
            assert t["odometer_start_km"] == last[t["bus_id"]]
        last[t["bus_id"]] = t["odometer_end_km"]


def test_gps_pings_every_20_seconds(full):
    by_trip = {}
    for p in full["gps_pings"]:
        by_trip.setdefault(p["trip_id"], []).append(p["ping_ts"])
    trips = {t["trip_id"]: t for t in full["trips"]}
    for trip_id, times in list(by_trip.items())[:50]:
        assert times[0] == trips[trip_id]["actual_start_ts"]
        assert times[-1] <= trips[trip_id]["actual_arrival_ts"]
        gaps = {(b - a).total_seconds() for a, b in itertools.pairwise(times)}
        assert gaps == {20.0}


def test_friday_has_more_no_shows(full):
    day = {t["trip_id"]: t["service_date"] for t in full["trips"]}
    friday = [e["event_type"] == "NO_SHOW" for e in full["stop_events"]
              if day[e["trip_id"]].weekday() == 4]
    other = [e["event_type"] == "NO_SHOW" for e in full["stop_events"]
             if day[e["trip_id"]].weekday() != 4]
    assert mean(friday) > mean(other)
    assert 0.03 < mean(other) < 0.05


def test_first_day_after_a_holiday_is_later(full, cal):
    day = {t["trip_id"]: t["service_date"] for t in full["trips"]}
    after, rest = [], []
    for e in full["stop_events"]:
        if e["actual_ts"] is None:
            continue
        delay = (e["actual_ts"] - e["scheduled_ts"]).total_seconds() / 60
        d = day[e["trip_id"]]
        (rest if cal.get(previous_weekday(d), False) else after).append(delay)
    assert mean(after) > mean(rest) + 1.0