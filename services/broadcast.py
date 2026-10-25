from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import Club, ClubStaff, Student, StudentParent, User


async def resolve_broadcast_recipients(
    session: AsyncSession,
    club_id: int,
    student_ids: list[int] | None = None,
) -> set[int]:
    """Return Telegram IDs for a club-wide or selected-athlete broadcast."""
    if student_ids is None:
        user_ids = {
            int(user_id)
            for user_id in (await session.scalars(select(User.user_id).where(User.club_id == club_id))).all()
            if user_id
        }
        linked = await session.scalars(
            select(StudentParent.parent_id)
            .join(Student, StudentParent.student_id == Student.id)
            .where(Student.club_id == club_id)
        )
        user_ids.update(int(user_id) for user_id in linked.all() if user_id)
        staff_ids = {
            int(telegram_id)
            for telegram_id in (await session.scalars(select(ClubStaff.telegram_id).where(ClubStaff.club_id == club_id))).all()
            if telegram_id
        }
        user_ids.difference_update(staff_ids)
        return user_ids

    selected = {int(student_id) for student_id in student_ids}
    if not selected:
        return set()
    valid_ids = {
        int(student_id)
        for student_id in (
            await session.scalars(
                select(Student.id).where(Student.club_id == club_id, Student.id.in_(selected))
            )
        ).all()
    }
    if not valid_ids:
        return set()
    primary = await session.scalars(
        select(Student.parent_id).where(Student.club_id == club_id, Student.id.in_(valid_ids))
    )
    linked = await session.scalars(
        select(StudentParent.parent_id)
        .join(Student, StudentParent.student_id == Student.id)
        .where(Student.club_id == club_id, Student.id.in_(valid_ids))
    )
    return {int(user_id) for user_id in [*primary.all(), *linked.all()] if user_id}


async def broadcast_recipients_for_student_search(
    session: AsyncSession,
    club_id: int,
    query: str = "",
    limit: int = 500,
) -> list[dict]:
    """Return selectable athletes with their linked recipient count."""
    needle = query.strip().casefold()
    filters = [Student.club_id == club_id]
    if needle:
        pattern = f"%{needle}%"
        filters.append(
            or_(
                Student.name.ilike(pattern),
                Student.parent_phone.ilike(pattern),
                Student.parent_phone_secondary.ilike(pattern),
            )
        )
    students = list(
        (
            await session.scalars(
                select(Student).where(*filters).order_by(Student.name).limit(limit)
            )
        ).all()
    )
    rows = []
    for student in students:
        recipient_ids = await resolve_broadcast_recipients(session, club_id, [student.id])
        rows.append(
            {
                "id": student.id,
                "name": student.name,
                "discipline": student.discipline,
                "parent_phone": student.parent_phone,
                "parent_phone_secondary": student.parent_phone_secondary,
                "recipient_count": len(recipient_ids),
                "expire_date": student.expire_date.isoformat() if student.expire_date else None,
                "balance_lessons": int(student.balance_lessons or 0),
                "last_visit": student.last_visit.isoformat() if student.last_visit else None,
            }
        )
    return rows
