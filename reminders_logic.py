

from datetime import datetime
from typing import List


def parse_times(times_str: str) -> List[str]:
    """Turns '08:00,14:00,20:00' into ['08:00', '14:00', '20:00']."""
    return [t.strip() for t in times_str.split(",") if t.strip()]


def is_due_now(scheduled_time: str, window_minutes: int = 15) -> bool:
    """Checks if a scheduled HH:MM time is within `window_minutes` of right now."""
    now = datetime.now()
    try:
        hour, minute = map(int, scheduled_time.split(":"))
    except ValueError:
        return False

    scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    diff_minutes = abs((now - scheduled_today).total_seconds()) / 60
    return diff_minutes <= window_minutes
