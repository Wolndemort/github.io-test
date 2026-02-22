import pandas as pd
import io
from fastapi import Depends, HTTPException, APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import StreamingResponse
from database.db import User, Student
from database.db import get_session


router = APIRouter(prefix="/stats", tags=["Analytics"])


@router.get("/export/excel")
async def export_students_to_excel(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Student))
    df = pd.DataFrame([{"Имя": s.name, "Баланс": s.balance_lessons} for s in result.scalars().all()])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Атлеты')
    output.seek(0)
    headers = {'Content-Disposition': 'attachment; filename="report.xlsx"'}
    return StreamingResponse(output, headers=headers, media_type='application/vnd.ms-excel')
#добавить кнопку !


@router.get("/revenue")
async def get_revenue_stats(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Student))
    students = result.scalars().all()
    if not students:
        return {"message": "Данных пока нет"}
    data = [
        {
            "name": s.name,
            "balance": s.balance_lessons,
            "is_frozen": s.is_frozen
        } for s in students
    ]
    df = pd.DataFrame(data)
    total_lessons = df["balance"].sum()
    avg_lessons = df["balance"].mean()
    estimated_revenue = total_lessons * 500  # Пример: 1 занятие = 500 руб.
    frozen_count = df[df["is_frozen"] == 1].shape[0]
    return {
        "total_active_lessons": int(total_lessons),
        "estimated_revenue_rub": int(estimated_revenue),
        "average_lessons_per_student": round(float(avg_lessons), 2),
        "frozen_students": frozen_count,
        "top_students": df.nlargest(3, "balance")[["name", "balance"]].to_dict(orient="records")
    }


class StudentCreate(BaseModel):
    name: str
    parent_id: int


@router.post("/students")
async def create_student(data: StudentCreate, session: AsyncSession = Depends(get_session)):
    new_student = Student(name=data.name, parent_id=data.parent_id)
    session.add(new_student)
    await session.commit()
    return {"status": "success", "student": new_student.name}


@router.get("/users", response_model=None)
async def get_all_users(session: AsyncSession = Depends(get_session)):
    query = select(User).options(selectinload(User.students))
    result = await session.execute(query)
    users = result.scalars().all()
    return users


@router.get("/students/{student_id}")
async def get_student_info(student_id: int, session: AsyncSession = Depends(get_session)):
    """Получить детальную информацию по конкретному ученику"""
    query = select(Student).where(Student.id == student_id)
    result = await session.execute(query)
    student = result.scalar_one_or_none()

    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student

