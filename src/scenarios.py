"""Scenario engine: changes to the source system on given days (WP2, part 2a).

The source tables keep no history: a change overwrites the row and bumps
updated_at. The engine therefore works on an in-memory "state" of the mutable
tables and applies every scenario on its entry date, in date order. The state
after day D is a pure function of the baseline data and the scenarios.
"""
import math
import random
from datetime import timedelta
from pathlib import Path

import yaml

from timeutil import to_utc


# def load_scenarios(path: Path):
#     return yaml.safe_load(path.read_text(encoding="utf-8"))["scenarios"]
def load_scenarios(path: Path):
    scenarios = yaml.safe_load(path.read_text(encoding="utf-8"))["scenarios"]
    for sc in scenarios:
        if sc["kind"] not in HANDLERS and sc["kind"] not in TRIP_HANDLERS:
            raise ValueError(f"{sc['id']}: unknown scenario kind {sc['kind']!r}")
    return scenarios

def initial_state(ref):
    return {
        "routes": {r["route_id"]: r for r in ref["routes"]},
        "students": {s["student_id"]: dict(s) for s in ref["students"]},
        "stops": {s["stop_id"]: dict(s) for s in ref["stops"]},
        "buses": {b["bus_id"]: dict(b) for b in ref["buses"]},
        "enrollments": [dict(e) for e in ref["student_route_enrollments"]],
    }


def neighborhood_of(state, route_id):
    return state["routes"][route_id]["route_name"].split(" - ", 1)[1]


def _stop_at(state, route_id, sequence):
    for s in state["stops"].values():
        if s["route_id"] == route_id and s["stop_sequence"] == sequence:
            return s
    raise ValueError(f"route {route_id} has no stop with sequence {sequence}")


def student_move(state, sc):
    """S02 / S04: the student moves; old enrollment closed the day before, new one opened."""
    day = sc["date"]
    student = state["students"][sc["student_id"]]
    new_hood = neighborhood_of(state, sc["to_route"])
    if new_hood == student["home_neighborhood"]:
        raise ValueError(f"{sc['id']}: the student already lives in {new_hood}")
    current = next(e for e in state["enrollments"]
                   if e["student_id"] == sc["student_id"] and e["valid_to"] is None)
    current["valid_to"] = day - timedelta(days=1)
    state["enrollments"].append({
        "enrollment_id": max(e["enrollment_id"] for e in state["enrollments"]) + 1,
        "student_id": sc["student_id"], "route_id": sc["to_route"],
        "pickup_stop_id": _stop_at(state, sc["to_route"], sc["to_stop_sequence"])["stop_id"],
        "valid_from": day, "valid_to": None,
    })
    student["home_neighborhood"] = new_hood
    student["home_effective_from"] = day
    if "new_grade" in sc:  # S04: a grade correction entered on the same day
        if sc["new_grade"] == student["grade_level"]:
            raise ValueError(f"{sc['id']}: the grade is already {sc['new_grade']}")
        student["grade_level"] = sc["new_grade"]
    student["updated_at"] = to_utc(day, "09:00")


def back_dated_correction(state, sc):
    """S03: an address correction entered on `date` but effective earlier."""
    student = state["students"][sc["student_id"]]
    if sc["new_neighborhood"] == student["home_neighborhood"]:
        raise ValueError(f"{sc['id']}: the neighborhood is unchanged")
    student["home_neighborhood"] = sc["new_neighborhood"]
    student["home_effective_from"] = sc["effective_from"]
    student["updated_at"] = to_utc(sc["date"], "10:00")


def stop_rename(state, sc):
    """S05a: a rename overwrites the name only (an SCD1 change in the warehouse)."""
    stop = state["stops"][sc["stop_id"]]
    if sc["new_name"] == stop["stop_name"]:
        raise ValueError(f"{sc['id']}: the stop name is unchanged")
    stop["stop_name"] = sc["new_name"]
    stop["updated_at"] = to_utc(sc["date"], "09:00")


def stop_relocate(state, sc):
    """S05b: the stop physically moves (an SCD2 change in the warehouse)."""
    stop = state["stops"][sc["stop_id"]]
    lat_shift = sc["north_m"] / 111_000
    lon_shift = sc["east_m"] / (111_000 * math.cos(math.radians(stop["latitude"])))
    stop["latitude"] = round(stop["latitude"] + lat_shift, 6)
    stop["longitude"] = round(stop["longitude"] + lon_shift, 6)
    stop["location_effective_from"] = sc["date"]
    stop["updated_at"] = to_utc(sc["date"], "09:00")


def bus_capacity(state, sc):
    """S12: seat capacity changes after a refit."""
    bus = state["buses"][sc["bus_id"]]
    if sc["new_capacity"] == bus["seat_capacity"]:
        raise ValueError(f"{sc['id']}: the capacity is unchanged")
    bus["seat_capacity"] = sc["new_capacity"]
    bus["capacity_effective_from"] = sc["date"]
    bus["updated_at"] = to_utc(sc["date"], "09:00")


HANDLERS = {
    "student_move": student_move,
    "back_dated_correction": back_dated_correction,
    "stop_rename": stop_rename,
    "stop_relocate": stop_relocate,
    "bus_capacity": bus_capacity,
}


def apply_day(state, day, scenarios):
    """Apply every source-table scenario whose entry date is `day`."""
    for sc in scenarios:
        if sc["kind"] in HANDLERS and sc["date"] == day:
            HANDLERS[sc["kind"]](state, sc)


# ------------------------------------------------------------ trip-level scenarios
# Each handler receives and returns (trip, events, pings) of ONE generated trip.
def no_driver(sc, trip, events, pings, key, end):
    """S07: nobody was recorded as driver."""
    return {**trip, "driver_id": None}, events, pings


def cancel_trip(sc, trip, events, pings, key, end):
    """S08: the trip never ran: no events, no GPS, no actual times, no odometer."""
    cancelled = {
        **trip, "status": "CANCELLED", "cancel_reason": sc["reason"],
        "actual_start_ts": None, "first_stop_ts": None, "last_stop_ts": None,
        "actual_arrival_ts": None, "odometer_start_km": None, "odometer_end_km": None,
        "updated_at": trip["scheduled_start_ts"] - timedelta(minutes=25),
    }
    return cancelled, [], []


def in_progress(sc, trip, events, pings, key, end):
    """S09: if the simulated clock stops on this date, the trip is still running at `cutoff`.

    A later run that covers the date rewrites the trip as completed (same trip_id).
    """
    if end != sc["date"]:
        return trip, events, pings
    cutoff = to_utc(sc["date"], sc["cutoff"])
    if not trip["first_stop_ts"] <= cutoff < trip["last_stop_ts"]:
        raise ValueError(f"{sc['id']}: the cutoff must fall between the first and last stop")
    partial = {
        **trip, "status": "IN_PROGRESS", "last_stop_ts": None, "actual_arrival_ts": None,
        "odometer_end_km": None, "updated_at": cutoff,
    }
    return (partial, [e for e in events if e["received_at"] <= cutoff],
            [p for p in pings if p["ping_ts"] <= cutoff])


def orphan_event(sc, trip, events, pings, key, end):
    """S10: an event for a student that does not exist in the source."""
    template = next(e for e in events[len(events) // 2:] if e["actual_ts"] is not None)
    return trip, events + [{**template, "student_id": sc["student_id"]}], pings


def late_event(sc, trip, events, pings, key, end):
    """S11: one event reaches the server days after the trip."""
    i = next(i for i, e in enumerate(events) if e["actual_ts"] is not None)
    late = {**events[i], "received_at": events[i]["actual_ts"]
            + timedelta(days=sc["delay_days"], minutes=2)}
    return trip, events[:i] + [late] + events[i + 1:], pings


def duplicate_events(sc, trip, events, pings, key, end):
    """S06: app retries resend some events; the copy arrives a little later."""
    rng = random.Random(key + ":dup")  # own stream: never shifts the baseline numbers
    copies = [{**e, "received_at": e["received_at"] + timedelta(seconds=rng.randint(30, 600))}
              for e in events if rng.random() < sc["rate"]]
    return trip, events + copies, pings


TRIP_HANDLERS = {
    "no_driver": no_driver,
    "cancel_trip": cancel_trip,
    "in_progress": in_progress,
    "orphan_event": orphan_event,
    "late_event": late_event,
    "duplicate_events": duplicate_events,
}


def adjust_trip(trip, events, pings, scenarios, key, end):
    """Apply the trip-level scenarios that target this trip. Duplicates are added last."""
    ordered = sorted((sc for sc in scenarios if sc["kind"] in TRIP_HANDLERS),
                     key=lambda sc: sc["kind"] == "duplicate_events")
    for sc in ordered:
        if sc["kind"] != "duplicate_events" and not (
                sc["date"] == trip["service_date"] and sc["route_id"] == trip["route_id"]
                and sc["direction"] == trip["direction"]):
            continue
        trip, events, pings = TRIP_HANDLERS[sc["kind"]](sc, trip, events, pings, key, end)
    return trip, events, pings


def cancelled_legs(scenarios, school_index):
    """Leg numbers (two per school day) of cancelled trips, per route.

    A cancelled trip drives no kilometres, so the bus odometer must not advance for it.
    """
    legs = {}
    for sc in scenarios:
        if sc["kind"] == "cancel_trip":
            if sc["date"] not in school_index:
                raise ValueError(f"{sc['id']}: {sc['date']} is not a school day")
            leg = school_index[sc["date"]] * 2 + (0 if sc["direction"] == "TO_SCHOOL" else 1)
            legs.setdefault(sc["route_id"], []).append(leg)
    return legs