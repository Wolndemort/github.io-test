from pathlib import Path


def test_visual_system_has_collapsible_navigation_and_long_lists():
    css = Path('static/web/design.css').read_text(encoding='utf-8')
    js = Path('static/web/components.js').read_text(encoding='utf-8')
    assert '.web-nav-menu' in css
    assert '.web-long-list' in css
    assert 'navLinks' in js and 'collapseLongTables' in js
    assert 'Показать список' in js


def test_visual_system_normalizes_links_and_table_overflow():
    css = Path('static/web/design.css').read_text(encoding='utf-8')
    assert '.web-container a, .web-card a, .web-hero a' in css
    assert 'overflow-wrap: anywhere' in css
    assert 'white-space: normal' in css


def test_staff_ux_has_dashboard_search_details_and_mobile_navigation():
    ux = Path('static/web/ux.js').read_text(encoding='utf-8')
    css = Path('static/web/design.css').read_text(encoding='utf-8')
    routes = Path('auth/forecast_routes.py').read_text(encoding='utf-8')
    for marker in ('/staff/overview', '/staff/students/', 'web-global-search', 'web-tabs', 'web-mobile-nav'):
        assert marker in ux
    assert '.web-mobile-nav' in css
    assert '@overview_router.get("/search")' in routes
