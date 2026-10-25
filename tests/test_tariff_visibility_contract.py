from pathlib import Path

from handlers.buttons import discipline, get_cash_options_kb, get_pay_options_kb


ROOT = Path(__file__).parents[1]


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_client_tariff_keyboard_hides_inactive_tariffs_but_cash_keyboard_keeps_them():
    cfg = {
        "name": "Бокс",
        "tariffs": [
            {"count": 8, "days": 30, "price": 7000, "active": True},
            {"count": 12, "days": 30, "price": 9000, "active": False},
        ],
    }

    client_callbacks = [b.callback_data for b in _buttons(get_pay_options_kb(cfg, "boxing"))]
    cash_callbacks = [b.callback_data for b in _buttons(get_cash_options_kb(cfg))]

    assert "set_tariff_boxing_0" in client_callbacks
    assert "set_tariff_boxing_1" not in client_callbacks
    assert "confirm_cash_0" in cash_callbacks
    assert "confirm_cash_1" in cash_callbacks


def test_client_tariff_callback_rejects_negative_indexes():
    payments = (ROOT / "handlers/payments.py").read_text(encoding="utf-8")
    client_block = payments[payments.index("async def process_kids_limit"):payments.index("async def process_one_click_payment")]
    assert "tariff_idx < 0 or tariff_idx >= len(tariffs)" in client_block


def test_disabled_disciplines_are_hidden_from_client_menu_and_legacy_webapp_filters_them():
    markup = discipline({
        "disciplines": {
            "boxing": {"name": "Бокс", "active": True},
            "judo": {"name": "Дзюдо", "active": False},
            "old": {"name": "Старое направление"},
        }
    })
    callbacks = [b.callback_data for b in _buttons(markup)]
    assert "buy_boxing" in callbacks
    assert "buy_judo" not in callbacks
    assert "buy_old" in callbacks

    cabinet = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert "if isinstance(info, dict) and info.get(\"active\", True)" in cabinet
    assert "if not discipline_cfg or not discipline_cfg.get(\"active\", True)" in cabinet


def test_tariff_and_discipline_lifecycle_preserves_visibility_flags():
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    bot_tariffs = (ROOT / "handlers/admin_tariffs_schedule.py").read_text(encoding="utf-8")
    cash_api = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")

    assert 'payload.action == "toggle_tariff"' in views
    assert 'old_active = bool(tariffs[payload.index].get("active", True))' in views
    assert '"active": old_active' in views
    assert '"active": True' in views
    assert '"active": True}' in bot_tariffs
    assert 'disciplines = settings.get("disciplines", {})' in cash_api
    assert 'tariff = tariffs[payload.tariff_idx]' in cash_api


def test_cash_flows_do_not_reject_hidden_tariffs_for_staff():
    payments = (ROOT / "handlers/payments.py").read_text(encoding="utf-8")
    cash_start = payments.index("async def final_cash_pay")
    cash_block = payments[cash_start:]
    assert "selected_tariff = tariffs[tariff_idx]" in cash_block
    assert "if not selected_tariff.get(\"active\", True)" not in cash_block

    page = (ROOT / "templates/admin_cash_subscription.html").read_text(encoding="utf-8")
    assert "disciplines[discipline.value]?.tariffs||[]" in page
