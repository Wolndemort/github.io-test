from typing import Optional, List
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column, relationship
from sqlalchemy import BigInteger, DateTime, String, func, Integer, ForeignKey
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import db_file
from datetime import datetime
from loguru import logger
import asyncio
from datetime import timedelta
from sqlalchemy import select
from database.constants import DEFAULT_CLUB_SETTINGS


engine = create_async_engine(db_file, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


class User(Base):
    __tablename__ = 'users'
    club: Mapped["Club"] = relationship(back_populates="users")
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    club_id: Mapped[Optional[int]] = mapped_column(ForeignKey('clubs.id'), nullable=True, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String)
    students: Mapped[List["Student"]] = relationship(back_populates='parent')


class Student(Base):
    __tablename__ = 'students'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_id: Mapped[Optional[int]] = mapped_column(ForeignKey('clubs.id'), index=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey('users.user_id'), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String)
    expire_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    can_freeze: Mapped[int] = mapped_column(Integer, default=1)
    is_frozen: Mapped[int] = mapped_column(Integer, default=0)
    balance_lessons: Mapped[int] = mapped_column(default=0)
    last_visit: Mapped[Optional[datetime]] = mapped_column(DateTime)
    parent: Mapped["User"] = relationship(back_populates="students")
    parent_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class Club(Base):
    __tablename__ = 'clubs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    bot_token: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True)
    club_settings: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB()), server_default='{}')
    users: Mapped[List['User']] = relationship(back_populates='club')
    owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    subscription_expire_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


async def init_db():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            logger.success("✅ База данных успешно инициализирована и синхронизирована")
    except Exception as e:
        logger.critical(f"❌ Ошибка при инициализации базы данных: {e}")
        raise e


async def register_new_club(
        name: str,
        bot_token: str,
        owner_id: int,
        session: AsyncSession
):
    """
    Создает новый клуб с дефолтными настройками.
    """
    try:
        new_club = Club(
            name=name,
            bot_token=bot_token,
            owner_id=owner_id,
            club_settings=DEFAULT_CLUB_SETTINGS.copy(),  # Копируем эталон
            is_active=True
        )
        session.add(new_club)
        await session.commit()
        await session.refresh(new_club)

        logger.success(f"🏢 Клуб '{name}' успешно создан! ID: {new_club.id}")
        return new_club

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка при регистрации клуба {name}: {e}")
        return None


async def get_student_list(parent_id: int, club_id: int, session: AsyncSession):
    """
    Получает список учеников конкретного родителя ТОЛЬКО в рамках текущего клуба.
    """
    try:
        # 🛡️ Добавляем фильтр по club_id, чтобы не смешивать данные разных клубов
        stmt = select(Student).where(
            Student.parent_id == parent_id,
            Student.club_id == club_id  # <--- КРИТИЧНО ДЛЯ SaaS
        )
        result = await session.execute(stmt)
        students = result.scalars().all()

        logger.debug(f"Клуб {club_id}: У родителя {parent_id} найдено {len(students)} учеников.")
        return students

    except Exception as e:
        logger.error(f"❌ Ошибка получения списка учеников (Parent {parent_id}, Club {club_id}): {e}")
        return []


async def get_all_subscriptions(user_id: int, club_id: int, session: AsyncSession):
    """
    Получает всех студентов конкретного родителя ТОЛЬКО в рамках текущего клуба.
    """
    try:
        # 🛡️ Добавляем обязательный фильтр по club_id
        stmt = select(Student).where(
            Student.parent_id == user_id,
            Student.club_id == club_id  # <--- Ключевой момент для SaaS
        )
        result = await session.execute(stmt)
        students = result.scalars().all()

        logger.debug(f"Клуб {club_id}: У юзера {user_id} найдено {len(students)} подписок.")
        return students

    except Exception as e:
        logger.error(f"❌ Ошибка получения подписок для {user_id} в клубе {club_id}: {e}")
        return []


async def get_expire_students_grouped(session_pool):
    """
    Достает студентов, у которых кончается абонемент,
    и сразу подтягивает данные их Клуба (чтобы знать, с какого токена писать)
    """
    today = datetime.now()
    three_days_limit = today + timedelta(days=3)

    try:
        async with session_pool() as session:
            # Делаем JOIN со связанной таблицей Club, чтобы сразу получить токен
            stmt = (
                select(Student, Club.bot_token)
                .join(Club, Student.club_id == Club.id)
                .where(
                    Student.expire_date <= three_days_limit,
                    Student.expire_date >= today,
                    Club.subscription_expire_at == True  # Пишем только от активных клубов
                )
            )
            result = await session.execute(stmt)
            # Получаем список кортежей: (объект_студента, токен_бота)
            rows = result.all()

            if rows:
                logger.info(f"✅ Найдено {len(rows)} атлетов для рассылки в разных клубах")
            return rows

    except Exception as e:
        logger.error(f"❌ Ошибка при запросе истекающих абонементов для SaaS: {e}")
        return []


async def process_student_freeze(
        student_id: int,
        club_id: int,  # <--- ID клуба из Middleware
        club_settings: dict,  # <--- Настройки из Middleware
        session: AsyncSession,
        days: int
):
    try:
        # 🛡️ Загружаем студента и сразу проверяем принадлежность к клубу
        student = await session.get(Student, student_id)

        if not student or student.club_id != club_id:
            logger.warning(f"❌ Попытка заморозки чужого студента! ID: {student_id}, Club: {club_id}")
            return None

        # ⚙️ Настройки из конфига клуба
        # Если в конфиге нет freeze_days_step, берем дефолт 5 дней
        freeze_days = club_settings.get("limits", {}).get("freeze_days_step", 5)
        can_freeze_global = club_settings.get("features", {}).get("freeze", True)

        # Проверка: разрешена ли заморозка в этом клубе и есть ли попытки у студента
        if not can_freeze_global:
            logger.info(f"🚫 В клубе {club_id} заморозка отключена в настройках")
            return "disabled"  # Специальный статус для хендлера

        if student and student.can_freeze > 0 and student.expire_date:
            # Применяем срок из настроек клуба
            student.expire_date += timedelta(days=freeze_days)
            student.last_visit = datetime.now()
            student.can_freeze = 0
            student.is_frozen = 1

            # session.add(student) — не обязательно, SQLAlchemy уже "следит" за объектом
            await session.commit()

            logger.success(f"❄️ Клуб {club_id}: Студент {student.name} заморожен на {freeze_days} дней")
            return student.expire_date

        return None

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка при заморозке (Student {student_id}, Club {club_id}): {e}")
        return None


async def has_subscription(user_id: int, club_id: int, session: AsyncSession):
    """
    Проверяет наличие подписки у пользователя В КОНКРЕТНОМ КЛУБЕ.
    """
    try:
        # 🛡️ ФИЛЬТР: Ищем только тех студентов, которые привязаны к этому клубу
        stmt = select(Student).where(
            Student.parent_id == user_id,
            Student.club_id == club_id  # <--- Обязательно!
        )
        result = await session.execute(stmt)
        students = result.scalars().all()

        if not students:
            logger.debug(f"Клуб {club_id}: У юзера {user_id} нет записей.")
            return None, None

        now = datetime.now()
        # Фильтруем активных
        active_students = [s for s in students if s.expire_date and s.expire_date > now]

        if active_students:
            latest_expire = max(s.expire_date for s in active_students)
            logger.debug(f"Клуб {club_id}: У {user_id} активен до {latest_expire}")
            return True, latest_expire

        # Если все просрочены
        latest_expired = max((s.expire_date for s in students if s.expire_date), default=None)
        logger.debug(f"Клуб {club_id}: У {user_id} все просрочено.")
        return False, latest_expired

    except Exception as e:
        logger.error(f"❌ Ошибка проверки подписки (User {user_id}, Club {club_id}): {e}")
        return None, None


async def add_abon(
        student_id: int,
        lessons_count: int,
        session: AsyncSession,
        club_id: int,
        club_settings: dict
):
    """
    Универсальная функция зачисления абонемента (SaaS).
    Работает и для онлайн-чеков, и для налички.
    """
    try:
        # 1. Загружаем студента и проверяем принадлежность к клубу (Security Check)
        student = await session.get(Student, student_id)

        if not student or student.club_id != club_id:
            logger.warning(f"⚠️ [Клуб {club_id}] Попытка доступа к чужому студенту ID: {student_id}")
            return None

        now = datetime.now()

        # 2. Расчет даты продления
        # Берем срок из конфига клуба или 30 дней по дефолту
        days_to_add = club_settings.get("limits", {}).get("subscription_days", 30)

        # Если абонемент еще активен — плюсуем к дате окончания. Если просрочен — отсчет от сегодня.
        current_expire = student.expire_date
        start_date = current_expire if (current_expire and current_expire > now) else now

        new_expire = start_date + timedelta(days=days_to_add)
        student.expire_date = new_expire

        # 3. Логика занятий (Складываем с остатком, если не безлимит)
        if lessons_count == 0:
            # Режим безлимита (ставим 999 или другое спец. число)
            student.balance_lessons = 999
        else:
            # Плюсуем новые занятия к тем, что уже были (чтобы не затереть остаток)
            current_balance = student.balance_lessons or 0
            student.balance_lessons = current_balance + lessons_count

        # 4. Сброс флагов заморозки
        # Если оплатил — значит пришел тренироваться, снимаем заморозку
        student.is_frozen = 0

        # Даем ли право на заморозку в новом периоде? (смотрим настройки клуба)
        can_freeze_global = club_settings.get("features", {}).get("freeze", True)
        student.can_freeze = 1 if can_freeze_global else 0

        # 5. Сохранение
        await session.commit()
        # Refresh не нужен, так как мы только обновляем поля

        logger.success(
            f"✅ [Клуб {club_id}] Продлен: {student.name} | До: {new_expire.strftime('%d.%m.%Y')} | Занятий: {student.balance_lessons}")

        return new_expire.strftime('%d.%m.%Y'), student.parent_id

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ [Клуб {club_id}] Ошибка в add_abon для Student {student_id}: {e}")
        return None


async def get_daily_stats(club_id: int, session: AsyncSession):
    """Сбор статистики посещений и активных карт конкретного клуба за сегодня"""
    now = datetime.now()
    # Начало текущего дня (00:00:00)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        logger.debug(f"📊 Клуб {club_id}: Сбор статистики за {today_start.strftime('%d.%m.%Y')}")

        # 1. Считаем посещения ТОЛЬКО для этого клуба
        stmt_visit = (
            select(func.count(Student.id))
            .where(
                Student.club_id == club_id,         # Фильтр по клубу
                Student.last_visit >= today_start   # Фильтр по дате
            )
        )
        visits_count = await session.scalar(stmt_visit) or 0

        # 2. Считаем активные абонементы ТОЛЬКО для этого клуба
        stmt_active = (
            select(func.count(Student.id))
            .where(
                Student.club_id == club_id,         # Фильтр по клубу
                Student.expire_date >= now          # Фильтр по сроку
            )
        )
        active_count = await session.scalar(stmt_active) or 0

        logger.info(f"📈 Клуб {club_id} | Посещений сегодня: {visits_count} | Активных: {active_count}")
        return visits_count, active_count

    except Exception as e:
        logger.error(f"❌ Ошибка при сборе дневной статистики для клуба {club_id}: {e}")
        return 0, 0


async def get_all_users_count(club_id: int, session: AsyncSession):
    """Считает пользователей только конкретного клуба"""
    try:
        # Считаем только тех, кто привязан к этому club_id
        stmt = select(func.count(User.user_id)).where(User.club_id == club_id)
        result = await session.execute(stmt)
        return result.scalar() or 0
    except Exception as e:
        logger.error(f"❌ Ошибка счета юзеров для клуба {club_id}: {e}")
        return 0


async def get_active_subs_count(club_id: int, session: AsyncSession):
    """Считает активные абонементы только конкретного клуба"""
    try:
        now = datetime.now()
        # Фильтруем и по дате, и по club_id
        stmt = (
            select(func.count(Student.id))
            .where(
                Student.expire_date > now,
                Student.club_id == club_id  # <--- Ключевой фильтр
            )
        )
        result = await session.execute(stmt)
        return result.scalar() or 0
    except Exception as e:
        logger.error(f"❌ Ошибка счета активных подписок для клуба {club_id}: {e}")
        return 0


async def create_db_backup():
    backup_path = f"backup_{datetime.now().strftime('%Y-%m-%d')}.sql"
    command = f"pg_dump -h db -p 5432 -U postgres crm_db > {backup_path}"
    process = await asyncio.create_subprocess_shell(
        command,
        env={"PGPASSWORD": "lordwolndemort0195"}
    )
    await process.wait()
    return backup_path
