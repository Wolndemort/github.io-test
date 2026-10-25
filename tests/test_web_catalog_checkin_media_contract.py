from pathlib import Path


def test_checkin_search_supports_phone_and_client_shop_exposes_product_images():
    api = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    client = Path("static/web/client.js").read_text(encoding="utf-8")
    block = api[api.index('@checkin_router.get("/students")'):api.index('@checkin_router.post("/manual")')]
    assert "Student.phone.ilike" in block
    assert '"phone": s.phone' in block
    endpoint = api[api.index('@client_router.get("/products/data")'):api.index('@client_router.post("/products/order")')]
    assert '"image_url": p.image_url' in endpoint
    assert "product-image" in client
    assert "upload-image" in Path("static/web/catalog.js").read_text(encoding="utf-8")
