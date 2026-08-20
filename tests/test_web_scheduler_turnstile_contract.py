from pathlib import Path


def test_scheduler_and_turnstile_web_controls_are_scoped_and_secret_safe():
    api = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    ui = Path("static/web/components.js").read_text(encoding="utf-8")
    for path in ("/staff/settings/schedulers", "/staff/settings/turnstile"):
        assert path in api and path in ui
    assert 'WEB_SETTINGS_MUTATIONS_ENABLED' in api
    assert 'web:schedulers:' in api and 'web:turnstile:' in api
    turnstile_block = api[api.index('@settings_router.get("/turnstile")'):api.index('@settings_router.patch("/turnstile")')]
    assert 'password' not in turnstile_block or 'configured' in turnstile_block
    assert 'stock_reminders' in api and 'work_schedule_reminders' in api
