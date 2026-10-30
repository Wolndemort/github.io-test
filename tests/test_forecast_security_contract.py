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


def test_forecast_forms_preserve_independent_date_windows():
    template = Path("templates/forecast.html").read_text(encoding="utf-8")
    assert 'name="revenue_from" value="{{ filters.revenue_from }}"' in template
    assert 'name="visits_from" value="{{ filters.visits_from }}"' in template
    assert template.count('name="date_from" value="{{ filters.date_from }}"') >= 2


def test_forecast_registrations_use_the_general_window():
    source = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    assert "start_date <= moscow_calendar_date(s.created_at) <= finish_date" in source
    assert '"revenue_from": revenue_start' in source
