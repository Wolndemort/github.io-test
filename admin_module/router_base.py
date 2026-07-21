from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["Analytics"])
templates = Jinja2Templates(directory="templates")
