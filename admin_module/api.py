import pandas as pd
import io
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Depends, HTTPException, APIRouter, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status
from starlette.responses import StreamingResponse
from database.db import User, Student
from database.db import get_session
from config import fastapi_key


router = APIRouter(prefix="/stats", tags=["Analytics"])
templates = Jinja2Templates(directory="templates")

API_KEY_NAME = "X-API-Key"
API_KEY = fastapi_key

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def get_api_key(header_value: str = Security(api_key_header)):
    if header_value == API_KEY:
        return header_value
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Доступ запрещен: неверный API ключ"
    )


@router.get("/export/excel")
async def export_students_to_excel(session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    result = await session.execute(select(Student))
    df = pd.DataFrame([{"Имя": s.name, "Баланс": s.balance_lessons} for s in result.scalars().all()])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Атлеты')
    output.seek(0)
    headers = {'Content-Disposition': 'attachment; filename="report.xlsx"'}
    return StreamingResponse(output, headers=headers, media_type='application/vnd.ms-excel')


@router.get("/revenue", response_class=HTMLResponse)
async def get_revenue_stats(
        request=Request,
        session: AsyncSession = Depends(get_session),
        _=Depends(get_api_key)
):
    result = await session.execute(select(Student))
    students = result.scalars().all()
    if not students:
        return HTMLResponse(content="<h1>Данных пока нет</h1>", status_code=200)
    data = [
        {"name": s.name, "balance": s.balance_lessons, 'is_frozen': s.is_frozen}
        for s in students
    ]
    df = pd.DataFrame(data)

    total_lessons = df["balance"].sum()
    estimated_revenue = total_lessons * 500
    frozen_count = df[df["is_frozen"] == 1].shape[0]
    top_students = df.nlargest(3, "balance")[["name", "balance"]].to_dict(orient="records")

    return templates.TemplateResponse(
        "stats.html",
        {"request": request,
            "total_lessons": int(total_lessons),
            "revenue": int(estimated_revenue),
            "frozen": frozen_count,
            "top_students": top_students}
    )


class StudentCreate(BaseModel):
    name: str
    parent_id: int


@router.post("/students")
async def create_student(data: StudentCreate, session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    new_student = Student(name=data.name, parent_id=data.parent_id)
    session.add(new_student)
    await session.commit()
    return {"status": "success", "student": new_student.name}


@router.get("/users", response_model=None)
async def get_all_users(session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    query = select(User).options(selectinload(User.students))
    result = await session.execute(query)
    users = result.scalars().all()
    return users


@router.get("/students/{student_id}")
async def get_student_info(student_id: int, session: AsyncSession = Depends(get_session), _=Depends(get_api_key)):
    """Получить детальную информацию по конкретному ученику"""
    query = select(Student).where(Student.id == student_id)
    result = await session.execute(query)
    student = result.scalar_one_or_none()

    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student
