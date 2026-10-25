from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from services.legal_documents import PLATFORM_LEGAL, legal_context, legal_documents_enabled, platform_legal_context
from handlers.super_admin_payment import SUBSCRIPTION_PRICES
from middlewares.db_saas_midleware import subscription_is_active


ROOT = Path(__file__).parents[1]


def test_legal_documents_are_enabled_for_legacy_clubs_and_can_be_disabled_per_tenant():
    assert legal_documents_enabled({}) is True
    assert legal_documents_enabled({"legal": {"enabled": True}}) is True
    assert legal_documents_enabled({"legal": {"enabled": False}}) is False
    first = SimpleNamespace(name="Первый клуб", club_settings={"legal": {"provider_name": "ООО Первый"}})
    second = SimpleNamespace(name="Второй клуб", club_settings={"legal": {"provider_name": "ИП Второй"}})
    assert legal_context(first)["provider_name"] == "ООО Первый"
    assert legal_context(second)["provider_name"] == "ИП Второй"
    assert legal_context(first)["club_name"] == "Первый клуб"
    assert legal_context(first)["provider_name"] != legal_context(second)["provider_name"]


def test_admin_legal_ui_explains_tenant_scope_and_publication_switch():
    page = (ROOT / "templates/admin_legal.html").read_text(encoding="utf-8")
    assert 'id="legalEnabled"' in page
    assert "Показывать юридические документы" in page
    assert 'href="/privacy?club_id={{ club_id }}"' in page
    assert 'href="/oferta?club_id={{ club_id }}"' in page
    assert "Последнее сохранение" in page
    assert 'data-field="updated_at"' not in page
    assert "document_version" in page


def test_legal_save_is_club_scoped_validated_and_server_dated():
    source = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert 'club_id: int = Query(...)' in source
    assert "with_for_update=True" in source
    assert "LEGAL_PROVIDER_TYPES" in source
    assert "ИНН должен содержать 10 или 12 цифр" in source
    assert "ОГРН/ОГРНИП должен содержать 13 или 15 цифр" in source
    assert "Укажите корректный email" in source
    assert 'datetime.now(timezone.utc).isoformat(timespec="seconds")' in source
    assert 'settings["legal"] = legal' in source


def test_start_skips_consent_only_for_the_current_club_when_legal_is_disabled():
    source = (ROOT / "handlers/start.py").read_text(encoding="utf-8")
    assert "legal_documents_enabled(club_settings)" in source
    assert "if not db_user.is_accepted and legal_documents_enabled(club_settings):" in source


def test_platform_legal_documents_are_stable_and_separate_from_club_settings():
    first = platform_legal_context()
    second = platform_legal_context()
    assert first == second
    assert first["provider_name"] == PLATFORM_LEGAL["provider_name"]
    assert first["platform_name"] == "SpeedyCRM"
    assert first["provider_name"] != legal_context(SimpleNamespace(name="Клуб", club_settings={"legal": {"provider_name": "ООО Клуб"}}))["provider_name"]


def test_saas_prices_are_one_source_for_all_payment_paths():
    assert SUBSCRIPTION_PRICES == {30: 700000, 90: 1800000, 365: 7000000}
    offer = (ROOT / "templates/platform_oferta.html").read_text(encoding="utf-8")
    assert "1 500" not in offer and "15 000" not in offer
    payment = (ROOT / "handlers/super_admin_payment.py").read_text(encoding="utf-8")
    assert "buy_sub_90" in payment and "18 000" in payment


def test_subscription_middleware_expires_at_exact_boundary_and_handles_utc():
    boundary = datetime(2026, 9, 4, 12, 0, 0)
    assert subscription_is_active(boundary, boundary) is False
    assert subscription_is_active(boundary, boundary - timedelta(microseconds=1)) is True
    assert subscription_is_active(boundary, boundary + timedelta(microseconds=1)) is False
    assert subscription_is_active(boundary, boundary.replace(tzinfo=timezone.utc)) is False
    assert subscription_is_active(None, boundary) is False


def test_platform_routes_and_super_menu_expose_the_two_contract_levels():
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    super_admin = (ROOT / "handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    assert '@router.get("/platform/privacy"' in views
    assert '@router.get("/platform/oferta"' in views
    assert "/platform/privacy" in super_admin
    assert "/platform/oferta" in super_admin
    assert "Стоимость лицензии CRM" in (ROOT / "templates/platform_oferta.html").read_text(encoding="utf-8")
    assert "не является исполнителем тренировочных" in (ROOT / "templates/platform_privacy.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("page", "marker"),
    [("privacy.html", "{{ club_name }}"), ("oferta.html", "{{ provider_name }}")],
)
def test_public_legal_documents_keep_club_specific_placeholders(page, marker):
    content = (ROOT / "templates" / page).read_text(encoding="utf-8")
    assert "{{ provider_name }}" in content or "{{ provider_type }}" in content
    assert marker in content
