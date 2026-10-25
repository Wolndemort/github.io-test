"""Tenant-specific data used by the public legal documents.

The documents must never identify the SaaS operator as the provider of a
club's training services.  Missing fields stay explicit so an owner can fill
them in before publishing the bot.
"""

from __future__ import annotations

from collections.abc import Mapping


LEGAL_PROVIDER_TYPES = ("ИП", "ООО", "Самозанятый", "Физическое лицо")

# Постоянные реквизиты SaaS-платформы. Они не берутся из настроек клуба:
# клубная оферта и оферта платформы — разные договорные уровни.
PLATFORM_LEGAL = {
    "provider_name": "Омаров Магомед Ильясович",
    "provider_type": "ИП",
    "inn": "010510361804",
    "ogrn": "326010000012776",
    "legal_address": "Г. Майкоп, ул. Курганная, 66",
    "club_address": "Г. Майкоп, ул. Титова, 181",
    "email": "Kid.cudi.1995@mail.ru",
    "phone": "89606666165",
    "document_version": "1.0",
    "updated_at": "",
    "privacy_operator": "Омаров Магомед Ильясович",
    "platform_name": "SpeedyCRM",
}


def platform_legal_context() -> dict:
    """Build a stable, non-tenant legal context for the SaaS documents."""
    return {
        "platform_name": PLATFORM_LEGAL["platform_name"],
        "provider_name": PLATFORM_LEGAL["provider_name"],
        "provider_type": PLATFORM_LEGAL["provider_type"],
        "inn": PLATFORM_LEGAL["inn"],
        "ogrn": PLATFORM_LEGAL["ogrn"],
        "legal_address": PLATFORM_LEGAL["legal_address"],
        "club_address": PLATFORM_LEGAL["club_address"],
        "email": PLATFORM_LEGAL["email"],
        "phone": PLATFORM_LEGAL["phone"],
        "document_version": PLATFORM_LEGAL["document_version"],
        "updated_at": PLATFORM_LEGAL["updated_at"] or "дата публикации указана в актуальной редакции",
        "privacy_operator": PLATFORM_LEGAL["privacy_operator"],
    }


def legal_documents_enabled(settings: Mapping | None) -> bool:
    """Return the per-club publication switch, defaulting on for old clubs."""
    legal = settings.get("legal", {}) if isinstance(settings, Mapping) else {}
    return bool(legal.get("enabled", True)) if isinstance(legal, Mapping) else True


def legal_context(club) -> dict:
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    legal = settings.get("legal", {}) if isinstance(settings.get("legal", {}), dict) else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    return {
        "legal_enabled": legal_documents_enabled(settings),
        "club_name": ui.get("club_name") or getattr(club, "name", "Клуб"),
        "provider_name": legal.get("provider_name") or "Заполните юридическое наименование клуба",
        "provider_type": legal.get("provider_type") or "ИП/ООО",
        "inn": legal.get("inn") or "не указан",
        "ogrn": legal.get("ogrn") or "не указан",
        "legal_address": legal.get("legal_address") or "не указан",
        "club_address": legal.get("club_address") or "не указан",
        "email": legal.get("email") or "не указан",
        "phone": legal.get("phone") or "не указан",
        "document_version": legal.get("document_version") or "1.0",
        "updated_at": legal.get("updated_at") or "дата публикации не указана",
        "privacy_operator": legal.get("privacy_operator") or legal.get("provider_name") or getattr(club, "name", "Клуб"),
        "platform_name": legal.get("platform_name") or "платформа автоматизации клуба",
    }
