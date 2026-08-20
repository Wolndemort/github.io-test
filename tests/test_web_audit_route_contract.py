from pathlib import Path


def test_audit_page_is_not_shadowed_by_student_hub_route():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    route = '@web_router.get("/staff/audit", response_class=HTMLResponse)'
    assert source.count(route) == 1
    audit_page = source.index('async def web_audit_page')
    assert route in source[audit_page - 180:audit_page]
