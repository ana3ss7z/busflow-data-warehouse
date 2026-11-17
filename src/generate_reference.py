"""Deterministic reference data for the oltp schema (WP1)."""
import hashlib
import json
import math
import os
import random
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import psycopg
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHOOL = (33.8935, -5.5473)  # synthetic school location, Meknès centre
BASE_TS = datetime(2025, 8, 1, 8, 0, tzinfo=UTC)  # fixed: output never depends on the clock

NEIGHBORHOODS = [
    "Hamria", "Marjane", "Wislane", "Sidi Baba", "Toulal", "Zitoune",
    "Bassatine", "Ismailia", "Belle Vue", "Plaisance", "Rouamzine",
    "Sebbata", "Borj Moulay Omar", "Hay Salam", "Al Massira", "Dar Smen",
]
FIRST = [
    "Youssef", "Mohamed", "Amine", "Omar", "Hamza", "Ayoub", "Yassine", "Mehdi",
    "Fatima", "Khadija", "Salma", "Imane", "Aya", "Meryem", "Sara", "Hajar",
    "Zineb", "Nada", "Ilyas", "Anas",
]
LAST = [
    "Alaoui", "Benani", "El Idrissi", "Tazi", "Berrada", "Fassi", "Lahlou",
    "Chraibi", "Bennis", "El Amrani", "Saidi", "Naciri", "Ouazzani", "Zniber",
    "Hajji", "Moussaoui", "Kettani", "Benjelloun", "Rahmouni", "Sebti",
]
LOAD_ORDER = [
    "routes", "stops", "buses", "drivers", "students", "student_route_enrollments",
]


def haversine_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = p2 - p1
    dlmb = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def build_reference(cfg):
    rng = random.Random(cfg["seed"])
    n = cfg["counts"]
    window_start = cfg["window"]["start"]
    hoods = rng.sample(NEIGHBORHOODS, n["routes"])

    routes, stops = [], []
    stop_id = 1
    for r, hood in enumerate(hoods, start=1):
        bearing = 2 * math.pi * (r - 1) / n["routes"] + rng.uniform(-0.2, 0.2)
        far_km = rng.uniform(5, 9)
        pts = []
        for s in range(n["stops_per_route"]):
            d = far_km * (1 - s / n["stops_per_route"])  # sequence 1 is farthest, last is nearest
            lat = SCHOOL[0] + (d / 111.0) * math.cos(bearing) + rng.uniform(-0.0015, 0.0015)
            lon = (SCHOOL[1] + (d / (111.0 * math.cos(math.radians(SCHOOL[0]))))
                   * math.sin(bearing) + rng.uniform(-0.0015, 0.0015))
            pts.append((round(lat, 6), round(lon, 6)))
            stops.append({
                "stop_id": stop_id, "route_id": r,
                "stop_name": f"{hood} - Stop {s + 1}", "stop_sequence": s + 1,
                "latitude": pts[-1][0], "longitude": pts[-1][1],
                "location_effective_from": date(2025, 8, 1),
                "is_active": True, "updated_at": BASE_TS,
            })
            stop_id += 1
        dist = sum(haversine_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        dist += haversine_km(pts[-1], SCHOOL)
        routes.append({
            "route_id": r, "route_name": f"Route {r} - {hood}",
            "planned_distance_km": round(dist, 2), "is_active": True, "updated_at": BASE_TS,
        })

    # Buses: one per route, total capacity must reach total_min
    cap = cfg["bus_capacity"]
    while True:
        caps = [rng.randint(cap["min"], cap["max"]) for _ in range(n["buses"])]
        if sum(caps) >= cap["total_min"]:
            break
    plates, buses = set(), []
    for b, seats in enumerate(caps, start=1):
        while True:
            plate = f"{rng.randint(10000, 99999)}-{rng.choice('ABDEHJ')}-{rng.randint(1, 89)}"
            if plate not in plates:
                plates.add(plate)
                break
        buses.append({
            "bus_id": b, "plate_number": plate, "seat_capacity": seats,
            "capacity_effective_from": date(2025, 8, 1),
            "model_year": rng.randint(2015, 2023), "status": "ACTIVE", "updated_at": BASE_TS,
        })

    drivers = [{
        "driver_id": d, "full_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
        "hire_date": date(2012, 1, 1) + timedelta(days=rng.randint(0, 4400)),
        "status": "ACTIVE", "updated_at": BASE_TS,
    } for d in range(1, n["drivers"] + 1)]

    # Students: spread evenly over routes (about 43 per route, below bus capacity)
    assign = [(i % n["routes"]) + 1 for i in range(n["students"])]
    rng.shuffle(assign)
    students, enrollments = [], []
    for sid, route_id in enumerate(assign, start=1):
        eff = date(2024, 9, 1) + timedelta(days=rng.randint(0, 364))
        ts = datetime.combine(eff, time(8, 0), tzinfo=UTC)
        students.append({
            "student_id": sid, "full_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "grade_level": rng.randint(1, 12), "home_neighborhood": hoods[route_id - 1],
            "home_city": "Meknès", "home_effective_from": eff, "status": "ACTIVE",
            "created_at": ts, "updated_at": ts,
        })
        route_stops = [s["stop_id"] for s in stops if s["route_id"] == route_id]
        enrollments.append({
            "enrollment_id": sid, "student_id": sid, "route_id": route_id,
            "pickup_stop_id": rng.choice(route_stops),
            "valid_from": window_start, "valid_to": None,
        })

    return {
        "routes": routes, "stops": stops, "buses": buses, "drivers": drivers,
        "students": students, "student_route_enrollments": enrollments,
    }


def checksum(data):
    blob = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def dsn_from_env():
    return (f"host=localhost port={os.getenv('POSTGRES_PORT', '5432')} "
            f"dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} "
            f"password={os.environ['POSTGRES_PASSWORD']}")


def load(data, dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE oltp.student_route_enrollments, oltp.students, oltp.stops, "
            "oltp.routes, oltp.buses, oltp.drivers, oltp.trips, oltp.stop_events, "
            "oltp.gps_pings RESTART IDENTITY CASCADE"
        )
        for table in LOAD_ORDER:
            rows = data[table]
            cols = list(rows[0])
            sql = (f"INSERT INTO oltp.{table} ({', '.join(cols)}) "
                   f"VALUES ({', '.join(['%s'] * len(cols))})")
            cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


def main():
    cfg = yaml.safe_load((ROOT / "config.yml").read_text())
    data = build_reference(cfg)
    load(data, dsn_from_env())
    print(f"seeded oltp reference data, checksum {checksum(data)[:12]}")
    for t in LOAD_ORDER:
        print(f"  {t}: {len(data[t])}")


if __name__ == "__main__":
    main()