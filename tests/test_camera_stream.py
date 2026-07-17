from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from admin_module import api


class FakeSession:
    def __init__(self, club_settings):
        self.club = SimpleNamespace(club_settings=club_settings)

    async def execute(self, _query):
        return SimpleNamespace(scalar_one_or_none=lambda: self.club)


class FakeGo2RtcResponse:
    def __init__(self, status_code=200, content_type=None, chunks=None):
        self.status_code = status_code
        self.headers = {}
        if content_type:
            self.headers["content-type"] = content_type
        self.chunks = chunks or []
        self.closed = False

    async def aiter_raw(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class FakeGo2RtcClient:
    def __init__(self, response):
        self.response = response
        self.params = None
        self.closed = False

    def build_request(self, _method, _url, params):
        self.params = params
        return object()

    async def send(self, _request, stream):
        assert stream is True
        return self.response

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_camera_stream_keeps_go2rtc_boundary(monkeypatch):
    upstream = FakeGo2RtcResponse(
        content_type="multipart/x-mixed-replace; boundary=go2rtc-boundary",
        chunks=[b"--go2rtc-boundary\r\n", b"jpeg-frame"]
    )
    client = FakeGo2RtcClient(upstream)
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout: client)

    response = await api.video_stream(
        club_id=7,
        session=FakeSession({"turnstile": {"camera_src": "camera3"}})
    )

    assert response.headers["content-type"] == (
        "multipart/x-mixed-replace; boundary=go2rtc-boundary"
    )
    assert response.headers["x-accel-buffering"] == "no"
    assert client.params == {"src": "camera3"}
    assert b"".join([chunk async for chunk in response.body_iterator]) == (
        b"--go2rtc-boundary\r\njpeg-frame"
    )
    assert upstream.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_camera_stream_reports_go2rtc_error(monkeypatch):
    upstream = FakeGo2RtcResponse(status_code=500)
    client = FakeGo2RtcClient(upstream)
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout: client)

    with pytest.raises(HTTPException) as exc_info:
        await api.video_stream(
            club_id=1,
            session=FakeSession({"turnstile": {}})
        )

    assert exc_info.value.status_code == 502
    assert "go2rtc: 500" in exc_info.value.detail
    assert client.params == {"src": "camera1"}
    assert upstream.closed is True
    assert client.closed is True
