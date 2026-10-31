from __future__ import annotations

from datetime import date


def age_on(birthday: date | None, on_date: date) -> int | None:
    if not birthday:
        return None
    return on_date.year - birthday.year - ((on_date.month, on_date.day) < (birthday.month, birthday.day))


def age_matches(birthday: date | None, on_date: date, *, enabled=False, minimum=0, maximum=None) -> bool:
    if not enabled:
        return True
    age = age_on(birthday, on_date)
    if age is None:
        return False
    minimum = max(0, int(minimum or 0))
    maximum = None if maximum in (None, "") else max(minimum, int(maximum))
    return age >= minimum and (maximum is None or age <= maximum)


def tariff_age_matches(birthday: date | None, on_date: date, tariff: dict) -> bool:
    minimum = max(0, int(tariff.get("min_age", 0) or 0))
    raw_maximum = tariff.get("max_age")
    maximum = None if raw_maximum in (None, "") else int(raw_maximum)
    return age_matches(birthday, on_date, enabled=minimum > 0 or maximum is not None, minimum=minimum, maximum=maximum)
