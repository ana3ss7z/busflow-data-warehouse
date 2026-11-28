"""Static dimension loads that need Python: dim_date (with the Moroccan school calendar).

dim_time_of_day, dim_event_type, dim_trip_profile and the special members are plain SQL
(docker/initdb/05_dwh_static_data.sql). The upsert is idempotent: running it again changes
nothing, so the ETL can call it at the start of every run.
"""
import csv
from datetime import date, timedelta

import psycopg

from generate_reference import ROOT, dsn_from_env

FIRST_DATE, LAST_DATE = date(2025, 1, 1), date(2027, 12, 31)
CALENDAR_CSV = ROOT / "data" / "calendar_ma.csv"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

UPSERT = """
INSERT INTO dwh.dim_date (
    date_key, full_date, day_of_week_num, day_name, is_weekend, iso_week, iso_year,
    month_num, month_name, quarter, year, academic_year, is_holiday, holiday_name,
    is_school_break, is_school_day, is_first_school_day_after_break)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (date_key) DO UPDATE SET
    full_date = EXCLUDED.full_date, day_of_week_num = EXCLUDED.day_of_week_num,
    day_name = EXCLUDED.day_name, is_weekend = EXCLUDED.is_weekend,
    iso_week = EXCLUDED.iso_week, iso_year = EXCLUDED.iso_year,
    month_num = EXCLUDED.month_num, month_name = EXCLUDED.month_name,
    quarter = EXCLUDED.quarter, year = EXCLUDED.year,
    academic_year = EXCLUDED.academic_year, is_holiday = EXCLUDED.is_holiday,
    holiday_name = EXCLUDED.holiday_name, is_school_break = EXCLUDED.is_school_break,
    is_school_day = EXCLUDED.is_school_day,
    is_first_school_day_after_break = EXCLUDED.is_first_school_day_after_break
"""


def read_calendar(path=CALENDAR_CSV):
    """{date: (is_holiday, holiday_name, is_school_break)} for the curated window."""
    with path.open(encoding="utf-8") as f:
        return {
            date.fromisoformat(r["date"]):
                (r["is_holiday"] == "1", r["holiday_name"] or None, r["is_school_break"] == "1")
            for r in csv.DictReader(f)
        }


def academic_year(d):
    start = d.year if d.month >= 9 else d.year - 1
    return f"{start}-{start + 1}"


def previous_weekday(d):
    d -= timedelta(days=1)
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return d


def build_dim_date_rows(calendar, first=FIRST_DATE, last=LAST_DATE):
    """One tuple per calendar day, in the column order of UPSERT.

    Dates outside the curated calendar window have no holiday or break information:
    only weekends are non-school days there.
    """
    def info(d):
        return calendar.get(d, (False, None, False))

    def is_school(d):
        holiday, _, brk = info(d)
        return d.weekday() < 5 and not holiday and not brk

    rows, d = [], first
    while d <= last:
        holiday, holiday_name, brk = info(d)
        iso = d.isocalendar()
        school = is_school(d)
        rows.append((
            int(d.strftime("%Y%m%d")), d, d.weekday() + 1, DAY_NAMES[d.weekday()],
            d.weekday() >= 5, iso.week, iso.year, d.month, MONTH_NAMES[d.month - 1],
            (d.month - 1) // 3 + 1, d.year, academic_year(d), holiday, holiday_name,
            brk, school, school and not is_school(previous_weekday(d)),
        ))
        d += timedelta(days=1)
    return rows


def load_dim_date(conn):
    rows = build_dim_date_rows(read_calendar())
    with conn.cursor() as cur:
        cur.executemany(UPSERT, rows)
    return len(rows)


def main():
    with psycopg.connect(dsn_from_env()) as conn:
        n = load_dim_date(conn)
    print(f"dim_date upserted: {n} days ({FIRST_DATE} to {LAST_DATE})")


if __name__ == "__main__":
    main()