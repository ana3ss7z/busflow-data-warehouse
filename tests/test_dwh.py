"""Tests of the warehouse DDL, static dimensions and constraints (WP3).

They need the database (`make up`). Every constraint test runs inside a transaction that
is rolled back, so nothing is left behind. Without a database the tests are skipped.
"""
import psycopg
import pytest
from psycopg import errors

from generate_reference import ROOT, dsn_from_env
from simulate import load_calendar, previous_weekday
from static_dims import load_dim_date

SCD2 = {  # dimension -> (natural key column, extra columns and values to insert)
    "dim_student": ("student_id", "student_name, student_status, home_neighborhood, home_city",
                    "'T', 'ACTIVE', 'A', 'Meknes'"),
    "dim_stop": ("stop_id", "stop_name", "'T'"),
    "dim_bus": ("bus_id", "plate_number, bus_status", "'T', 'ACTIVE'"),
}


@pytest.fixture(scope="module")
def conn():
    try:
        c = psycopg.connect(dsn_from_env())
    except (KeyError, psycopg.OperationalError):
        pytest.skip("PostgreSQL is not available (start it with `make up`)")
    with c:
        if c.execute("SELECT to_regclass('dwh.dim_date')").fetchone()[0] is None:
            pytest.skip("warehouse schema missing: run `docker compose down -v` then `make up`")
        load_dim_date(c)
        c.commit()
        yield c


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def add_version(conn, table, natural_id, effective, expiration, current):
    key, cols, values = SCD2[table]
    conn.execute(
        f"INSERT INTO dwh.{table} ({key}, {cols}, effective_date, expiration_date, is_current, "
        f"scd2_hash, created_load_id, updated_load_id) "
        f"VALUES (%s, {values}, %s, %s, %s, 'x', 0, 0)",
        (natural_id, effective, expiration, current))


def add_event(conn, trip_id=1, student_key=-1, ridership=1, no_show=0, delay=2.0, late=0):
    conn.execute(
        "INSERT INTO dwh.fact_stop_event (trip_id, service_date_key, scheduled_time_key, "
        "actual_time_key, student_key, stop_key, route_key, bus_key, driver_key, "
        "event_type_key, delay_minutes, ridership_count, no_show_count, late_count, load_id) "
        "VALUES (%s, 20251014, 715, 717, %s, -1, -1, -1, -1, 1, %s, %s, %s, %s, 0)",
        (trip_id, student_key, delay, ridership, no_show, late))


def add_trip(conn, trip_id=1):
    conn.execute(
        "INSERT INTO dwh.fact_trip_lifecycle (trip_id, service_date_key, route_key, bus_key, "
        "driver_key, trip_profile_key, is_complete, load_id) "
        "VALUES (%s, 20251014, -1, -1, -1, 1, TRUE, 0)", (trip_id,))


# ---------------------------------------------------------------- structure
def test_schemas_and_tables_exist(conn):
    def tables(schema):
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (schema,)).fetchall()
        return {r[0] for r in rows}

    assert tables("ctl") == {"etl_run", "etl_step_log", "etl_watermark"}
    assert tables("staging") == {
        "students", "routes", "stops", "buses", "drivers",
        "student_route_enrollments", "trips", "stop_events"}
    assert tables("dwh") == {
        "dim_date", "dim_time_of_day", "dim_event_type", "dim_trip_profile",
        "dim_route", "dim_driver", "dim_student", "dim_stop", "dim_bus",
        "fact_stop_event", "fact_trip_lifecycle", "fact_bus_daily",
        "reject_record", "dq_issue"}


def test_bootstrap_run_owns_the_special_members(conn):
    assert scalar(conn, "SELECT mode FROM ctl.etl_run WHERE run_id = 0") == "BOOTSTRAP"


# ---------------------------------------------------------------- special members
@pytest.mark.parametrize("table, key", [
    ("dim_route", "route_key"), ("dim_driver", "driver_key"), ("dim_student", "student_key"),
    ("dim_stop", "stop_key"), ("dim_bus", "bus_key"), ("dim_event_type", "event_type_key"),
    ("dim_trip_profile", "trip_profile_key"), ("dim_date", "date_key"),
    ("dim_time_of_day", "time_key")])
def test_every_dimension_has_the_unknown_member(conn, table, key):
    assert scalar(conn, f"SELECT count(*) FROM dwh.{table} WHERE {key} = -1") == 1


@pytest.mark.parametrize("table, key", [("dim_date", "date_key"), ("dim_time_of_day", "time_key")])
def test_date_and_time_have_the_not_applicable_member(conn, table, key):
    assert scalar(conn, f"SELECT count(*) FROM dwh.{table} WHERE {key} = -2") == 1


@pytest.mark.parametrize("table", ["dim_student", "dim_stop", "dim_bus"])
def test_scd2_unknown_member_is_open_since_1900(conn, table):
    row = conn.execute(
        f"SELECT effective_date, expiration_date, is_current FROM dwh.{table} "
        f"WHERE {SCD2[table][0]} = -1").fetchone()
    assert str(row[0]) == "1900-01-01" and str(row[1]) == "9999-12-31" and row[2] is True


# ---------------------------------------------------------------- static dimensions
def test_time_of_day_has_one_row_per_minute(conn):
    assert scalar(conn, "SELECT count(*) FROM dwh.dim_time_of_day WHERE time_key >= 0") == 1440
    assert scalar(conn, "SELECT count(*) FROM dwh.dim_time_of_day WHERE minute > 59") == 0
    parts = dict(conn.execute(
        "SELECT time_key, day_part FROM dwh.dim_time_of_day "
        "WHERE time_key IN (459, 500, 759, 800, 1159, 1200, 1359, 1400, 1759, 1800)").fetchall())
    assert parts == {459: "NIGHT", 500: "EARLY_MORNING", 759: "EARLY_MORNING", 800: "MORNING",
                     1159: "MORNING", 1200: "MIDDAY", 1359: "MIDDAY", 1400: "AFTERNOON",
                     1759: "AFTERNOON", 1800: "EVENING"}
    assert scalar(conn, "SELECT time_label FROM dwh.dim_time_of_day WHERE time_key = 705") == "07:05"


def test_event_types_match_the_spec(conn):
    rows = conn.execute(
        "SELECT event_type_key, event_type_code, direction FROM dwh.dim_event_type "
        "WHERE event_type_key > 0 ORDER BY 1").fetchall()
    assert rows == [(1, "PICKUP", "TO_SCHOOL"), (2, "DROPOFF", "FROM_SCHOOL"),
                    (3, "NO_SHOW", "TO_SCHOOL"), (4, "NO_SHOW", "FROM_SCHOOL")]


def test_trip_profiles_are_14_plus_unknown(conn):
    assert scalar(conn, "SELECT count(*) FROM dwh.dim_trip_profile WHERE trip_profile_key > 0") == 14
    for direction in ("TO_SCHOOL", "FROM_SCHOOL"):
        reasons = {r[0] for r in conn.execute(
            "SELECT cancel_reason FROM dwh.dim_trip_profile "
            "WHERE direction = %s AND trip_status = 'CANCELLED'", (direction,)).fetchall()}
        assert reasons == {"BREAKDOWN", "DRIVER_ABSENT", "WEATHER", "OTHER"}
        statuses = {r[0] for r in conn.execute(
            "SELECT trip_status FROM dwh.dim_trip_profile WHERE direction = %s", (direction,)
        ).fetchall()}
        assert statuses == {"COMPLETED", "IN_PROGRESS", "SCHEDULED", "CANCELLED"}


def test_dim_date_covers_2025_to_2027(conn):
    assert scalar(conn, "SELECT count(*) FROM dwh.dim_date WHERE date_key > 0") == 1095
    assert scalar(conn, "SELECT min(full_date)::text FROM dwh.dim_date") == "2025-01-01"
    assert scalar(conn, "SELECT max(full_date)::text FROM dwh.dim_date") == "2027-12-31"


def test_dim_date_attributes(conn):
    def row(key):
        return conn.execute(
            "SELECT day_name, day_of_week_num, is_weekend, academic_year, holiday_name "
            "FROM dwh.dim_date WHERE date_key = %s", (key,)).fetchone()

    assert row(20251109) == ("Sunday", 7, True, "2025-2026", None)
    assert row(20251106)[0] == "Thursday" and row(20251106)[4] == "Marche Verte"
    assert row(20250901)[3] == "2025-2026" and row(20260131)[3] == "2025-2026"
    assert row(20250831)[3] == "2024-2025" and row(20260901)[3] == "2026-2027"


def test_dim_date_agrees_with_the_simulator_calendar(conn):
    cal = load_calendar(ROOT / "data" / "calendar_ma.csv")
    rows = conn.execute(
        "SELECT full_date, is_school_day, is_first_school_day_after_break FROM dwh.dim_date "
        "WHERE date_key > 0 AND full_date BETWEEN %s AND %s", (min(cal), max(cal))).fetchall()
    assert len(rows) == len(cal)
    assert sum(r[1] for r in rows) == 86
    for d, school, first in rows:
        assert school == cal[d], d
        assert first == (cal[d] and not cal.get(previous_weekday(d), False)), d


def test_dim_date_load_is_idempotent(conn):
    before = conn.execute("SELECT md5(string_agg(t::text, '|' ORDER BY date_key)) "
                          "FROM dwh.dim_date t").fetchone()[0]
    load_dim_date(conn)
    after = conn.execute("SELECT md5(string_agg(t::text, '|' ORDER BY date_key)) "
                         "FROM dwh.dim_date t").fetchone()[0]
    assert before == after


# ---------------------------------------------------------------- SCD2 constraints
@pytest.mark.parametrize("table", list(SCD2))
def test_overlapping_versions_are_rejected(conn, table):
    with conn.transaction(force_rollback=True):
        add_version(conn, table, 9001, "1900-01-01", "2025-10-14", False)
        with pytest.raises(errors.ExclusionViolation), conn.transaction():
            add_version(conn, table, 9001, "2025-10-01", "9999-12-31", True)


@pytest.mark.parametrize("table", list(SCD2))
def test_touching_versions_are_allowed(conn, table):
    with conn.transaction(force_rollback=True):  # half-open: [1900, Oct 14) then [Oct 14, ...)
        add_version(conn, table, 9001, "1900-01-01", "2025-10-14", False)
        add_version(conn, table, 9001, "2025-10-14", "9999-12-31", True)
        assert scalar(conn, f"SELECT count(*) FROM dwh.{table} "
                            f"WHERE {SCD2[table][0]} = 9001") == 2


@pytest.mark.parametrize("table", list(SCD2))
def test_two_current_versions_are_rejected(conn, table):
    with conn.transaction(force_rollback=True):
        add_version(conn, table, 9001, "1900-01-01", "9999-12-31", True)
        with pytest.raises((errors.UniqueViolation, errors.ExclusionViolation)), \
                conn.transaction():
            add_version(conn, table, 9001, "2025-10-14", "9999-12-31", True)


@pytest.mark.parametrize("table", list(SCD2))
def test_is_current_must_match_the_open_end_date(conn, table):
    with conn.transaction(force_rollback=True):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            add_version(conn, table, 9001, "1900-01-01", "2030-01-01", True)
        with pytest.raises(errors.CheckViolation), conn.transaction():
            add_version(conn, table, 9001, "1900-01-01", "9999-12-31", False)


@pytest.mark.parametrize("table", list(SCD2))
def test_a_version_must_end_after_it_starts(conn, table):
    with conn.transaction(force_rollback=True), pytest.raises(errors.CheckViolation):
        add_version(conn, table, 9001, "2025-10-14", "2025-10-14", False)


# ---------------------------------------------------------------- fact constraints
def test_fact_stop_event_grain_is_one_row_per_student_and_trip(conn):
    with conn.transaction(force_rollback=True):
        add_event(conn, trip_id=1)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            add_event(conn, trip_id=1)
        add_event(conn, trip_id=2)  # another trip is fine


def test_fact_stop_event_foreign_keys_are_enforced(conn):
    with conn.transaction(force_rollback=True), pytest.raises(errors.ForeignKeyViolation):
        add_event(conn, student_key=123456)


def test_fact_stop_event_measures_are_consistent(conn):
    with conn.transaction(force_rollback=True):
        for kwargs in (
                {"ridership": 1, "no_show": 1},              # both outcomes
                {"ridership": 0, "no_show": 0},              # no outcome
                {"ridership": 0, "no_show": 1, "delay": 2},  # a no-show has no delay
                {"ridership": 1, "no_show": 0, "delay": None, "late": 1}):  # late needs a delay
            with pytest.raises(errors.CheckViolation), conn.transaction():
                add_event(conn, **kwargs)
        add_event(conn, ridership=0, no_show=1, delay=None)  # a valid no-show


def test_fact_trip_lifecycle_has_one_row_per_trip(conn):
    with conn.transaction(force_rollback=True):
        add_trip(conn, trip_id=7)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            add_trip(conn, trip_id=7)
        milestones = conn.execute(
            "SELECT actual_start_time_key, actual_arrival_time_key FROM dwh.fact_trip_lifecycle "
            "WHERE trip_id = 7").fetchone()
        assert milestones == (-2, -2)  # milestones default to "not yet occurred"


def test_fact_bus_daily_has_one_row_per_bus_and_day(conn):
    insert = ("INSERT INTO dwh.fact_bus_daily (service_date_key, bus_key, trips_scheduled, "
              "trips_completed, trips_cancelled, seat_capacity, seat_trips_offered, load_id) "
              "VALUES (20251014, -1, 2, 2, 0, 45, 90, 0)")
    with conn.transaction(force_rollback=True):
        conn.execute(insert)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            conn.execute(insert)


def test_dim_date_rejects_an_inconsistent_school_day_flag(conn):
    with conn.transaction(force_rollback=True), pytest.raises(errors.CheckViolation):
        conn.execute(
            "UPDATE dwh.dim_date SET is_school_day = NOT is_school_day WHERE date_key = 20251014")


def test_reject_and_dq_tables_accept_valid_rows(conn):
    with conn.transaction(force_rollback=True):
        conn.execute(
            "INSERT INTO dwh.reject_record (run_id, target_table, source_key, reason_code, payload) "
            "VALUES (0, 'fact_stop_event', '1', 'UNKNOWN_STUDENT', '{\"student_id\": 99999}')")
        conn.execute(
            "INSERT INTO dwh.dq_issue (run_id, rule_id, severity, details) "
            "VALUES (0, 'DQ-E01', 'ERROR', '{}')")
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute("INSERT INTO dwh.dq_issue (run_id, rule_id, severity, details) "
                         "VALUES (0, 'X', 'FATAL', '{}')")