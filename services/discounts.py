from datetime import date
from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import Discount, DiscountAssignment


def tariff_ref(discipline: str, tariff_idx: int, tariff: dict) -> dict:
    """Build a snapshot for config-based tariffs (which have no DB id)."""
    return {"discipline": str(discipline), "tariff_idx": int(tariff_idx),
            "price": str(tariff.get("price", "")),
            "count": int(tariff.get("count", 0) or 0),
            "days": int(tariff.get("days", 30) or 30)}


def discount_applies_to_tariff(discount: Discount, discipline: str, tariff_idx: int, tariff: dict) -> bool:
    """Empty refs preserve legacy unrestricted discounts; non-empty refs are allowlists."""
    refs = discount.tariff_refs or []
    if not refs:
        return True
    current = tariff_ref(discipline, tariff_idx, tariff)
    return any(
        str(ref.get("discipline")) == current["discipline"]
        and int(ref.get("tariff_idx", -1)) == current["tariff_idx"]
        and str(ref.get("price", "")) == current["price"]
        and int(ref.get("count", 0) or 0) == current["count"]
        and int(ref.get("days", 30) or 30) == current["days"]
        for ref in refs
    )


def filter_tariff_discounts(discounts, discipline: str, tariff_idx: int, tariff: dict):
    return [d for d in discounts if discount_applies_to_tariff(d, discipline, tariff_idx, tariff)]

async def active_discount(session: AsyncSession, club_id: int, user_id: int | None, scope: str, student_id: int | None = None, tariff: tuple[str, int, dict] | None = None):
    today = date.today()
    assignees = [x for x in ((DiscountAssignment.user_id == user_id) if user_id is not None else None, (DiscountAssignment.student_id == student_id) if student_id is not None else None) if x is not None]
    recipient_filter = or_(Discount.is_global.is_(True), or_(*assignees)) if assignees else Discount.is_global.is_(True)
    rows = list((await session.scalars(select(Discount).outerjoin(DiscountAssignment, (DiscountAssignment.discount_id == Discount.id) & (DiscountAssignment.club_id == club_id)).where(
        Discount.club_id == club_id,
        Discount.is_active.is_(True),
        recipient_filter,
        Discount.scope.in_([scope, "all"]),
        (Discount.starts_at.is_(None) | (Discount.starts_at <= today)),
        (Discount.ends_at.is_(None) | (Discount.ends_at >= today)),
    ).order_by(Discount.id.desc()))).all())
    discounts = list({discount.id: discount for discount in rows}.values())
    if tariff:
        discounts = filter_tariff_discounts(discounts, *tariff)
    return discounts[0] if discounts else None

async def active_discounts(session: AsyncSession, club_id: int, user_id: int | None, scope: str, student_id: int | None = None, ids: list[int] | None = None, tariff: tuple[str, int, dict] | None = None):
    assignees = [x for x in ((DiscountAssignment.user_id == user_id) if user_id is not None else None, (DiscountAssignment.student_id == student_id) if student_id is not None else None) if x is not None]
    recipient_filter = or_(Discount.is_global.is_(True), or_(*assignees)) if assignees else Discount.is_global.is_(True)
    query = select(Discount).outerjoin(DiscountAssignment, (DiscountAssignment.discount_id == Discount.id) & (DiscountAssignment.club_id == club_id)).where(
        Discount.club_id == club_id, Discount.is_active.is_(True),
        Discount.scope.in_([scope, "all"]),
        recipient_filter,
        (Discount.starts_at.is_(None) | (Discount.starts_at <= date.today())),
        (Discount.ends_at.is_(None) | (Discount.ends_at >= date.today())),
    )
    if ids:
        query = query.where(Discount.id.in_(ids))
    rows = list((await session.scalars(query.order_by(Discount.priority.asc(), Discount.kind.desc(), Discount.id.asc()))).all())
    discounts = list({discount.id: discount for discount in rows}.values())
    if tariff:
        discounts = filter_tariff_discounts(discounts, *tariff)
    return discounts

def apply_discounts(amount_kopecks: int, discounts: list[Discount]):
    current = amount_kopecks
    applied = []
    for discount in discounts:
        before = current
        current, saved = apply_discount(current, discount)
        if saved:
            applied.append((discount, saved))
        if current <= 0:
            break
    return max(0, current), applied

def apply_discount(amount_kopecks: int, discount: Discount | None):
    if not discount:
        return amount_kopecks, 0
    if discount.kind == "percent":
        result = int(round(amount_kopecks * (100 - discount.value) / 100))
    else:
        result = max(0, amount_kopecks - discount.value)
    return result, amount_kopecks - result
