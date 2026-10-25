from pathlib import Path


def test_money_and_identity_mutations_have_idempotency_and_audit():
    source = Path('auth/forecast_routes.py').read_text(encoding='utf-8')
    for marker, end in (
        ('@client_router.post("/payments/{order_id}/intent")', '@client_router.post("/products/order")'),
        ('@client_router.post("/bind-phone")', '@router.get("/access")'),
    ):
        start = source.index(marker)
        block = source[start:source.index(end, start)]
        for required in ('require_csrf', 'idempotency', 'actor.club_id', 'audit_event'):
            assert required in block


def test_native_auth_has_rate_limit_and_audit_events_without_logging_codes():
    source = Path('auth/routes.py').read_text(encoding='utf-8')
    request = source[source.index('@router.post("/native/request")'):source.index('@router.post("/native/verify")')]
    verify = source[source.index('@router.post("/native/verify")'):source.index('@router.get("/web-entry", response_class=HTMLResponse)')]
    assert 'allow_otp_request' in request and 'web_native_otp_rate_limited' in request
    assert 'web_native_login_failed' in verify and 'web_native_login' in verify
    assert '.code' not in request.lower() or 'payload.code' not in request


def test_logout_audit_keeps_actor_context_when_available():
    source = Path('auth/routes.py').read_text(encoding='utf-8')
    block = source[source.index('@router.post("/logout")'):]
    assert 'Depends(web_context)' in block
    assert 'actor.club_id' in block and 'actor.user_id' in block


def test_product_history_casts_text_audit_object_id_for_postgresql():
    source = Path('auth/forecast_routes.py').read_text(encoding='utf-8')
    start = source.index('@catalog_router.get("/products/{product_id}/history")')
    block = source[start:source.index('@catalog_router.post("/products/{product_id}/stock")', start)]
    assert 'AuditEntry.object_id == str(product_id)' in block
