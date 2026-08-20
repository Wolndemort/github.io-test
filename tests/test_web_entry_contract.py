from pathlib import Path


def test_telegram_web_entry_uses_init_data_without_rendering_it():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    start = source.index('@router.get("/web-entry"')
    end = source.index('class TelegramExchangePayload', start)
    block = source[start:end]
    assert "telegram-web-app.js" in block
    assert "tg.initData" in block
    assert "init_data:tg.initData" in block
    assert "document.body.innerHTML" not in block
