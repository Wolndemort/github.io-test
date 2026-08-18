from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import Discount, DiscountAssignment

async def active_discount(session: AsyncSession, club_id: int, user_id: int | None, scope: str, student_id: int | None = None):
    today = date.today()
    return await session.scalar(select(Discount).join(DiscountAssignment, DiscountAssignment.discount_id == Discount.id).where(
        Discount.club_id == club_id, DiscountAssignment.club_id == club_id,
        Discount.is_active.is_(True),
        ((DiscountAssignment.user_id == user_id) if user_id is not None else False) | ((DiscountAssignment.student_id == student_id) if student_id is not None else False),
        Discount.scope.in_([scope, "all"]),
        (Discount.starts_at.is_(None) | (Discount.starts_at <= today)),
        (Discount.ends_at.is_(None) | (Discount.ends_at >= today)),
    ).order_by(Discount.id.desc()).limit(1))

async def active_discounts(session: AsyncSession, club_id: int, user_id: int | None, scope: str, student_id: int | None = None, ids: list[int] | None = None):
    query = select(Discount).join(DiscountAssignment, DiscountAssignment.discount_id == Discount.id).where(
        Discount.club_id == club_id, DiscountAssignment.club_id == club_id, Discount.is_active.is_(True),
        Discount.scope.in_([scope, "all"]),
        ((DiscountAssignment.user_id == user_id) if user_id is not None else False) | ((DiscountAssignment.student_id == student_id) if student_id is not None else False),
        (Discount.starts_at.is_(None) | (Discount.starts_at <= date.today())),
        (Discount.ends_at.is_(None) | (Discount.ends_at >= date.today())),
    )
    if ids:
        query = query.where(Discount.id.in_(ids))
    return list((await session.scalars(query.order_by(Discount.priority.asc(), Discount.kind.desc(), Discount.id.asc()))).all())

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
