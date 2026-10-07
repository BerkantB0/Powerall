"""UTC storage and UK civil-time boundaries, including short and long days."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
UK = ZoneInfo('Europe/London')
HALF_HOUR = 1800

def stamp(value):
    return int(value.timestamp())

def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat().replace('+00:00', 'Z')

def local_date(value):
    return datetime.fromtimestamp(value, UK).date().isoformat()

def date_bounds(day):
    day = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(day, time.min, UK)
    end = datetime.combine(day + timedelta(days=1), time.min, UK)
    return stamp(start), stamp(end)

def parse_instant(value):
    """Provider dates are UTC unless an explicit offset is supplied."""
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return stamp(parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed)

def local_midnight(value):
    return date_bounds(date.fromisoformat(value))[0]

def period_bounds(view, anchor, until=None):
    day = date.fromisoformat(anchor)
    if view == 'day':
        first, last = day, day + timedelta(days=1)
        previous = day - timedelta(days=1)
    elif view == 'week':
        first = day - timedelta(days=day.weekday())
        last = first + timedelta(days=7)
        previous = first - timedelta(days=7)
    elif view == 'month':
        first = day.replace(day=1)
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        previous = (first - timedelta(days=1)).replace(day=1)
    elif view == 'custom':
        if not until:
            raise ValueError('Choose a start and end date for the custom period.')
        end = date.fromisoformat(until)
        if not 2000 <= day.year <= 2099 or not 2000 <= end.year <= 2099:
            raise ValueError('Choose dates between 2000 and 2099.')
        length = (end - day).days + 1
        if not 1 <= length <= 366:
            raise ValueError('Choose a custom period between 1 and 366 days, with the end on or after the start.')
        first, last, previous = day, end + timedelta(days=1), day - timedelta(days=length)
    else:
        raise ValueError('Choose day, week, month or custom.')
    return first, last, previous
