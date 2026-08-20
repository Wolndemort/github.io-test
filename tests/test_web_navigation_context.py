from pathlib import Path

def test_shared_navigation_has_staff_and_client_contexts():
    source=Path("static/web/components.js").read_text(encoding="utf-8")
    assert 'includes("client")' in source
    assert "/client/cabinet" in source
    assert "/client/history" in source
    assert "/client/subscriptions" in source
    assert "/client/purchases" in source
    assert "/client/freeze" in source
    assert "/client/me" in source
    assert "/client/legal" in source
    for path in ("/client/schedule", "/client/products", "/client/discounts", "/client/tariffs", "/client/club", "/client/summary/attendance", "/client/summary/subscriptions", "/client/summary/purchases"):
        assert path in source
    assert "/staff/overview" in source
    for path in ("/staff/cash", "/staff/sales", "/staff/audit", "/staff/schedule", "/staff/products", "/staff/discounts", "/staff/tariffs", "/staff/checkin", "/staff/freeze"):
        assert path in source
    for path in ("/staff/settings/legal", "/staff/settings/camera", "/staff/settings/features", "/staff/settings/limits", "/staff/settings/branding", "/staff/settings/integrations"):
        assert path in source
