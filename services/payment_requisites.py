from __future__ import annotations

from html import escape

DEFAULT_PAYMENT_INFO = "⚠️ Реквизиты временно не указаны. Пожалуйста, свяжитесь с администратором."


def get_payment_info_text(club_settings: dict | None) -> str:
    settings = club_settings if isinstance(club_settings, dict) else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    payment_info = str(ui.get("payment_info") or "").strip()
    if not payment_info or "+79000000000" in payment_info:
        return DEFAULT_PAYMENT_INFO
    return payment_info


def build_payment_instruction_text(*, title: str, amount_kopecks: int, payment_info: str, extra_lines: list[str] | None = None) -> str:
    lines = [
        f"💳 <b>{escape(title)}</b>",
        f"Сумма: <b>{amount_kopecks / 100:.2f} ₽</b>",
        "",
        "<b>Реквизиты для перевода:</b>",
        f"<code>{escape(payment_info)}</code>",
    ]
    if extra_lines:
        lines.extend([""] + extra_lines)
    lines.extend(
        [
            "",
            "После перевода заявка уйдёт на подтверждение администратору.",
        ]
    )
    return "\n".join(lines)
