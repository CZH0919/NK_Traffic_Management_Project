"""New York wall time is source data; UTC is optional when unambiguous."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')

def resolve_wall_time(local_date, local_time):
    if local_date is None or local_time is None:
        return None, 'missing'
    wall = datetime.combine(local_date, local_time).replace(tzinfo=None)
    candidates = set()
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=NY, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(NY).replace(tzinfo=None) == wall:
            candidates.add(candidate)
    if not candidates:
        return None, 'nonexistent'
    if len(candidates) > 1:
        return None, 'ambiguous'
    return candidates.pop(), 'unique'
