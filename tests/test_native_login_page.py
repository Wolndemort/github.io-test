from pathlib import Path


def test_public_native_login_page_has_request_and_verify_flow():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    start = source.index('@router.get("/native-login"')
    block = source[start:source.index('@router.get("/login")', start)]
    assert "auth/native/request" in block
    assert "auth/native/verify" in block
    assert "one-time-code" in block
    assert "Telegram is not required" in block
