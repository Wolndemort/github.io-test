from pathlib import Path


def test_phase1_web_assets_and_auth_contract_are_present():
    components = Path("static/web/components.js").read_text(encoding="utf-8")
    design = Path("static/web/design.css").read_text(encoding="utf-8")
    auth = Path("auth/routes.py").read_text(encoding="utf-8")
    assert "SpeedyCRMWeb" in components
    assert "/auth/logout" in components
    assert "X-CSRF-Token" in components
    assert "web-shell" in design
    assert 'router.get("/login")' in auth
    assert 'router.get("/me")' in auth


def test_phase1_mutations_are_not_enabled_by_default_in_source():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    for flag in ("WEB_SCHEDULE_MUTATIONS_ENABLED", "WEB_CLIENT_STUDENT_MUTATIONS_ENABLED", "WEB_CLIENT_BIND_PHONE_ENABLED"):
        assert f'{flag}", "0"' in source


def test_phase1_checklist_keeps_production_gate_explicit():
    checklist = Path("PHASE1_CHECKLIST.md").read_text(encoding="utf-8")
    assert "Production gate" in checklist
    assert "No direct push to `master`" in checklist
    assert "Web mutation flags disabled" in checklist
