"""Day-by-day simulation of trips, stop events and GPS pings (WP2).

The simulation is a pure function of (config, calendar, scenarios, date range):
the baseline reference data is regenerated from the seed, scenarios are applied
on their entry dates, and every school day gets its own random generator seeded
with (seed, date). The database is only the sink.
"""
import argparse
import csv
import random
from collections import defaultdict
from datetime import date, timedelta

import psycopg
import yaml

from generate_reference import (
    ROOT,
    SCHOOL,
    build_reference,
    checksum,
    dsn_from_env,
    haversine_km,
)
from scenarios import apply_day, initial_state, load_scenarios
from timeutil import to_utc

TRIP_COLS = [
    "trip_id", "service_date", "route_id", "bus_id", "driver_id", "direction",
    "scheduled_start_ts", "scheduled_arrival_ts", "actual_start_ts", "first_stop_ts",
    "last_stop_ts", "actual_arrival_ts", "odometer_start_km", "odometer_end_km",
    "status", "cancel_reason", "created_at", "updated_at",
]
EVENT_COLS = [
    "trip_id", "student_id", "stop_id", "event_type",
    "scheduled_ts", "actual_ts", "received_at",
]
PING_COLS = ["trip_id", "ping_ts", "latitude", "longitude", "speed_kmh"]
TABLES = [("trips", TRIP_COLS), ("stop_events", EVENT_COLS), ("gps_pings", PING_COLS)]


# ---------------------------------------------------------------- calendar
def load_calendar(path):
    """Return {date: is_school_day} for every date in the calendar file."""
    cal = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r["date"])
            cal[d] = d.weekday() < 5 and r["is_holiday"] == "0" and r["is_school_break"] == "0"
    return cal


def previous_weekday(d):
    d -= timedelta(days=1)
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------- helpers
def trunc_normal(rng, mean, sd, lo, hi):
    """Normal draw, redrawn until it falls inside [lo, hi]."""
    while True:
        x = rng.gauss(mean, sd)
        if lo <= x <= hi:
            return x


def build_context(ref, cal, seed):
    """Everything that does not change over time."""
    school_days = sorted(d for d, ok in cal.items() if ok)

    # Explicit assignment: the n-th route (by id) gets the n-th bus and the n-th driver
    # (by id). Drivers beyond the number of routes are substitutes, unused for now.
    route_ids = sorted(r["route_id"] for r in ref["routes"])
    bus_ids = sorted(b["bus_id"] for b in ref["buses"])
    driver_ids = sorted(d["driver_id"] for d in ref["drivers"])
    if len(bus_ids) < len(route_ids) or len(driver_ids) < len(route_ids):
        raise ValueError("need at least one bus and one driver per route")
    return {
        "cal": cal,
        "bus_of_route": dict(zip(route_ids, bus_ids, strict=False)),
        "driver_of_route": dict(zip(route_ids, driver_ids, strict=False)),
        "routes": {r["route_id"]: r for r in ref["routes"]},
        "school_index": {d: i for i, d in enumerate(school_days)},
        # starting odometer per route (one bus per route, an agreed project assumption)
        "odo_base": {
            r["route_id"]: random.Random(f"{seed}:odo:{r['route_id']}").randint(20000, 150000)
            for r in ref["routes"]
        },
    }


# ---------------------------------------------------------------- one trip
def simulate_trip(rng, gps_rng, day, ctx, stops, route_id, direction, mean, noshow_p, riders, sim):
    route = ctx["routes"][route_id]
    to_school = direction == "TO_SCHOOL"
    window = sim["morning"] if to_school else sim["afternoon"]
    start = to_utc(day, window["start"])
    arrival = to_utc(day, window["arrival"])
    visit = stops if to_school else stops[::-1]  # morning: far -> near, afternoon: near -> far
    pos = [(float(s["latitude"]), float(s["longitude"])) for s in visit]
    n = len(visit)
    dwell = timedelta(seconds=sim["stop_dwell_s"])
    d = sim["delay"]

    # scheduled stop times are spread evenly between the first and the last stop
    first_off, last_off = (5, 8) if to_school else (8, 5)
    first = start + timedelta(minutes=first_off)
    last = arrival - timedelta(minutes=last_off)
    step = (last - first) / (n - 1)

    sched, actual = [], []
    for k in range(n):
        s_k = first + step * k
        a_k = s_k + timedelta(minutes=trunc_normal(rng, mean, d["sd"], d["min"], d["max"]))
        if actual and a_k < actual[-1] + timedelta(seconds=90):
            a_k = actual[-1] + timedelta(seconds=90)  # a bus cannot reach stop k+1 before stop k
        sched.append(s_k)
        actual.append(a_k)

    start_delay = trunc_normal(rng, mean, d["sd"], d["min"], d["max"])
    actual_start = min(start + timedelta(minutes=start_delay), actual[0] - timedelta(minutes=2))
    actual_arrival = actual[-1] + (arrival - last)

    # odometer: continuous over the whole simulation (2 legs per school day)
    dist = route["planned_distance_km"]
    legs = ctx["school_index"][day] * 2 + (0 if to_school else 1)
    base = ctx["odo_base"][route_id]
    trip_id = int(day.strftime("%Y%m%d")) * 100 + route_id * 10 + (1 if to_school else 2)
    trip = {
        "trip_id": trip_id, "service_date": day, "route_id": route_id,
        "bus_id": ctx["bus_of_route"][route_id],
        "driver_id": ctx["driver_of_route"][route_id], "direction": direction,
        "scheduled_start_ts": start, "scheduled_arrival_ts": arrival,
        "actual_start_ts": actual_start, "first_stop_ts": actual[0],
        "last_stop_ts": actual[-1], "actual_arrival_ts": actual_arrival,
        "odometer_start_km": round(base + legs * dist, 1),
        "odometer_end_km": round(base + (legs + 1) * dist, 1),
        "status": "COMPLETED", "cancel_reason": None,
        "created_at": start - timedelta(hours=2),
        "updated_at": actual_arrival + timedelta(minutes=2),
    }

    # stop events: one per enrolled student, at the student's own stop
    ok_type = "PICKUP" if to_school else "DROPOFF"
    events = []
    for k, stop in enumerate(visit):
        for student_id in riders.get(stop["stop_id"], []):
            if rng.random() < noshow_p:
                kind, actual_ts = "NO_SHOW", None
                received = actual[k] + dwell + timedelta(seconds=rng.randint(1, 20))
            else:
                kind = ok_type
                actual_ts = actual[k] + timedelta(seconds=rng.randint(0, 25))
                received = actual_ts + timedelta(seconds=rng.randint(2, 40))
            events.append({
                "trip_id": trip_id, "student_id": student_id, "stop_id": stop["stop_id"],
                "event_type": kind, "scheduled_ts": sched[k],
                "actual_ts": actual_ts, "received_at": received,
            })

    # GPS: the bus moves in straight lines between stops and waits at each stop
    knots = [(actual_start, pos[0] if to_school else SCHOOL)]
    for k in range(n):
        knots.append((actual[k], pos[k]))
        knots.append((actual[k] + dwell, pos[k]))
    knots.append((actual_arrival, SCHOOL if to_school else pos[-1]))

    pings, i = [], 0
    interval = timedelta(seconds=sim["ping_interval_s"])
    t = actual_start
    while t <= actual_arrival:
        while i < len(knots) - 2 and t > knots[i + 1][0]:
            i += 1
        (t0, p0), (t1, p1) = knots[i], knots[i + 1]
        span = (t1 - t0).total_seconds()
        frac = 0.0 if span <= 0 else min(1.0, max(0.0, (t - t0).total_seconds() / span))
        lat = p0[0] + (p1[0] - p0[0]) * frac + gps_rng.gauss(0, 0.00004)
        lon = p0[1] + (p1[1] - p0[1]) * frac + gps_rng.gauss(0, 0.00004)
        if p0 == p1:
            speed = 0.0
        else:
            speed = min(80.0, haversine_km(p0, p1) / (span / 3600) * gps_rng.uniform(0.85, 1.15))
        pings.append({
            "trip_id": trip_id, "ping_ts": t, "latitude": round(lat, 6),
            "longitude": round(lon, 6), "speed_kmh": round(speed, 1),
        })
        t += interval
    return trip, events, pings


# ---------------------------------------------------------------- one day
def simulate_day(day, ctx, state, cfg):
    sim = cfg["simulation"]
    rng = random.Random(f"{cfg['seed']}:{day.isoformat()}")
    bad_weather = rng.random() < sim["bad_weather_rate"]
    post_holiday = not ctx["cal"].get(previous_weekday(day), False)
    mean = (sim["delay"]["mean"]
            + (sim["bad_weather_extra_mean"] if bad_weather else 0.0)
            + (sim["post_holiday_extra_mean"] if post_holiday else 0.0))
    noshow_p = sim["no_show_rate"] * (sim["friday_no_show_factor"] if day.weekday() == 4 else 1.0)

    # the stops and enrollments are those of the source system on this day
    stops_by_route = defaultdict(list)
    for s in sorted(state["stops"].values(), key=lambda s: (s["route_id"], s["stop_sequence"])):
        stops_by_route[s["route_id"]].append(s)
    riders = defaultdict(list)  # stop_id -> student ids with an open enrollment today
    active = [e for e in state["enrollments"]
              if e["valid_from"] <= day and (e["valid_to"] is None or e["valid_to"] >= day)]
    for e in sorted(active, key=lambda e: e["student_id"]):
        riders[e["pickup_stop_id"]].append(e["student_id"])

    trips, events, pings = [], [], []
    for route_id in sorted(ctx["routes"]):
        for direction in ("TO_SCHOOL", "FROM_SCHOOL"):
            # one independent random stream per trip (and one for its GPS), so a change
            # to one route never shifts the random numbers of another trip
            key = f"{cfg['seed']}:{day.isoformat()}:{route_id}:{direction}"
            t, e, p = simulate_trip(random.Random(key), random.Random(key + ":gps"), day, ctx,
                                    stops_by_route[route_id], route_id, direction, mean,
                                    noshow_p, riders, sim)
            trips.append(t)
            events.extend(e)
            pings.extend(p)
    return trips, events, pings


def run(ref, cal, cfg, start, end, scenarios=None):
    """Simulate [start, end]; return (rows per table, source state at the end of `end`)."""
    scenarios = scenarios or []
    ctx = build_context(ref, cal, cfg["seed"])
    state = initial_state(ref)
    out = {"trips": [], "stop_events": [], "gps_pings": []}
    for day in (start, end):
        if day not in cal:
            raise ValueError(f"{day} is outside data/calendar_ma.csv")
    for day in sorted(cal):  # replay history from the first day so the state is always right
        if day > end:
            break
        apply_day(state, day, scenarios)
        if day >= start and cal[day]:
            trips, events, pings = simulate_day(day, ctx, state, cfg)
            out["trips"].extend(trips)
            out["stop_events"].extend(events)
            out["gps_pings"].extend(pings)
    return out, state


def simulate(ref, cal, cfg, start, end, scenarios=None):
    return run(ref, cal, cfg, start, end, scenarios)[0]


# ---------------------------------------------------------------- database
def check_seeded(conn, ref):
    """The database must hold the baseline reference data (run make seed first)."""
    with conn.cursor() as cur:
        for table in ("routes", "stops", "buses", "drivers", "students"):
            cur.execute(f"SELECT count(*) FROM oltp.{table}")
            if cur.fetchone()[0] != len(ref[table]):
                raise RuntimeError(f"oltp.{table} does not match the seed: run `make seed` first")


def sync_state(conn, state):
    """Bring the mutable source tables to the state at the end of the simulated range."""
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE oltp.students SET grade_level=%s, home_neighborhood=%s, "
            "home_effective_from=%s, updated_at=%s WHERE student_id=%s",
            [(s["grade_level"], s["home_neighborhood"], s["home_effective_from"],
              s["updated_at"], s["student_id"]) for s in state["students"].values()])
        cur.executemany(
            "UPDATE oltp.stops SET stop_name=%s, latitude=%s, longitude=%s, "
            "location_effective_from=%s, updated_at=%s WHERE stop_id=%s",
            [(s["stop_name"], s["latitude"], s["longitude"], s["location_effective_from"],
              s["updated_at"], s["stop_id"]) for s in state["stops"].values()])
        cur.executemany(
            "UPDATE oltp.buses SET seat_capacity=%s, capacity_effective_from=%s, "
            "updated_at=%s WHERE bus_id=%s",
            [(b["seat_capacity"], b["capacity_effective_from"], b["updated_at"], b["bus_id"])
             for b in state["buses"].values()])
        cur.execute("DELETE FROM oltp.student_route_enrollments")
        cols = ["enrollment_id", "student_id", "route_id", "pickup_stop_id", "valid_from", "valid_to"]
        cur.executemany(
            f"INSERT INTO oltp.student_route_enrollments ({', '.join(cols)}) "
            f"VALUES ({', '.join(['%s'] * len(cols))})",
            [tuple(e[c] for c in cols)
             for e in sorted(state["enrollments"], key=lambda e: e["enrollment_id"])])


def write(conn, data, start, end):
    """Replace the oltp rows of the date range (idempotent), then bulk-load with COPY."""
    in_range = "(SELECT trip_id FROM oltp.trips WHERE service_date BETWEEN %s AND %s)"
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM oltp.gps_pings WHERE trip_id IN {in_range}", (start, end))
        cur.execute(f"DELETE FROM oltp.stop_events WHERE trip_id IN {in_range}", (start, end))
        cur.execute("DELETE FROM oltp.trips WHERE service_date BETWEEN %s AND %s", (start, end))
        for table, cols in TABLES:
            with cur.copy(f"COPY oltp.{table} ({', '.join(cols)}) FROM STDIN") as copy:
                for row in data[table]:
                    copy.write_row([row[c] for c in cols])


def main():
    parser = argparse.ArgumentParser(description="Simulate trips, events and GPS pings")
    parser.add_argument("--start", help="first date, YYYY-MM-DD (default: config window start)")
    parser.add_argument("--end", help="last date, YYYY-MM-DD (default: config window end)")
    args = parser.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yml").read_text())
    start = date.fromisoformat(args.start) if args.start else cfg["window"]["start"]
    end = date.fromisoformat(args.end) if args.end else cfg["window"]["end"]
    cal = load_calendar(ROOT / "data" / "calendar_ma.csv")
    scenarios = load_scenarios(ROOT / "scenarios.yml")
    ref = build_reference(cfg)

    data, state = run(ref, cal, cfg, start, end, scenarios)
    with psycopg.connect(dsn_from_env()) as conn:
        check_seeded(conn, ref)
        write(conn, data, start, end)
        sync_state(conn, state)

    print(f"simulated {start} to {end}, checksum {checksum(data)[:12]}")
    for table, _ in TABLES:
        print(f"  {table}: {len(data[table])}")


if __name__ == "__main__":
    main()