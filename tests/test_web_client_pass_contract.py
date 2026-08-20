from pathlib import Path


def test_client_qr_pass_is_authenticated_scoped_and_hmac_based():
    api = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    ui = Path("static/web/components.js").read_text(encoding="utf-8")
    assert '@client_router.get("/pass/data")' in api
    assert 'Student.club_id == actor.club_id' in api
    assert 'StudentParent.parent_id == actor.user_id' in api
    assert 'hmac.new((secret_key or "").encode()' in api
    assert '@web_router.get("/client/pass"' in api
    assert 'href = "/client/pass"' in ui
