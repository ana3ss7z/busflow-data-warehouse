"""Time helpers shared by the simulator and the scenarios."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Casablanca")


def to_utc(day, hhmm):
    """Local Morocco time on a given day -> timezone-aware UTC datetime."""
    t = datetime.strptime(hhmm, "%H:%M").time()
    return datetime.combine(day, t, tzinfo=TZ).astimezone(UTC)