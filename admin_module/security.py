import hmac

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from starlette import status

from config import fastapi_key

API_KEY_NAME = "X-API-Key"
API_KEY = fastapi_key
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def get_api_key(header_value: str = Security(api_key_header)):
    if API_KEY and header_value and hmac.compare_digest(header_value, API_KEY):
        return header_value
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Доступ запрещен: неверный API ключ"
    )
