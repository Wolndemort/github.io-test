from pathlib import Path


def test_common_email_profile_page_and_navigation_exist():
    routes = Path("auth/routes.py").read_text(encoding="utf-8")
    components = Path("static/web/components.js").read_text(encoding="utf-8")
    assert '@router.get("/email-profile"' in routes
    assert 'mountEmailBinding("email-binding")' in routes
    assert 'href="/auth/email-profile"' in components
