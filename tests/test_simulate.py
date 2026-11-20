
import itertools
from collections import Counter
from datetime import date
from statistics import mean

import pytest
import yaml

from generate_reference import ROOT, build_reference, checksum
from simulate import load_calendar, previous_weekday, simulate

CFG = yaml.safe_load(
    (ROOT / "config.yml").read_text()
)

START = CFG["window"]["start"]
END = CFG["window"]["end"]


# ---------------------------------------------------------------------
# SHARED FIXTURES
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def ref():
    return build_reference(CFG)


@pytest.fixture(scope="module")
def cal():
    return load_calendar(
        ROOT / "data" / "calendar_ma.csv"
    )


@pytest.fixture(scope="module")
def full(ref, cal):
    return simulate(
        ref,
        cal,
        CFG,
        START,
        END,
    )


# ---------------------------------------------------------------------
# VOLUME AND CALENDAR TESTS
# ---------------------------------------------------------------------

def test_volumes_match_the_spec(full, cal):

    school_days = sum(cal.values())

    # 7 routes x 2 directions = 14 trips per school day.
    assert len(full["trips"]) == 14 * school_days

    assert 50_000 <= len(full["stop_events"]) <= 60_000

    assert 150_000 <= len(full["gps_pings"]) <= 200_000


def test_trips_only_on_school_days_and_unique(full, cal):

    assert all(
        cal[trip["service_date"]]
        for trip in full["trips"]
    )

    keys = [
        (
            trip["service_date"],
            trip["route_id"],
            trip["direction"],
        )
        for trip in full["trips"]
    ]

    assert len(keys) == len(set(keys))


def test_a_day_is_the_same_whatever_the_range(ref, cal, full):

    day = date(2025, 10, 14)

    # Simulate October 14 alone.
    alone = simulate(
        ref,
        cal,
        CFG,
        day,
        day,
    )

    # Extract October 14 from the complete simulation.
    from_full = {

        "trips": [
            trip
            for trip in full["trips"]
            if trip["service_date"] == day
        ],

        "stop_events": [
            event
            for event in full["stop_events"]
            if event["trip_id"] // 100 == 20251014
        ],

        "gps_pings": [
            ping
            for ping in full["gps_pings"]
            if ping["trip_id"] // 100 == 20251014
        ],
    }

    assert checksum(alone) == checksum(from_full)


def test_dates_outside_the_calendar_are_rejected(ref, cal):

    with pytest.raises(ValueError):

        simulate(
            ref,
            cal,
            CFG,
            date(2026, 3, 2),
            date(2026, 3, 2),
        )


# ---------------------------------------------------------------------
# STUDENT EVENT TESTS
# ---------------------------------------------------------------------

def test_event_rules(full):

    for event in full["stop_events"]:

        # NO_SHOW must have no actual timestamp.
        assert (
            event["event_type"] == "NO_SHOW"
        ) == (
            event["actual_ts"] is None
        )

        # Received timestamp cannot precede actual timestamp.
        if event["actual_ts"] is not None:

            assert event["received_at"] >= event["actual_ts"]

    per_trip_student = Counter(
        (
            event["trip_id"],
            event["student_id"],
        )
        for event in full["stop_events"]
    )

    # No duplicate student events within the same trip.
    # Duplicates will be introduced later by scenario S06.
    assert max(per_trip_student.values()) == 1


# ---------------------------------------------------------------------
# TRIP TIMELINE TESTS
# ---------------------------------------------------------------------

def test_trip_timeline_is_ordered(full):

    for trip in full["trips"]:

        assert (
            trip["actual_start_ts"]
            < trip["first_stop_ts"]
            <= trip["last_stop_ts"]
        )

        assert (
            trip["last_stop_ts"]
            < trip["actual_arrival_ts"]
        )

        assert (
            trip["odometer_end_km"]
            > trip["odometer_start_km"]
        )


def test_odometer_is_continuous_per_bus(full):

    last = {}

    for trip in sorted(
        full["trips"],
        key=lambda t: t["trip_id"],
    ):

        bus_id = trip["bus_id"]

        if bus_id in last:

            assert (
                trip["odometer_start_km"]
                == last[bus_id]
            )

        last[bus_id] = trip["odometer_end_km"]


# ---------------------------------------------------------------------
# GPS TESTS
# ---------------------------------------------------------------------

def test_gps_pings_every_20_seconds(full):

    by_trip = {}

    for ping in full["gps_pings"]:

        by_trip.setdefault(
            ping["trip_id"],
            [],
        ).append(ping["ping_ts"])

    trips = {
        trip["trip_id"]: trip
        for trip in full["trips"]
    }

    for trip_id, times in list(by_trip.items())[:50]:

        assert (
            times[0]
            == trips[trip_id]["actual_start_ts"]
        )

        assert (
            times[-1]
            <= trips[trip_id]["actual_arrival_ts"]
        )

        gaps = {
            (b - a).total_seconds()
            for a, b in itertools.pairwise(times)
        }

        assert gaps == {20.0}


# ---------------------------------------------------------------------
# BUSINESS PATTERN TESTS
# ---------------------------------------------------------------------

def test_friday_has_more_no_shows(full):

    day = {
        trip["trip_id"]: trip["service_date"]
        for trip in full["trips"]
    }

    friday = [
        event["event_type"] == "NO_SHOW"
        for event in full["stop_events"]
        if day[event["trip_id"]].weekday() == 4
    ]

    other = [
        event["event_type"] == "NO_SHOW"
        for event in full["stop_events"]
        if day[event["trip_id"]].weekday() != 4
    ]

    assert mean(friday) > mean(other)

    assert 0.03 < mean(other) < 0.05


def test_first_day_after_a_holiday_is_later(full, cal):

    day = {
        trip["trip_id"]: trip["service_date"]
        for trip in full["trips"]
    }

    after = []
    rest = []

    for event in full["stop_events"]:

        if event["actual_ts"] is None:
            continue

        delay = (
            event["actual_ts"] - event["scheduled_ts"]
        ).total_seconds() / 60

        current_day = day[event["trip_id"]]

        if not cal.get(
            previous_weekday(current_day),
            False,
        ):
            after.append(delay)
        else:
            rest.append(delay)

    assert mean(after) > mean(rest) + 1.0


# ---------------------------------------------------------------------
# NEW TEST 1: BUS AND DRIVER ASSIGNMENTS
# ---------------------------------------------------------------------

def test_bus_and_driver_assignment_is_valid(ref, full):

    bus_ids = {
        bus["bus_id"]
        for bus in ref["buses"]
    }

    driver_ids = {
        driver["driver_id"]
        for driver in ref["drivers"]
    }

    # Every trip must reference an existing bus and driver.
    for trip in full["trips"]:

        assert trip["bus_id"] in bus_ids

        assert trip["driver_id"] in driver_ids

    # Each route must have exactly one bus/driver pair.
    per_route = {
        (
            trip["route_id"],
            trip["bus_id"],
            trip["driver_id"],
        )
        for trip in full["trips"]
    }

    assert len(per_route) == len(ref["routes"])


# ---------------------------------------------------------------------
# NEW TEST 2: INSUFFICIENT BUSES
# ---------------------------------------------------------------------

def test_not_enough_buses_is_rejected(ref, cal):

    # Keep only three buses.
    short = {
        **ref,
        "buses": ref["buses"][:3],
    }

    # The simulation must reject this configuration.
    with pytest.raises(ValueError):

        simulate(
            short,
            cal,
            CFG,
            date(2025, 10, 14),
            date(2025, 10, 14),
        )


# ---------------------------------------------------------------------
# NEW TEST 3: GPS SPEED PLAUSIBILITY
# ---------------------------------------------------------------------

def test_gps_speeds_are_plausible(full):

    speeds = [
        ping["speed_kmh"]
        for ping in full["gps_pings"]
    ]

    moving = [
        speed
        for speed in speeds
        if speed > 0
    ]

    # No negative or excessively high speeds.
    assert min(speeds) >= 0

    assert max(speeds) <= 80

    # Average moving speed should be within a broad
    # plausibility range for this synthetic simulator.
    assert 5 < mean(moving) < 60
