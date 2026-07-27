from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from admin_module.router_base import router
from admin_module.security import get_api_key
from database.db import Student, User, Club, get_session


class StudentCreate(BaseModel):
    name: str
    parent_id: int
    club_id: int


@router.post("/stats/students")
async def create_student(data: StudentCreate, session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    club = await session.get(Club, data.club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    parent = await session.get(User, data.parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    new_student = Student(name=data.name.strip(), parent_id=data.parent_id, club_id=data.club_id)
    session.add(new_student)
    await session.commit()
    return {"status": "success", "student": new_student.name}


@router.get("/stats/users", response_model=None)
async def get_all_users(
    club_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
    _=Depends(get_api_key),
):
    result = await session.execute(
        select(User).where(User.club_id == club_id).options(selectinload(User.students))
    )
    users = result.scalars().all()
    return [
        {
            "user_id": user.user_id,
            "full_name": user.full_name,
            "is_accepted": user.is_accepted,
            "is_biometric_enabled": user.is_biometric_enabled,
            "students": [
                {
                    "id": student.id,
                    "name": student.name,
                    "club_id": student.club_id,
                    "parent_id": student.parent_id,
                }
                for student in user.students
                if student.club_id == club_id
            ],
        }
        for user in users
    ]


@router.get("/stats/students/{student_id}")
async def get_student_info(
    student_id: int,
    club_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
    _=Depends(get_api_key),
):
    result = await session.execute(select(Student).where(Student.id == student_id, Student.club_id == club_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student
