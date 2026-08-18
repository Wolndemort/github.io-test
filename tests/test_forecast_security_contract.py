from pathlib import Path


def test_forecast_route_is_admin_protected_and_bounded():
    source = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    assert '@router.get("/forecast"' in source
    assert "await verify_webapp_admin(club, init_data)" in source
    assert "timedelta(days=366)" in source
    assert 'status_code=400' in source


def test_old_statistics_page_no_longer_renders_forecast_payload():
    source = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    stats_section = source.split('@router.get("/forecast"', 1)[0]
    assert '"renewal": renewal' not in stats_section


def test_forecast_default_window_includes_history_and_future():
    source = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    assert "local_today - timedelta(days=30)" in source
    assert "local_today + timedelta(days=30)" in source
