from datetime import date, timedelta

import pytest
import yaml

from generate_reference import ROOT, build_reference
from scenarios import cancelled_legs, load_scenarios
from simulate import load_calendar, run

CFG = yaml.safe_load((ROOT / "config.yml").read_text())
START, END = CFG["window"]["start"], CFG["window"]["end"]
SCENARIOS = load_scenarios(ROOT / "scenarios.yml")
CUTOFF_UTC_HOUR = 6  # S09 cutoff 07:35 Casablanca (UTC+1) is 06:35 UTC

S07_TRIP, S08_TRIP, S09_TRIP, S10_TRIP, S11_TRIP = (
    2025110531, 2025111251, 2025120221, 2025121742, 2025121611)


@pytest.fixture(scope="module")
def ref():
    return build_reference(CFG)


@pytest.fixture(scope="module")
def cal():
    return load_calendar(ROOT / "data" / "calendar_ma.csv")


@pytest.fixture(scope="module")
def data(ref, cal):
    return run(ref, cal, CFG, START, END, SCENARIOS)[0]


def key(e):
    return (e["trip_id"], e["student_id"], e["stop_id"], e["event_type"],
            e["scheduled_ts"], e["actual_ts"])


def trip(data, trip_id):
    return next(t for t in data["trips"] if t["trip_id"] == trip_id)


def test_s06_duplicates_are_about_one_percent_and_identical_but_for_arrival(data):
    events = data["stop_events"]
    unique = {key(e) for e in events}
    duplicates = len(events) - len(unique)
    assert 0.007 < duplicates / len(unique) < 0.013
    first_seen = {}
    for e in events:
        k = key(e)
        first_seen.setdefault(k, e["received_at"])
    # a retry always arrives after the original
    for e in events:
        assert e["received_at"] >= first_seen[key(e)]
    per_student_trip = {}
    for k in unique:
        per_student_trip[(k[0], k[1])] = per_student_trip.get((k[0], k[1]), 0) + 1
    assert max(per_student_trip.values()) == 1  # one logical event per student and trip


def test_s07_exactly_one_trip_has_no_driver(data):
    no_driver = [t["trip_id"] for t in data["trips"] if t["driver_id"] is None]
    assert no_driver == [S07_TRIP]


def test_s08_cancelled_trip_is_empty_and_the_odometer_does_not_advance(data):
    cancelled = [t for t in data["trips"] if t["status"] == "CANCELLED"]
    assert [t["trip_id"] for t in cancelled] == [S08_TRIP]
    t = cancelled[0]
    assert t["cancel_reason"] == "BREAKDOWN"
    assert all(t[c] is None for c in (
        "actual_start_ts", "first_stop_ts", "last_stop_ts", "actual_arrival_ts",
        "odometer_start_km", "odometer_end_km"))
    assert not [e for e in data["stop_events"] if e["trip_id"] == S08_TRIP]
    assert not [p for p in data["gps_pings"] if p["trip_id"] == S08_TRIP]
    last = {}
    for tr in sorted(data["trips"], key=lambda x: x["trip_id"]):
        if tr["status"] == "CANCELLED":
            continue
        if tr["bus_id"] in last:
            assert tr["odometer_start_km"] == last[tr["bus_id"]]
        last[tr["bus_id"]] = tr["odometer_end_km"]


def test_s09_trip_is_in_progress_then_completed(ref, cal, data):
    day = date(2025, 12, 2)
    partial_run = run(ref, cal, CFG, day, day, SCENARIOS)[0]
    partial = trip(partial_run, S09_TRIP)
    complete = trip(data, S09_TRIP)
    assert partial["status"] == "IN_PROGRESS" and complete["status"] == "COMPLETED"
    assert partial["last_stop_ts"] is None and partial["actual_arrival_ts"] is None
    assert partial["odometer_end_km"] is None
    assert partial["first_stop_ts"] is not None
    assert partial["updated_at"].hour == CUTOFF_UTC_HOUR and partial["updated_at"].minute == 35
    # same trip, more data later: the partial rows are a subset of the final ones
    part_events = [e for e in partial_run["stop_events"] if e["trip_id"] == S09_TRIP]
    full_events = [e for e in data["stop_events"] if e["trip_id"] == S09_TRIP]
    assert 0 < len(part_events) < len(full_events)
    assert all(e["received_at"] <= partial["updated_at"] for e in part_events)
    assert {key(e) for e in part_events} <= {key(e) for e in full_events}
    assert all(p["ping_ts"] <= partial["updated_at"]
               for p in partial_run["gps_pings"] if p["trip_id"] == S09_TRIP)
    # the other trips of that day are untouched
    assert sum(t["status"] == "IN_PROGRESS" for t in partial_run["trips"]) == 1


def test_s10_orphan_event_for_a_missing_student(ref, data):
    known = {s["student_id"] for s in ref["students"]}
    orphans = [e for e in data["stop_events"] if e["student_id"] not in known]
    assert {key(e) for e in orphans} and len({key(e) for e in orphans}) == 1
    assert {e["trip_id"] for e in orphans} == {S10_TRIP}
    route_4_stops = {s["stop_id"] for s in ref["stops"] if s["route_id"] == 4}
    assert orphans[0]["stop_id"] in route_4_stops


def test_s11_one_event_arrives_three_days_late(data):
    late = [e for e in data["stop_events"]
            if e["actual_ts"] is not None
            and e["received_at"] - e["actual_ts"] >= timedelta(days=3)]
    assert len({key(e) for e in late}) == 1
    assert {e["trip_id"] for e in late} == {S11_TRIP}
    assert min(e["received_at"] for e in late).date() == date(2025, 12, 19)


def test_checks_hold_for_all_events(data):
    for e in data["stop_events"]:
        assert (e["event_type"] == "NO_SHOW") == (e["actual_ts"] is None)
        if e["actual_ts"] is not None:
            assert e["received_at"] >= e["actual_ts"]
    cancelled = {t["trip_id"] for t in data["trips"] if t["status"] == "CANCELLED"}
    assert not [e for e in data["stop_events"] if e["trip_id"] in cancelled]


def test_unknown_scenario_kind_is_rejected(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("scenarios:\n  - {id: X1, kind: typo, date: 2025-10-14}\n")
    with pytest.raises(ValueError):
        load_scenarios(bad)


def test_cancelling_a_non_school_day_is_rejected():
    sc = [{"id": "X", "kind": "cancel_trip", "date": date(2025, 12, 9),
           "route_id": 1, "direction": "TO_SCHOOL", "reason": "BREAKDOWN"}]
    with pytest.raises(ValueError):
        cancelled_legs(sc, {date(2025, 12, 16): 0})


def test_in_progress_cutoff_outside_the_trip_is_rejected(ref, cal):
    bad = [{**sc, "cutoff": "06:00"} if sc["id"] == "S09" else sc for sc in SCENARIOS]
    day = date(2025, 12, 2)
    with pytest.raises(ValueError):
        run(ref, cal, CFG, day, day, bad)