from pathlib import Path


def test_staff_navigation_exposes_all_requested_cards():
    source = Path('static/web/components.js').read_text(encoding='utf-8')
    for path in ('/staff/pass', '/staff/checkin', '/staff/cash', '/staff/products', '/staff/discounts', '/staff/table', '/staff/revenue', '/staff/forecast', '/staff/freeze', '/staff/audit'):
        assert path in source


def test_turnstile_mutation_is_idempotent_and_scoped():
    source = Path('auth/forecast_routes.py').read_text(encoding='utf-8')
    block = source[source.index('@checkin_router.post("/open-turnstile")'):source.index('@checkin_router.post("/cancel")')]
    for marker in ('require_csrf', 'idempotency_key', 'web:turnstile:', 'actor.club_id', 'web_turnstile_opened'):
        assert marker in block


def test_native_checkin_matches_telegram_search_and_two_pass_modes():
    source = Path('static/web/checkin.js').read_text(encoding='utf-8')
    for marker in ('Поиск атлета или телефона', 'Без турникета', 'С турникетом', '/api/v1/staff/checkin/manual', 'open_turnstile'):
        assert marker in source


def test_native_catalog_returns_images_and_supports_explicit_product_toggle():
    api = Path('auth/forecast_routes.py').read_text(encoding='utf-8')
    js = Path('static/web/catalog.js').read_text(encoding='utf-8')
    assert '"image_url": p.image_url' in api
    assert '"is_active": bool(p.is_active)' in api
    block = api[api.index('@catalog_router.patch("/products/{product_id}")'):api.index('@catalog_router.delete("/products/{product_id}")')]
    assert '"is_active"' in block
    assert 'data-toggle' in js and 'Выключить товар' in js and 'Включить товар' in js
