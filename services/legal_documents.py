"""Tenant-specific data used by the public legal documents.

The documents must never identify the SaaS operator as the provider of a
club's training services.  Missing fields stay explicit so an owner can fill
them in before publishing the bot.
"""

from collections.abc import Mapping
from hashlib import sha256
from services.platform_legal import PLATFORM_LEGAL

LEGAL_PROVIDER_TYPES = {"ИП", "ООО", "Самозанятый", "Физическое лицо"}


def platform_legal_context() -> dict:
    """Return the immutable platform legal details for platform documents."""
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
        "updated_at": PLATFORM_LEGAL.get("updated_at") or "дата публикации указана в актуальной редакции",
        "privacy_operator": PLATFORM_LEGAL.get("privacy_operator") or PLATFORM_LEGAL["provider_name"],
    }


def validate_legal_data(legal: Mapping | None) -> list[str]:
    data = legal if isinstance(legal, Mapping) else {}
    errors = []
    provider_type = str(data.get("provider_type") or "").strip()
    if provider_type not in LEGAL_PROVIDER_TYPES:
        errors.append("Укажите форму исполнителя: ИП, ООО, самозанятый или физическое лицо")
    required = {"provider_name": "полное наименование", "legal_address": "юридический адрес", "club_address": "адрес клуба", "email": "email для обращений", "phone": "телефон клуба", "privacy_operator": "оператор персональных данных"}
    errors.extend(f"Заполните: {label}" for key, label in required.items() if not str(data.get(key) or "").strip())
    document_version = str(data.get("document_version") or "").strip()
    if len(document_version) > 32:
        errors.append("Версия документов не должна быть длиннее 32 символов")
    inn = "".join(ch for ch in str(data.get("inn") or "") if ch.isdigit())
    if len(inn) not in {10, 12}:
        errors.append("ИНН должен содержать 10 или 12 цифр")
    ogrn = "".join(ch for ch in str(data.get("ogrn") or "") if ch.isdigit())
    if provider_type in {"ИП", "ООО"} and len(ogrn) not in {13, 15}:
        errors.append("ОГРН/ОГРНИП должен содержать 13 или 15 цифр")
    email = str(data.get("email") or "").strip()
    if email and ("@" not in email or "." not in email.rsplit("@", 1)[-1]):
        errors.append("Укажите корректный email")
    return errors

def legal_documents_enabled(settings: Mapping | None) -> bool:
    return legal_document_enabled(settings, "privacy") or legal_document_enabled(settings, "offer")


def legal_document_enabled(settings: Mapping | None, document: str) -> bool:
    """Return publication state for one club document.

    ``enabled`` is retained as a backwards-compatible default for clubs saved
    before the per-document switches were introduced.
    """
    legal = settings.get("legal", {}) if isinstance(settings, Mapping) else {}
    if not isinstance(legal, Mapping):
        return False
    key = "privacy_enabled" if document == "privacy" else "offer_enabled"
    return bool(legal.get(key, legal.get("enabled", False)))


def legal_acceptance_version(settings: Mapping | None) -> str:
    legal = settings.get("legal", {}) if isinstance(settings, Mapping) else {}
    if not isinstance(legal, Mapping):
        return "1.0|privacy:0|offer:0"
    version = str(legal.get("document_version") or "1.0")
    # Keep legacy rows comparable when per-document switches were never saved;
    # new settings use a compact fingerprint that fits String(32).
    if "privacy_enabled" not in legal and "offer_enabled" not in legal:
        return version
    raw = f"{version}|privacy:{int(legal_document_enabled(settings, 'privacy'))}|offer:{int(legal_document_enabled(settings, 'offer'))}"
    return sha256(raw.encode("utf-8")).hexdigest()[:32]


def legal_context(club) -> dict:
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    legal = settings.get("legal", {}) if isinstance(settings.get("legal", {}), dict) else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    return {
        "legal_enabled": legal_documents_enabled(settings),
        "privacy_enabled": legal_document_enabled(settings, "privacy"),
        "offer_enabled": legal_document_enabled(settings, "offer"),
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
        "privacy_text": legal.get("privacy_text") or "",
        "offer_text": legal.get("offer_text") or "",
        "updated_at": legal.get("updated_at") or "дата публикации не указана",
        "privacy_operator": legal.get("privacy_operator") or legal.get("provider_name") or getattr(club, "name", "Клуб"),
        # This is platform-owned context and must never be tenant-editable.
        "platform_name": PLATFORM_LEGAL["platform_name"],
        "club_id": getattr(club, "id", None),
    }
