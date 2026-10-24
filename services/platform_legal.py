"""Immutable legal context for the SaaS platform offer.

These values describe the platform owner, not a tenant club.  Club admins
cannot edit or override them through club settings.
"""

PLATFORM_LEGAL = {
    "provider_name": "Омаров Магомед Ильясович",
    "provider_type": "ИП",
    "inn": "010510361804",
    "ogrn": "326010000012776",
    "legal_address": "Г. Майкоп, ул. Курганная, 66",
    "club_address": "Г. Майкоп, ул. Титова, 181",
    "email": "Kid.cudi.1995@mail.ru",
    "phone": "89606666165",
    "platform_name": "Speedy CRM",
    "document_version": "1.0",
    "updated_at": "2026-10-24",
}


def platform_legal_context() -> dict:
    return dict(PLATFORM_LEGAL)
