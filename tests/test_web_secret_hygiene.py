from pathlib import Path


def test_generated_web_assets_do_not_contain_secrets_or_credential_logging():
    files = [Path("static/web/components.js"), Path("static/web/design.css")]
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in files)
    for forbidden in ("bot_token", "secret_key", "private_key", "authorization:", "cookie:"):
        assert forbidden not in text


def test_web_html_does_not_persist_telegram_init_data_in_links():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8").lower()
    assert "init_data=" not in source
