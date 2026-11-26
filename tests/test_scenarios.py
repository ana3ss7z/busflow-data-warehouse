import itertools
from datetime import date

import pytest
import yaml

from generate_reference import ROOT, build_reference, checksum, haversine_km
from scenarios import apply_day, initial_state, load_scenarios
from simulate import load_calendar, run, simulate

CFG = yaml.safe_load((ROOT / "config.yml").read_text())
START, END = CFG["window"]["start"], CFG["window"]["end"]
SCENARIOS = load_scenarios(ROOT / "scenarios.yml")


@pytest.fixture(scope="module")
def ref():
    return build_reference(CFG)


@pytest.fixture(scope="module")
def cal():
    return load_calendar(ROOT / "data" / "calendar_ma.csv")


@pytest.fixture(scope="module")
def base(ref, cal):
    return simulate(ref, cal, CFG, START, END)


@pytest.fixture(scope="module")
def full(ref, cal):
    return run(ref, cal, CFG, START, END, SCENARIOS)


def state_on(ref, cal, day):
    return run(ref, cal, CFG, START, day, SCENARIOS)[1]


def service_date(data):
    return {t["trip_id"]: t["service_date"] for t in data["trips"]}


def test_baseline_without_scenarios_is_stable(base):
    # If this fails after a deliberate change to the simulator, update the constant.
    assert checksum(base).startswith("7e4ecef6416b")


def test_scenario_dates_exist_in_the_calendar(cal):
    assert all(sc["date"] in cal for sc in SCENARIOS if "date" in sc)


def test_s02_student_moves_and_later_events_follow(ref, full):
    data, state = full
    day_of = service_date(data)
    new_stop = next(s["stop_id"] for s in ref["stops"]
                    if s["route_id"] == 7 and s["stop_sequence"] == 4)
    old_stop = 25
    stops = {(day_of[e["trip_id"]] >= date(2025, 10, 14)): set() for e in data["stop_events"]}
    for e in data["stop_events"]:
        if e["student_id"] == 17:
            stops[day_of[e["trip_id"]] >= date(2025, 10, 14)].add(e["stop_id"])
    assert stops[False] == {old_stop} and stops[True] == {new_stop}
    student = state["students"][17]
    assert student["home_neighborhood"] == "Wislane"
    assert student["home_effective_from"] == date(2025, 10, 14)
    enrollments = [e for e in state["enrollments"] if e["student_id"] == 17]
    assert len(enrollments) == 2
    closed = next(e for e in enrollments if e["valid_to"] is not None)
    assert closed["valid_to"] == date(2025, 10, 13)
    assert sum(e["valid_to"] is None for e in enrollments) == 1


def test_s03_back_dated_correction(ref, cal):
    before = state_on(ref, cal, date(2025, 10, 19))["students"][42]
    after = state_on(ref, cal, date(2025, 10, 20))["students"][42]
    assert before["home_neighborhood"] == "Toulal"
    assert after["home_neighborhood"] == "Ismailia"
    assert after["home_effective_from"] == date(2025, 10, 1)
    assert after["updated_at"].date() == date(2025, 10, 20)  # entered later than effective


def test_s04_grade_and_neighborhood_change_the_same_day(ref, cal):
    before = state_on(ref, cal, date(2025, 10, 27))["students"][77]
    after = state_on(ref, cal, date(2025, 10, 28))["students"][77]
    assert (before["grade_level"], before["home_neighborhood"]) == (9, "Sebbata")
    assert (after["grade_level"], after["home_neighborhood"]) == (10, "Sidi Baba")
    assert after["updated_at"].date() == date(2025, 10, 28)


def test_s05_rename_then_relocation(ref, cal):
    old = {s["stop_id"]: s for s in ref["stops"]}
    day2 = state_on(ref, cal, date(2025, 11, 3))["stops"]
    assert day2[12]["stop_name"] == "Hamria - Marche Central"
    assert (day2[30]["latitude"], day2[30]["longitude"]) == (
        old[30]["latitude"], old[30]["longitude"])  # not moved yet
    day3 = state_on(ref, cal, date(2025, 11, 17))["stops"]
    moved_m = 1000 * haversine_km(
        (old[30]["latitude"], old[30]["longitude"]), (day3[30]["latitude"], day3[30]["longitude"]))
    assert 200 < moved_m < 230  # sqrt(180^2 + 120^2) = 216 m
    assert day3[30]["location_effective_from"] == date(2025, 11, 17)


def test_s05_gps_follows_the_relocated_stop(ref, full):
    data, state = full
    old = next(s for s in ref["stops"] if s["stop_id"] == 30)
    new = state["stops"][30]

    def stationary_near(day, point):
        trip_id = int(day.strftime("%Y%m%d")) * 100 + 4 * 10 + 1  # route 4, morning
        return any(
            p["trip_id"] == trip_id and p["speed_kmh"] == 0
            and 1000 * haversine_km(point, (p["latitude"], p["longitude"])) < 30
            for p in data["gps_pings"])

    old_pt = (old["latitude"], old["longitude"])
    new_pt = (new["latitude"], new["longitude"])
    assert stationary_near(date(2025, 11, 14), old_pt)
    assert stationary_near(date(2025, 11, 19), new_pt)
    assert not stationary_near(date(2025, 11, 19), old_pt)


def test_s12_bus_capacity_changes_on_its_date(ref, cal):
    assert state_on(ref, cal, date(2025, 12, 17))["buses"][4]["seat_capacity"] == 48
    after = state_on(ref, cal, date(2025, 12, 18))["buses"][4]
    assert after["seat_capacity"] == 45
    assert after["capacity_effective_from"] == date(2025, 12, 18)


def test_enrollments_stay_consistent(full):
    _, state = full
    by_student = {}
    for e in state["enrollments"]:
        by_student.setdefault(e["student_id"], []).append(e)
    assert len(by_student) == 300
    for rows in by_student.values():
        assert sum(e["valid_to"] is None for e in rows) == 1
        rows.sort(key=lambda e: e["valid_from"])
        for a, b in itertools.pairwise(rows):
            assert a["valid_to"] < b["valid_from"]  # no overlap


def test_no_route_exceeds_its_bus_capacity(full):
    _, state = full
    riders = {}
    for e in state["enrollments"]:
        if e["valid_to"] is None:
            riders[e["route_id"]] = riders.get(e["route_id"], 0) + 1
    for route_id, n in riders.items():
        assert n <= state["buses"][route_id]["seat_capacity"]


def event_key(e):
    """An event without its arrival time: duplicates (S06) share the same key."""
    return (e["trip_id"], e["student_id"], e["stop_id"], e["event_type"],
            e["scheduled_ts"], e["actual_ts"])


def test_scenarios_only_touch_what_they_should(base, full):
    data, _ = full
    # trips: only the S07 and S08 trips change, plus odometers after the S08 cancellation
    targeted = {2025110531, 2025111251}
    new = {t["trip_id"]: t for t in data["trips"]}
    assert len(new) == len(base["trips"])

    def no_odometer(t):
        return {k: v for k, v in t.items() if not k.startswith("odometer")}

    for t in base["trips"]:
        if t["trip_id"] not in targeted:
            assert no_odometer(new[t["trip_id"]]) == no_odometer(t)
    # events: routes involved in S02, S04, S05b, S07, S08 and S10-S11 aside, same events
    # (duplicates added by S06 are ignored here)
    affected = {1, 3, 4, 5, 7}
    route_of = {t["trip_id"]: t["route_id"] for t in base["trips"]}
    assert {event_key(e) for e in data["stop_events"] if route_of[e["trip_id"]] not in affected} \
        == {event_key(e) for e in base["stop_events"] if route_of[e["trip_id"]] not in affected}
    # GPS: only route 4 (relocated stop) and the cancelled route 5 trip differ
    other = [p for p in data["gps_pings"] if route_of[p["trip_id"]] not in {4, 5}]
    assert other == [p for p in base["gps_pings"] if route_of[p["trip_id"]] not in {4, 5}]


def test_a_scenario_that_changes_nothing_is_rejected(ref):
    state = initial_state(ref)
    noop = {"id": "X", "kind": "bus_capacity", "date": date(2025, 12, 18),
            "bus_id": 3, "new_capacity": 45}  # bus 3 already has 45 seats
    with pytest.raises(ValueError):
        apply_day(state, date(2025, 12, 18), [noop])