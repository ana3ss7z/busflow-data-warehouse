"""Build data/calendar_ma.csv for the simulation window.

Sources: Ministry of National Education decision on the 2025-2026 school year
(breaks and effective start of classes) and the Moroccan national holiday list.
"""
import csv
from datetime import date, timedelta
from pathlib import Path

START, END = date(2025, 9, 1), date(2026, 1, 31)

# One-day public holidays inside the window
HOLIDAYS = {
    date(2025, 11, 6): "Marche Verte",
    date(2025, 11, 18): "Fete de l'Independance",
    date(2026, 1, 1): "Nouvel An",
    date(2026, 1, 11): "Manifeste de l'Independance",
    date(2026, 1, 14): "Nouvel An amazigh (Yennayer)",
}

# School breaks as (first day, last day, name), both ends included
BREAKS = [
    (date(2025, 9, 1), date(2025, 9, 7), "Pre-rentree (classes start 8 Sept)"),
    (date(2025, 10, 19), date(2025, 10, 26), "Vacances 1 (Toussaint)"),
    (date(2025, 12, 7), date(2025, 12, 14), "Vacances 2 (decembre)"),
    (date(2026, 1, 25), date(2026, 2, 1), "Vacances mi-annee"),
]


def main():
    rows = []
    d = START
    while d <= END:
        brk = next((n for a, b, n in BREAKS if a <= d <= b), "")
        rows.append({
            "date": d.isoformat(),
            "is_holiday": int(d in HOLIDAYS),
            "holiday_name": HOLIDAYS.get(d, ""),
            "is_school_break": int(bool(brk)),
        })
        d += timedelta(days=1)
    out = Path(__file__).resolve().parent.parent / "data" / "calendar_ma.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    school = [r for r in rows
              if date.fromisoformat(r["date"]).weekday() < 5
              and not r["is_holiday"] and not r["is_school_break"]]
    print(f"{len(rows)} days written, {len(school)} school days")


if __name__ == "__main__":
    main()