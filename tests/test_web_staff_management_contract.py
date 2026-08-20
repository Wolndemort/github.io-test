from pathlib import Path


def test_staff_management_is_owner_only_allowlisted_idempotent_and_audited():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@settings_router.post("/staff")'); block = source[start:source.index('@checkin_router.get("/data")', start)]
    for value in ("WEB_STAFF_MUTATIONS_ENABLED", "actor_type != \"owner\"", "require_csrf", "web:staff-create:", "web:staff-update:", "ClubStaff.club_id == actor.club_id", "with_for_update()", "web_staff_created", "web_staff_updated"):
        assert value in block
    assert '@settings_router.get("/staff/data")' in source
    assert '"permissions": s.permissions or {}' in source
    ui = Path("static/web/components.js").read_text(encoding="utf-8")
    assert "/api/v1/staff/settings/staff/${Number(f.get(\"staff_id\"))}" in ui
    assert 'permissions: {[f.get("mode")]' in ui
