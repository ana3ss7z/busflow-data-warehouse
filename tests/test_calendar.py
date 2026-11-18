import csv
from datetime import date

from generate_reference import ROOT

ROWS = list(csv.DictReader((ROOT / "data" / "calendar_ma.csv").open(encoding="utf-8")))


def school_days():
    return [
        r["date"] for r in ROWS
        if date.fromisoformat(r["date"]).weekday() < 5
        and r["is_holiday"] == "0" and r["is_school_break"] == "0"
    ]


def test_covers_the_full_window():
    assert ROWS[0]["date"] == "2025-09-01" and ROWS[-1]["date"] == "2026-01-31"
    assert len(ROWS) == 153


def test_school_day_count_in_spec_range():
    assert 85 <= len(school_days()) <= 100


def test_scenario_dates_are_school_days():
    days = set(school_days())
    for d in ["2025-10-14", "2025-10-28", "2025-11-03", "2025-11-05",
              "2025-11-12", "2025-11-17", "2025-12-02", "2025-12-16",
              "2025-12-17", "2025-12-18"]:
        assert d in days, d