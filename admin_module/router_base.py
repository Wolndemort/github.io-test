from fastapi import APIRouter
from fastapi.templating import Jinja2Templates
from datetime import timedelta

router = APIRouter(tags=["Analytics"])
templates = Jinja2Templates(directory="templates")


def moscow_time(value):
    """Format naive UTC timestamps from the DB in Moscow time (UTC+3)."""
    if value is None:
        return ""
    return (value.replace(tzinfo=None) + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")


templates.env.filters["moscow_time"] = moscow_time
