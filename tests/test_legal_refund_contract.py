from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_offer_states_no_refunds_except_when_club_cannot_provide_paid_services():
    page = (ROOT / "templates/oferta.html").read_text(encoding="utf-8")
    assert "Оплаченные услуги, абонементы, занятия, заморозки и товары возврату не подлежат" in page
    assert "Клуб по своей вине не может предоставить Пользователю уже оплаченные услуги" in page


def test_privacy_page_links_the_same_payment_and_refund_rule():
    page = (ROOT / "templates/privacy.html").read_text(encoding="utf-8")
    assert "Оплаченные услуги, абонементы, занятия, заморозки и товары Клубом не возвращаются" in page
    assert 'href="/oferta"' in page


def test_platform_offer_is_separate_and_club_documents_have_a_real_toggle():
    platform = (ROOT / "templates/platform_oferta.html").read_text(encoding="utf-8")
    legal = (ROOT / "services/legal_documents.py").read_text(encoding="utf-8")
    start = (ROOT / "handlers/start.py").read_text(encoding="utf-8")
    payment = (ROOT / "handlers/super_admin_payment.py").read_text(encoding="utf-8")
    admin = (ROOT / "templates/admin_legal.html").read_text(encoding="utf-8")
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert "Публичная оферта SaaS-лицензии" in platform
    assert "PLATFORM_LEGAL" in legal
    assert "legal_documents_enabled(club_settings)" in start
    assert "/platform-oferta" in payment
    assert "toggleLegal" in admin
    save_section = views[views.index('fields = ("provider_name"'):views.index('legal["document_version"]')]
    assert "platform_name" not in save_section


def test_inactive_disciplines_are_blocked_in_legacy_purchase_paths_and_acceptance_is_club_scoped():
    bot_payments = (ROOT / "handlers/payments.py").read_text(encoding="utf-8")
    webapp_api = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    start = (ROOT / "handlers/start.py").read_text(encoding="utf-8")
    model = (ROOT / "database/db.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/s3t4u5v6w7x8_add_club_legal_acceptances.py").read_text(encoding="utf-8")
    assert bot_payments.count('discipline_cfg.get("active", True)') >= 2
    assert "Дисциплина отключена администратором" in webapp_api
    assert "LegalAcceptance.club_id == club.id" in start
    assert "class LegalAcceptance" in model
    assert "uq_legal_acceptance_user_club" in migration


def test_new_club_creation_has_safe_defaults_and_retries_failed_webhooks():
    creation = (ROOT / "handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    constants = (ROOT / "database/constants.py").read_text(encoding="utf-8")
    registry = (ROOT / "services/bot_registry.py").read_text(encoding="utf-8")
    assert "len(name) > 100" in creation
    assert "func.lower(Club.name)" in creation
    assert "Club.bot_token == bot_token" in creation
    assert '"enabled": False' in constants
    assert "except Exception:" in registry
    assert "await bot.session.close()" in registry
    assert "bots_dict[bot_token] = bot" in registry
    assert "set_chat_menu_button" in registry
    assert "menu_button_url=f\"https://{new_club.id}.speedycrm.ru/webapp/client-cabinet?club_id={new_club.id}\"" in creation
