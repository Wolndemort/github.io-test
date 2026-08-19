"""Tenant-specific data used by the public legal documents.

The documents must never identify the SaaS operator as the provider of a
club's training services.  Missing fields stay explicit so an owner can fill
them in before publishing the bot.
"""


def legal_context(club) -> dict:
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    legal = settings.get("legal", {}) if isinstance(settings.get("legal", {}), dict) else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    return {
        "club_name": legal.get("provider_name") or ui.get("club_name") or getattr(club, "name", "Клуб"),
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
