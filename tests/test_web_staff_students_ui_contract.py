from pathlib import Path


def test_staff_students_render_links_to_functional_student_hubs():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert 'location.pathname === "/staff/students"' in source
    assert "/api/v1/staff/students/data?limit=50" in source
    assert 'href="/staff/students/${student.id}"' in source
    assert Path("static/web/athletes.js").read_text(encoding="utf-8").count("data-delete") > 0
    assert "freeze_days_entitlement" in Path("auth/forecast_routes.py").read_text(encoding="utf-8")
