from pathlib import Path


def test_settings_ui_has_payment_setup_and_fixed_navigation_layout():
    components = Path('static/web/components.js').read_text(encoding='utf-8')
    css = Path('static/web/design.css').read_text(encoding='utf-8')
    payment = Path('static/web/payment-settings.js').read_text(encoding='utf-8')
    assert '/staff/settings/payments' in components
    assert 'display: flex' in css and 'flex-wrap: wrap' in css
    assert '/api/v1/staff/settings/payments' in payment
    assert 'secret_key' in payment and 'sbp_enabled' in payment


def test_limits_keep_freeze_step_visible_and_persistable():
    source = Path('auth/forecast_routes.py').read_text(encoding='utf-8')
    assert '"freeze_days_step"' in source
