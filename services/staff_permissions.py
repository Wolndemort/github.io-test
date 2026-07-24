from __future__ import annotations

ROLE_PERMISSIONS = {
    "cashier": {"cash_sale", "products_view", "products_manage", "cash_view"},
    "coach": {"schedule_view", "schedule_edit", "qr_checkin"},
    "manager": {"cash_sale", "products_view", "products_manage", "cash_view", "schedule_view", "schedule_edit", "tariffs_manage", "qr_checkin"},
}


def permissions_for_staff(staff) -> set[str]:
    role_permissions = ROLE_PERMISSIONS.get(getattr(staff, "role", ""), set())
    custom = getattr(staff, "permissions", None) or {}
    extra = custom.get("allow", []) if isinstance(custom, dict) else []
    denied = custom.get("deny", []) if isinstance(custom, dict) else []
    return (set(role_permissions) | {str(x) for x in extra}) - {str(x) for x in denied}


def staff_can(staff, permission: str) -> bool:
    return bool(staff and getattr(staff, "is_active", False) and permission in permissions_for_staff(staff))
