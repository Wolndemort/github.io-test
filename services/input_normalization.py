from datetime import date, datetime
import re


def normalize_ru_phone(value: str | None) -> str | None:
    """Return a Russian phone as 7XXXXXXXXXX."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10:
        return "7" + digits
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        return "7" + digits[1:]
    return None


def parse_user_date(value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw or raw == "0":
        return None
    for pattern in ("%d.%m.%Y", "%d%m%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, pattern).date()
            if parsed > date.today() or parsed.year < 1900:
                raise ValueError
            return parsed
        except ValueError:
            continue
    raise ValueError("Некорректная дата")
