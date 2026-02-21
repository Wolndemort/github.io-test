from typing import Optional, List
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column, relationship
from sqlalchemy import BigInteger, DateTime, String, func,select, Integer, ForeignKey
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import db_file
from datetime import datetime, timedelta
from loguru import logger
import asyncio


engine = create_async_engine(db_file, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[Optional[str]] = mapped_column(String)
    students: Mapped[List["Student"]] = relationship(back_populates='parent')


class Student(Base):
    __tablename__ = 'students'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey('users.user_id'))
    name: Mapped[str] = mapped_column(String)
    expire_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    can_freeze: Mapped[int] = mapped_column(default=1)
    is_frozen: Mapped[int] = mapped_column(default=0)
    balance_lessons: Mapped[int] = mapped_column(default=0)
    last_visit: Mapped[Optional[datetime]] = mapped_column(DateTime)
    parent: Mapped["User"] = relationship(back_populates="students")


async def init_db():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            logger.success("✅ База данных успешно инициализирована и синхронизирована")
    except Exception as e:
        logger.critical(f"❌ Ошибка при инициализации базы данных: {e}")
        raise e


async def get_student_list(parent_id: int):
    async with AsyncSessionLocal()as session:
        stmt = select(Student).where(Student.parent_id == parent_id)
        result = await session.execute(stmt)
        return result.scalars().all()


async def get_all_subscriptions(user_id: int):
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Student).where(Student.parent_id == user_id)
            result =  await session.execute(stmt)
            return result.scalars().all()
    except Exception as e:
        logger.error(f"❌ Ошибка получения подписок для {user_id}: {e}")
        return []


async def get_expire_students():
    today = datetime.now()
    three_days_limit = today + timedelta(days=3)
    logger.debug(f"🔎 Поиск атлетов, истекающих с {today.strftime('%d.%m')} по {three_days_limit.strftime('%d.%m')}")
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Student).where(
                Student.expire_date <= three_days_limit,
                Student.expire_date >= today
            )
            result = await session.execute(stmt)
            students = result.scalars().all()
            if students:
                logger.info(f"✅ Найдено {len(students)} атлетов для уведомления родителей")
            else:
                logger.debug("Рассылка пуста: истекающих абонементов не найдено")
            return students
    except Exception as e:
        logger.error(f"❌ Ошибка при запросе истекающих абонементов в БД: {e}")
        return []


async def has_subscription(user_id: int):
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Student).where(Student.parent_id == user_id)
            result  = await session.execute(stmt)
            students = result.scalars().all()

            if not students:
                logger.debug(f"Проверка подписки: У родителя {user_id} нет зарегистрированных детей")
                return None, None

            now = datetime.now()
            active_students = [s for s in students if s.expire_date and s.expire_date > now]

            if active_students:
                latest_expire = max(s.expire_date for s in active_students)
                logger.debug(f"Проверка подписки: У ID {user_id} есть активные атлеты. Макс. дата: {latest_expire}")
                return True, latest_expire
            latest_expired = max((s.expire_date for s in students if s.expire_date), default=None)
            logger.debug(f"Проверка подписки: У ID {user_id} все абонементы истекли.")
            return False, latest_expired

    except Exception as e:
        logger.error(f"❌ Ошибка при проверке подписки для ID {user_id}: {e}")
        return None, None


async def add_abon(student_id: int):
    try:
        async with AsyncSessionLocal() as session:
            student = await session.get(Student, student_id)
            if not student:
                logger.info(f"❌ Студент с ID {student_id} не найден в базе")
                return None
            now = datetime.now()
            current_expire_val = student.expire_date
            logger.error(f"🔄 Продление абонемента для студента {student.name} (ID: {student_id}).")
            if current_expire_val and current_expire_val > now:
                start_date = current_expire_val
                logger.debug(f"Абонемент еще активен, добавляем 30 дней к {start_date}")
            else:
                start_date = now
                logger.debug(f"Абонемент истек или новый, отсчет от сегодня: {start_date}")
            new_expire = start_date + timedelta(days=30)
            student.expire_date = new_expire
            student.can_freeze = 1
            student.is_frozen = 0
            await session.commit()
            logger.success(f"✅ Продлен абонемент для {student.name} до {new_expire.strftime('%d.%m.%Y %H:%M')}")
            return new_expire.strftime('%d.%m.%Y'), student.parent_id
    except Exception as e:
        logger.error(f"❌ Ошибка при продлении для студента {student_id}: {e}")
        return None


async def get_daily_stats():
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        async with AsyncSessionLocal() as session:
            logger.debug(f"📊 Сбор статистики по СТУДЕНТАМ за {today_start.strftime('%d.%m.%Y')}")
            stmt_visit = select(func.count(Student.id)).where(Student.last_visit >= today_start)
            visits_count = await session.scalar(stmt_visit) or 0
            stmt_active = select(func.count(Student.id)).where(Student.expire_date >= now)
            active_count = await session.scalar(stmt_active) or 0
            logger.info(f"📈 Статистика: Посещений сегодня: {visits_count} | Активных абонементов: {active_count}")
            return visits_count, active_count
    except Exception as e:
        logger.error(f"❌ Ошибка при сборе дневной статистики: {e}")
        return 0, 0


async def get_all_users_count():
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(func.count(User.user_id))
            result = await session.execute(stmt)
            return result.scalar() or 0
    except Exception as e:
        logger.error(f" Ошибка счета юзеров: {e}")
        return 0


async def get_active_subs_count():
    try:
        async with AsyncSessionLocal() as session:
            now = datetime.now()
            stmt = select(func.count(Student.id)).where(Student.expire_date > now)
            result = await session.execute(stmt)
            return result.scalar() or 0
    except Exception as e:
        logger.error(f" Ошибка счета активных подписок: {e}")
        return 0


async def create_db_backup():
    backup_path = f"backup_{datetime.now().strftime('%Y-%m-%d')}.sql"
    command = f"pg_dump -h db -p 5432 -U postgres crm_db > {backup_path}"
    process = await asyncio.create_subprocess_shell(
        command,
        env={"PGPASSWORD": "lordwolndemort0195"}  # Чтобы не спрашивал пароль
    )
    await process.wait()
    return backup_path


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session