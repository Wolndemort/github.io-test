from fastapi import Depends, HTTPException
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
    if not parent or parent.club_id != data.club_id:
        raise HTTPException(status_code=403, detail="Parent is not linked to this club")
    new_student = Student(name=data.name.strip(), parent_id=data.parent_id, club_id=data.club_id)
    session.add(new_student)
    await session.commit()
    return {"status": "success", "student": new_student.name}


@router.get("/stats/users", response_model=None)
async def get_all_users(session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    result = await session.execute(select(User).options(selectinload(User.students)))
    return result.scalars().all()


@router.get("/stats/students/{student_id}")
async def get_student_info(student_id: int, session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    result = await session.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student
