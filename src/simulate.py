
"""Day-by-day simulation of trips, stop events and GPS pings (WP2, part 1).

The reference data (routes, stops, buses, drivers, enrollments) is read
from the oltp schema.

Each school day gets its own random generator seeded with (seed, date),
so a given day produces the same rows whatever date range is requested.

Project assumptions:
- One bus and one driver are assigned to each route.
- GPS trajectories are synthetic straight-line interpolations.
"""
import argparse
import csv
import random
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import yaml
from psycopg.rows import dict_row

from generate_reference import ROOT, SCHOOL, checksum, dsn_from_env, haversine_km

TZ = ZoneInfo("Africa/Casablanca")

TRIP_COLS = [
    "trip_id",
    "service_date",
    "route_id",
    "bus_id",
    "driver_id",
    "direction",
    "scheduled_start_ts",
    "scheduled_arrival_ts",
    "actual_start_ts",
    "first_stop_ts",
    "last_stop_ts",
    "actual_arrival_ts",
    "odometer_start_km",
    "odometer_end_km",
    "status",
    "cancel_reason",
    "created_at",
    "updated_at",
]

EVENT_COLS = [
    "trip_id",
    "student_id",
    "stop_id",
    "event_type",
    "scheduled_ts",
    "actual_ts",
    "received_at",
]

PING_COLS = [
    "trip_id",
    "ping_ts",
    "latitude",
    "longitude",
    "speed_kmh",
]

TABLES = [
    ("trips", TRIP_COLS),
    ("stop_events", EVENT_COLS),
    ("gps_pings", PING_COLS),
]


# ---------------------------------------------------------------------
# CALENDAR
# ---------------------------------------------------------------------

def load_calendar(path):
    """Return {date: is_school_day} for every date in the calendar file."""
    cal = {}

    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            day = date.fromisoformat(row["date"])

            cal[day] = (
                day.weekday() < 5
                and row["is_holiday"] == "0"
                and row["is_school_break"] == "0"
            )

    return cal


def previous_weekday(day):
    """Return the previous weekday, skipping Saturday and Sunday."""
    day -= timedelta(days=1)

    while day.weekday() > 4:
        day -= timedelta(days=1)

    return day


# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------

def to_utc(day, hhmm):
    """Convert Morocco local time on a given date into UTC."""
    t = datetime.strptime(hhmm, "%H:%M").time()

    local_datetime = datetime.combine(day, t, tzinfo=TZ)

    return local_datetime.astimezone(UTC)


def trunc_normal(rng, mean, sd, lo, hi):
    """Generate a normal random value constrained to [lo, hi]."""
    while True:
        value = rng.gauss(mean, sd)

        if lo <= value <= hi:
            return value


def build_context(ref, cal, seed):
    """Prepare reference data and mappings used by the simulator."""

    # Group stops by route, keeping their correct sequence.
    stops_by_route = defaultdict(list)

    for stop in sorted(
        ref["stops"],
        key=lambda s: (s["route_id"], s["stop_sequence"]),
    ):
        stops_by_route[stop["route_id"]].append(stop)

    school_days = sorted(
        day for day, is_school_day in cal.items() if is_school_day
    )

    # Explicit assignment:
    # The nth route (by ID) gets the nth bus and driver (by ID).
    # Additional drivers remain available as substitutes.
    route_ids = sorted(r["route_id"] for r in ref["routes"])
    bus_ids = sorted(b["bus_id"] for b in ref["buses"])
    driver_ids = sorted(d["driver_id"] for d in ref["drivers"])

    if len(bus_ids) < len(route_ids) or len(driver_ids) < len(route_ids):
        raise ValueError(
            "need at least one bus and one driver per route"
        )

    return {
        "cal": cal,

        "bus_of_route": dict(
            zip(route_ids, bus_ids, strict=False)
        ),

        "driver_of_route": dict(
            zip(route_ids, driver_ids, strict=False)
        ),

        "routes": {
            r["route_id"]: r for r in ref["routes"]
        },

        "stops_by_route": stops_by_route,

        "enrollments": ref["student_route_enrollments"],

        "school_index": {
            day: i for i, day in enumerate(school_days)
        },

        # Starting odometer per route:
        # one bus per route is an agreed project assumption.
        "odo_base": {
            route_id: random.Random(
                f"{seed}:odo:{route_id}"
            ).randint(20000, 150000)
            for route_id in route_ids
        },
    }


# ---------------------------------------------------------------------
# ONE TRIP
# ---------------------------------------------------------------------

def simulate_trip(
    rng,
    day,
    ctx,
    route_id,
    direction,
    mean,
    noshow_p,
    riders,
    sim,
):
    """Generate one trip, its student events and its GPS pings."""

    route = ctx["routes"][route_id]

    to_school = direction == "TO_SCHOOL"

    window = (
        sim["morning"]
        if to_school
        else sim["afternoon"]
    )

    start = to_utc(day, window["start"])
    arrival = to_utc(day, window["arrival"])

    stops = ctx["stops_by_route"][route_id]

    # Morning: farthest stop -> nearest stop.
    # Afternoon: nearest stop -> farthest stop.
    visit = stops if to_school else stops[::-1]

    positions = [
        (float(s["latitude"]), float(s["longitude"]))
        for s in visit
    ]

    n = len(visit)

    dwell = timedelta(seconds=sim["stop_dwell_s"])

    delay_cfg = sim["delay"]

    # Distribute scheduled stop times between the first and last stops.
    first_offset, last_offset = (
        (5, 8) if to_school else (8, 5)
    )

    first = start + timedelta(minutes=first_offset)
    last = arrival - timedelta(minutes=last_offset)

    step = (last - first) / (n - 1)

    scheduled_times = []
    actual_times = []

    for k in range(n):
        scheduled = first + step * k

        actual = scheduled + timedelta(
            minutes=trunc_normal(
                rng,
                mean,
                delay_cfg["sd"],
                delay_cfg["min"],
                delay_cfg["max"],
            )
        )

        # The bus must reach the next stop at least 90 seconds later.
        if actual_times and actual < (
            actual_times[-1] + timedelta(seconds=90)
        ):
            actual = actual_times[-1] + timedelta(seconds=90)

        scheduled_times.append(scheduled)
        actual_times.append(actual)

    # Actual trip departure.
    start_delay = trunc_normal(
        rng,
        mean,
        delay_cfg["sd"],
        delay_cfg["min"],
        delay_cfg["max"],
    )

    actual_start = min(
        start + timedelta(minutes=start_delay),
        actual_times[0] - timedelta(minutes=2),
    )

    # Arrival is calculated from the last stop's actual time.
    actual_arrival = actual_times[-1] + (arrival - last)

    # Odometer: continuous across both daily trips.
    distance = route["planned_distance_km"]

    legs = (
        ctx["school_index"][day] * 2
        + (0 if to_school else 1)
    )

    base = ctx["odo_base"][route_id]

    # Readable ID:
    # YYYYMMDD + route ID + direction code.
    trip_id = (
        int(day.strftime("%Y%m%d")) * 100
        + route_id * 10
        + (1 if to_school else 2)
    )

    trip = {
        "trip_id": trip_id,
        "service_date": day,
        "route_id": route_id,

        "bus_id": ctx["bus_of_route"][route_id],
        "driver_id": ctx["driver_of_route"][route_id],

        "direction": direction,

        "scheduled_start_ts": start,
        "scheduled_arrival_ts": arrival,

        "actual_start_ts": actual_start,
        "first_stop_ts": actual_times[0],
        "last_stop_ts": actual_times[-1],
        "actual_arrival_ts": actual_arrival,

        "odometer_start_km": round(
            base + legs * distance, 1
        ),

        "odometer_end_km": round(
            base + (legs + 1) * distance, 1
        ),

        "status": "COMPLETED",
        "cancel_reason": None,

        "created_at": start - timedelta(hours=2),
        "updated_at": actual_arrival + timedelta(minutes=2),
    }

    # -------------------------------------------------------------
    # STOP EVENTS
    # -------------------------------------------------------------

    event_type = "PICKUP" if to_school else "DROPOFF"

    events = []

    for k, stop in enumerate(visit):

        # Get students assigned to this stop.
        for student_id in riders.get(stop["stop_id"], []):

            if rng.random() < noshow_p:

                kind = "NO_SHOW"
                actual_ts = None

                received = (
                    actual_times[k]
                    + dwell
                    + timedelta(seconds=rng.randint(1, 20))
                )

            else:

                kind = event_type

                actual_ts = (
                    actual_times[k]
                    + timedelta(seconds=rng.randint(0, 25))
                )

                received = (
                    actual_ts
                    + timedelta(seconds=rng.randint(2, 40))
                )

            events.append({
                "trip_id": trip_id,
                "student_id": student_id,
                "stop_id": stop["stop_id"],
                "event_type": kind,
                "scheduled_ts": scheduled_times[k],
                "actual_ts": actual_ts,
                "received_at": received,
            })

    # -------------------------------------------------------------
    # GPS PINGS
    # -------------------------------------------------------------

    # A knot represents a known position at a known time.
    knots = [
        (
            actual_start,
            positions[0] if to_school else SCHOOL,
        )
    ]

    for k in range(n):

        # Bus arrives at the stop.
        knots.append((
            actual_times[k],
            positions[k],
        ))

        # Bus waits at the stop.
        knots.append((
            actual_times[k] + dwell,
            positions[k],
        ))

    # Final destination.
    knots.append((
        actual_arrival,
        SCHOOL if to_school else positions[-1],
    ))

    pings = []

    i = 0

    interval = timedelta(
        seconds=sim["ping_interval_s"]
    )

    t = actual_start

    while t <= actual_arrival:

        # Find the segment containing the current timestamp.
        while (
            i < len(knots) - 2
            and t > knots[i + 1][0]
        ):
            i += 1

        (t0, p0), (t1, p1) = knots[i], knots[i + 1]

        span = (t1 - t0).total_seconds()

        if span <= 0:
            fraction = 0.0
        else:
            fraction = min(
                1.0,
                max(
                    0.0,
                    (t - t0).total_seconds() / span,
                ),
            )

        # Linear interpolation between two GPS positions.
        latitude = (
            p0[0]
            + (p1[0] - p0[0]) * fraction
            + rng.gauss(0, 0.00004)
        )

        longitude = (
            p0[1]
            + (p1[1] - p0[1]) * fraction
            + rng.gauss(0, 0.00004)
        )

        # Speed is zero when the bus is stationary.
        if p0 == p1:
            speed = 0.0
        else:
            speed = min(
                80.0,
                haversine_km(p0, p1)
                / (span / 3600)
                * rng.uniform(0.85, 1.15),
            )

        pings.append({
            "trip_id": trip_id,
            "ping_ts": t,
            "latitude": round(latitude, 6),
            "longitude": round(longitude, 6),
            "speed_kmh": round(speed, 1),
        })

        t += interval

    return trip, events, pings


# ---------------------------------------------------------------------
# ONE DAY
# ---------------------------------------------------------------------

def simulate_day(day, ctx, cfg):
    """Simulate all routes and both directions for one school day."""

    sim = cfg["simulation"]

    # One deterministic random generator per date.
    rng = random.Random(
        f"{cfg['seed']}:{day.isoformat()}"
    )

    # Decide the day's characteristics.
    bad_weather = (
        rng.random() < sim["bad_weather_rate"]
    )

    post_holiday = not ctx["cal"].get(
        previous_weekday(day),
        False,
    )

    mean = (
        sim["delay"]["mean"]
        + (
            sim["bad_weather_extra_mean"]
            if bad_weather else 0.0
        )
        + (
            sim["post_holiday_extra_mean"]
            if post_holiday else 0.0
        )
    )

    # Fridays have a higher no-show probability.
    noshow_p = (
        sim["no_show_rate"]
        * (
            sim["friday_no_show_factor"]
            if day.weekday() == 4
            else 1.0
        )
    )

    # Group active students by their pickup stop.
    riders = defaultdict(list)

    active = [
        enrollment
        for enrollment in ctx["enrollments"]
        if (
            enrollment["valid_from"] <= day
            and (
                enrollment["valid_to"] is None
                or enrollment["valid_to"] >= day
            )
        )
    ]

    for enrollment in sorted(
        active,
        key=lambda e: e["student_id"],
    ):
        riders[enrollment["pickup_stop_id"]].append(
            enrollment["student_id"]
        )

    trips = []
    events = []
    pings = []

    # Two trips per route: morning and afternoon.
    for route_id in sorted(ctx["routes"]):

        for direction in (
            "TO_SCHOOL",
            "FROM_SCHOOL",
        ):

            trip, stop_events, gps_pings = simulate_trip(
                rng=rng,
                day=day,
                ctx=ctx,
                route_id=route_id,
                direction=direction,
                mean=mean,
                noshow_p=noshow_p,
                riders=riders,
                sim=sim,
            )

            trips.append(trip)
            events.extend(stop_events)
            pings.extend(gps_pings)

    return trips, events, pings


# ---------------------------------------------------------------------
# FULL SIMULATION
# ---------------------------------------------------------------------

def simulate(ref, cal, cfg, start, end):
    """Simulate every school day between start and end."""

    ctx = build_context(
        ref,
        cal,
        cfg["seed"],
    )

    out = {
        "trips": [],
        "stop_events": [],
        "gps_pings": [],
    }

    day = start

    while day <= end:

        if day not in cal:
            raise ValueError(
                f"{day} is outside data/calendar_ma.csv"
            )

        if cal[day]:

            trips, events, pings = simulate_day(
                day,
                ctx,
                cfg,
            )

            out["trips"].extend(trips)
            out["stop_events"].extend(events)
            out["gps_pings"].extend(pings)

        day += timedelta(days=1)

    return out


# ---------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------

def load_reference(conn):
    """Load existing reference data from the OLTP schema."""

    queries = {

        "routes": """
            SELECT route_id, planned_distance_km
            FROM oltp.routes
            ORDER BY route_id
        """,

        "stops": """
            SELECT
                stop_id,
                route_id,
                stop_sequence,
                latitude,
                longitude
            FROM oltp.stops
            ORDER BY stop_id
        """,

        "buses": """
            SELECT bus_id
            FROM oltp.buses
            ORDER BY bus_id
        """,

        "drivers": """
            SELECT driver_id
            FROM oltp.drivers
            ORDER BY driver_id
        """,

        "student_route_enrollments": """
            SELECT
                student_id,
                pickup_stop_id,
                valid_from,
                valid_to
            FROM oltp.student_route_enrollments
            ORDER BY student_id
        """,
    }

    ref = {}

    with conn.cursor(row_factory=dict_row) as cur:

        for name, sql in queries.items():

            cur.execute(sql)

            ref[name] = cur.fetchall()

    # Convert PostgreSQL numeric values into Python floats.
    for route in ref["routes"]:
        route["planned_distance_km"] = float(
            route["planned_distance_km"]
        )

    for stop in ref["stops"]:
        stop["latitude"] = float(stop["latitude"])
        stop["longitude"] = float(stop["longitude"])

    return ref


def write(conn, data, start, end):
    """Replace a date range and bulk-load its generated records."""

    in_range = """
        (SELECT trip_id
         FROM oltp.trips
         WHERE service_date BETWEEN %s AND %s)
    """

    with conn.cursor() as cur:

        # Delete dependent records first.
        cur.execute(
            f"""
            DELETE FROM oltp.gps_pings
            WHERE trip_id IN {in_range}
            """,
            (start, end),
        )

        cur.execute(
            f"""
            DELETE FROM oltp.stop_events
            WHERE trip_id IN {in_range}
            """,
            (start, end),
        )

        # Delete the parent trip records.
        cur.execute(
            """
            DELETE FROM oltp.trips
            WHERE service_date BETWEEN %s AND %s
            """,
            (start, end),
        )

        # Bulk-load all three tables using PostgreSQL COPY.
        for table, columns in TABLES:

            column_list = ", ".join(columns)

            with cur.copy(
                f"COPY oltp.{table} ({column_list}) FROM STDIN"
            ) as copy:

                for row in data[table]:

                    copy.write_row([
                        row[column]
                        for column in columns
                    ])


# ---------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="Simulate trips, events and GPS pings"
    )

    parser.add_argument(
        "--start",
        help="first date, YYYY-MM-DD (default: config window start)",
    )

    parser.add_argument(
        "--end",
        help="last date, YYYY-MM-DD (default: config window end)",
    )

    args = parser.parse_args()

    cfg = yaml.safe_load(
        (ROOT / "config.yml").read_text()
    )

    start = (
        date.fromisoformat(args.start)
        if args.start
        else cfg["window"]["start"]
    )

    end = (
        date.fromisoformat(args.end)
        if args.end
        else cfg["window"]["end"]
    )

    cal = load_calendar(
        ROOT / "data" / "calendar_ma.csv"
    )

    with psycopg.connect(dsn_from_env()) as conn:

        ref = load_reference(conn)

        data = simulate(
            ref,
            cal,
            cfg,
            start,
            end,
        )

        write(
            conn,
            data,
            start,
            end,
        )

    print(
        f"simulated {start} to {end}, "
        f"checksum {checksum(data)[:12]}"
    )

    for table, _ in TABLES:
        print(
            f"  {table}: {len(data[table])}"
        )


if __name__ == "__main__":
    main()
