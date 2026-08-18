import os
from typing import Optional, List
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column, relationship
from sqlalchemy import BigInteger, DateTime, String, func, Integer, ForeignKey, Boolean, or_, and_, UniqueConstraint
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import db_file
from datetime import datetime, date, timezone
from sqlalchemy import Date
from loguru import logger
import asyncio
import gzip
import io
from datetime import timedelta
from sqlalchemy import select
from database.constants import DEFAULT_CLUB_SETTINGS
from pathlib import Path


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
    is_accepted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    full_name: Mapped[Optional[str]] = mapped_column(String)
    students: Mapped[List["Student"]] = relationship(back_populates='parent', cascade="all, delete-orphan")
    is_biometric_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    def __str__(self) -> str:
        """Readable label for SQLAdmin relationship/AJAX selectors."""
        return self.full_name or str(self.user_id)

class Discount(Base):
    __tablename__ = "discounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10))
    value: Mapped[float] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    scope: Mapped[str] = mapped_column(String(20), default="subscriptions")
    comment: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    starts_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    ends_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()")

class DiscountAssignment(Base):
    __tablename__ = "discount_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id", ondelete="CASCADE"), index=True)
    discount_id: Mapped[int] = mapped_column(ForeignKey("discounts.id", ondelete="RESTRICT"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True, index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()")
    __table_args__ = (UniqueConstraint("club_id", "discount_id", "user_id", name="uq_discount_assignment"),)


class Student(Base):
    __tablename__ = 'students'
    __table_args__ = (
        UniqueConstraint("club_id", "name", "birthday", "discipline", "parent_phone", name="uq_students_club_name_bday_disc_phone"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_id: Mapped[Optional[int]] = mapped_column(ForeignKey('clubs.id'), index=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey('users.user_id'), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String)
    comment: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    expire_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    can_freeze: Mapped[int] = mapped_column(Integer, default=1)
    is_frozen: Mapped[int] = mapped_column(Integer, default=0)
    frozen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Фактическая длительность текущей заморозки. Нужна для платных пакетов,
    # которые могут отличаться от стандартного шага клуба.
    frozen_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    balance_lessons: Mapped[int] = mapped_column(default=0)
    birthday: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_visit: Mapped[Optional[datetime]] = mapped_column(DateTime)
    parent: Mapped["User"] = relationship(back_populates="students")
    parent_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    parent_phone_secondary: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    discipline: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="boxing")
    parents: Mapped[List["StudentParent"]] = relationship(back_populates="student", cascade="all, delete-orphan")


class StudentParent(Base):
    __tablename__ = "student_parents"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    student: Mapped["Student"] = relationship(back_populates="parents")
    parent: Mapped["User"] = relationship()


async def get_student_parent_ids(student_id: int, session: AsyncSession) -> list[int]:
    """Return legacy and additional parent IDs without duplicating notifications."""
    result = await session.execute(select(Student.parent_id).where(Student.id == student_id))
    primary = result.scalar_one_or_none()
    extra = await session.execute(select(StudentParent.parent_id).where(StudentParent.student_id == student_id))
    return list(dict.fromkeys(([int(primary)] if primary else []) + [int(x) for x in extra.scalars().all()]))


class Club(Base):
    __tablename__ = 'clubs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    bot_token: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True)
    club_settings: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSONB()), server_default='{}')
    users: Mapped[List['User']] = relationship(back_populates='club')
    owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    subscription_expire_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    saas_rebill_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    saas_auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)


class SaaSPaymentOrder(Base):
    """Payments for the platform license itself, isolated from club sales."""
    __tablename__ = "saas_payment_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="NEW", index=True)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True, index=True)
    payment_method_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()")
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ClubStaff(Base):
    """Staff accounts are separate from the club owner and never inherit owner rights."""
    __tablename__ = "club_staff"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id", ondelete="CASCADE"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="cashier")
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()")


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Кто платит (Родитель)
    user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey('users.user_id', ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # ИСПРАВЛЕНО: Сделано Optional и добавлен ondelete="SET NULL"
    # Если ученика удалят, подписка останется в базе, но поле student_id станет пустым
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('students.id', ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # В какой клуб капают деньги
    club_id: Mapped[int] = mapped_column(ForeignKey('clubs.id'), index=True)

    # Тот самый токен привязанной карты от Т-Банка для автосписаний
    rebill_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Сумма регулярного списания в копейках (например, 350000 для 3500 руб)
    amount_kopecks: Mapped[int] = mapped_column(Integer)

    # Активна ли подписка прямо сейчас
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # Дата и время следующего автоматического списания
    next_charge_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentOrder(Base):
    __tablename__ = 'payment_orders'

    # Уникальный ID платежа (будем генерировать UUID строку)
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey('users.user_id', ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    # ИСПРАВЛЕНО: Сделано Optional и добавлен ondelete="SET NULL"
    # Ордер на оплату не удалится (бухгалтерия сходится), но ссылка на ученика очистится
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('students.id', ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    club_id: Mapped[int] = mapped_column(ForeignKey('clubs.id'), index=True)
    discipline: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    # Статусы банка: NEW, CONFIRMED, REJECTED и т.д.
    status: Mapped[str] = mapped_column(String(20), default="NEW")
    # Тип платежа: "FIRST" (первый платеж с привязкой) или "RECURRENT" (автосписание по крону)
    type: Mapped[str] = mapped_column(String(20))
    # ID платежа ЮKassa для аудита и защиты от повторного зачисления
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True, index=True)

    lesson_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    days_to_add: Mapped[int] = mapped_column(Integer, default=30, server_default="30")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()")


class ClubProduct(Base):
    """Каталог клуба для будущей корзины: мерч, напитки и услуги."""
    __tablename__ = "club_products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(30), default="other")
    price_kopecks: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CartOrder(Base):
    __tablename__ = "cart_orders"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True)
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    original_amount_kopecks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    discount_amount_kopecks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    original_amount_kopecks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    discount_kind: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    discount_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_amount_kopecks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="NEW", index=True)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CartItem(Base):
    __tablename__ = "cart_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cart_order_id: Mapped[str] = mapped_column(ForeignKey("cart_orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("club_products.id", ondelete="SET NULL"), nullable=True)
    item_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(160))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_kopecks: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class CashEntry(Base):
    """Manual cash-register movement; sales remain recorded in their source orders."""
    __tablename__ = "cash_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id", ondelete="CASCADE"), index=True)
    entry_type: Mapped[str] = mapped_column(String(16))  # income / expense
    category: Mapped[str] = mapped_column(String(50), default="other")
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(500), default="")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, unique=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()", index=True)
    reversed_entry_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class AuditEntry(Base):
    """Structured audit trail for admin, staff and payment actions."""
    __tablename__ = "audit_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    club_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clubs.id", ondelete="CASCADE"), index=True, nullable=True)
    event: Mapped[str] = mapped_column(String(80), index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    object_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    object_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    location: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    amount_kopecks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    method: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()", index=True)


class VisitLog(Base):
    __tablename__ = 'visit_logs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey('students.id', ondelete="CASCADE"),
        index=True
    )
    club_id: Mapped[int] = mapped_column(ForeignKey('clubs.id'), index=True)
    visited_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default="now()")
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)



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
            subscription_expire_at=datetime.now() + timedelta(days=30)
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


async def get_expire_students_grouped(session):
    """
    Достает студентов, у которых скоро кончается абонемент
    или осталось мало занятий,
    и сразу подтягивает данные их Клуба (чтобы знать, с какого токена писать)
    """
    from services.analytics import reporting_periods

    today = reporting_periods()["now"]
    three_days_limit = today + timedelta(days=3)

    try:
        stmt = (
            select(Student, Club.bot_token)
            .join(Club, Student.club_id == Club.id)
            .where(
                Student.parent_id.is_not(None),
                Student.expire_date >= today,
                or_(Student.is_frozen == 0, Student.is_frozen.is_(None)),
                Club.subscription_expire_at >= today,
                or_(
                    and_(Student.expire_date <= three_days_limit, Student.expire_date >= today),
                    and_(Student.balance_lessons > 0, Student.balance_lessons <= 2),
                ),
            )
        )
        result = await session.execute(stmt)
        rows = result.all()

        if rows:
            logger.info(f"✅ Найдено {len(rows)} атлетов")
        return rows

    except Exception as e:
        logger.error(f"❌ Ошибка при запросе: {e}")
        return []


async def process_student_freeze(
        student_id: int,
        club_id: int,  # ID клуба из Middleware
        club_settings: dict,  # Настройки из Middleware
        session: AsyncSession,
        days: int  # Переданный из хендлера шаг (например, 7)
):
    try:
        # 1. Загружаем студента и проверяем изоляцию данных (SaaS Security Check)
        # Все каналы зачисления (webhook, WebApp, бот и cash) должны
        # сериализовать изменение даты и баланса одного ученика.
        student = await session.get(Student, student_id, with_for_update=True)

        if not student or student.club_id != club_id:
            logger.warning(f"❌ Попытка заморозки чужого студента! ID: {student_id}, Club: {club_id}")
            return None

        # 2. Проверяем глобальный флаг заморозки в этом клубе
        can_freeze_global = club_settings.get("features", {}).get("freeze", True)
        if not can_freeze_global:
            logger.info(f"🚫 В клубе {club_id} заморозка отключена в настройках")
            return "disabled"

        # ПРАВКА: Работаем строго в наивном формате UTC (без таймзон) для защиты от TypeError
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

        # 3. Проверяем, активен ли абонемент и не заморожен ли он уже прямо сейчас
        expire_naive = student.expire_date.replace(tzinfo=None) if student.expire_date else None
        if not expire_naive or expire_naive < now_naive:
            logger.info(f"🚫 У студента ID {student_id} абонемент уже просрочен")
            return None

        if getattr(student, "is_frozen", 0) == 1:
            logger.info(f"🚫 Студент ID {student_id} уже находится в заморозке")
            return None

        # 4. Проверяем лимит доступных заморозок у самого студента
        if student.can_freeze > 0:
            # Сдвигаем дату окончания на точное количество дней из аргумента тарифа
            if student.expire_date:
                student.expire_date += timedelta(days=days)

            # Списываем 1 право на заморозку и ставим флаг
            student.can_freeze -= 1  # ПРАВКА: Уменьшаем на 1, а не обнуляем в 0
            student.is_frozen = 1
            student.frozen_days = days

            # ❄️ ПРАВКА: Записываем текущую дату начала заморозки в твою новую колонку из Alembic!
            student.frozen_at = now_naive

            # 🚨 ПОДСТРАХОВКА ДЛЯ БЕЗЛИМИТА: Защищаем маркер 999 на балансе
            if student.balance_lessons == 999:
                student.balance_lessons = 999

            await session.commit()

            logger.info(
                f"❄️ Клуб {club_id}: Студент {student.name} заморожен на {days} дней. До: {student.expire_date.strftime('%d.%m.%Y')}"
            )
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
        club_settings: dict,
        days_to_add: int = None,
        discipline: str = None  # 🌟 КРИТИЧЕСКИЙ ФИКС: Добавили аргумент дисциплины
):
    """
    Универсальная функция зачисления абонемента (SaaS).
    Работает и для онлайн-чеков, и для налички.
    """
    try:
        # 1. Загружаем студента и проверяем принадлежность к клубу
        # Serialize date/balance updates across webhook, bot, WebApp and cash.
        student = await session.get(Student, student_id, with_for_update=True)

        if not student or student.club_id != club_id:
            logger.warning(f"⚠️ [Клуб {club_id}] Попытка доступа к чужому студенту ID: {student_id}")
            return None

        # Сбрасываем часы, минуты и микросекунды в 00:00:00
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # 🌟 КРИТИЧЕСКИЙ ФИКС: Если передана новая дисциплина — обновляем её у студента
        if discipline:
            student.discipline = discipline

        # 2. Расчет даты продления
        if days_to_add is None:
            days_to_add = club_settings.get("limits", {}).get("subscription_days", 30)

        current_expire = student.expire_date
        was_frozen = bool(student.is_frozen)

        # Если абонемент еще активен — плюсуем к дате окончания. Если просрочен — отсчет от сегодняшней полночи.
        if current_expire and current_expire > now:
            start_date = current_expire.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_date = now

        new_expire = start_date + timedelta(days=days_to_add)

        # Принудительно выставляем конец дня
        student.expire_date = new_expire.replace(hour=23, minute=59, second=59, microsecond=0)

        # 3. Логика занятий.
        # Для тарифов с ограниченным числом занятий остаток относится
        # только к действующему периоду. Поэтому после окончания срока
        # новый абонемент начинается с его собственного лимита, а не
        # складывается с остатком просроченного абонемента.
        discipline_cfg = club_settings.get("disciplines", {}).get(discipline or student.discipline, {})
        is_lessons_tariff = discipline_cfg.get("type", "lessons") == "lessons"
        if lessons_count == 999:
            student.balance_lessons = 999
        else:
            if is_lessons_tariff and not was_frozen and not (current_expire and current_expire > now):
                student.balance_lessons = lessons_count
            else:
                current_balance = student.balance_lessons or 0
                if current_balance == 999:
                    current_balance = 0
                student.balance_lessons = current_balance + lessons_count

        # 4. Сброс флагов заморозки
        student.is_frozen = 0
        student.frozen_at = None
        student.frozen_days = None

        # Даем ли право на заморозку в новом периоде?
        can_freeze_global = club_settings.get("features", {}).get("freeze", True)
        student.can_freeze = 1 if can_freeze_global else 0

        # 5. Сохранение
        await session.commit()

        logger.info(
            f"✅ [Клуб {club_id}] Продлен: {student.name} | Направление: {student.discipline} | До: {new_expire.strftime('%d.%m.%Y')} | Занятий: {student.balance_lessons}")

        return new_expire.strftime('%d.%m.%Y'), student.parent_id

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ [Клуб {club_id}] Ошибка в add_abon для Student {student_id}: {e}")
        return None


async def get_daily_stats(club_id: int, session: AsyncSession):
    """Сбор статистики посещений и активных карт конкретного клуба за сегодня"""
    from services.analytics import reporting_periods

    periods = reporting_periods()
    now = periods["now"]
    today_start = periods["today"]

    try:
        logger.debug(f"📊 Клуб {club_id}: Сбор статистики за {today_start.strftime('%d.%m.%Y')}")

        # 1. Считаем посещения ТОЛЬКО для этого клуба
        stmt_visit = (
            select(func.count(VisitLog.id))
            .where(
                VisitLog.club_id == club_id,
                VisitLog.visited_at >= today_start
            )
        )
        visits_count = await session.scalar(stmt_visit) or 0

        # 2. Считаем активные абонементы ТОЛЬКО для этого клуба
        stmt_active = (
            select(func.count(Student.id))
            .where(
                Student.club_id == club_id,         # Фильтр по клубу
                Student.expire_date > now,
                Student.balance_lessons > 0,
                func.coalesce(Student.is_frozen, 0) == 0,
            )
        )
        active_count = await session.scalar(stmt_active) or 0

        logger.info(f"📈 Клуб {club_id} | Посещений сегодня: {visits_count} | Активных: {active_count}")
        return visits_count, active_count

    except Exception as e:
        logger.error(f"❌ Ошибка при сборе дневной статистики для клуба {club_id}: {e}")
        return 0, 0


async def get_all_users_count(club_id: int, session: AsyncSession):
    """Counts unique Telegram parents linked to athletes in this club."""
    try:
        stmt = select(func.count(func.distinct(Student.parent_id))).where(
            Student.club_id == club_id,
            Student.parent_id.is_not(None),
        )
        result = await session.execute(stmt)
        return result.scalar() or 0
    except Exception as e:
        logger.error(f"❌ Ошибка счета юзеров для клуба {club_id}: {e}")
        return 0


async def get_total_athletes_count(club_id: int, session: AsyncSession):
    """Counts every athlete card in this club, including unlimited/no-subscription cards."""
    try:
        result = await session.execute(
            select(func.count(Student.id)).where(Student.club_id == club_id)
        )
        return result.scalar() or 0
    except Exception as e:
        logger.error(f"❌ Ошибка счета атлетов для клуба {club_id}: {e}")
        return 0


async def get_active_subs_count(club_id: int, session: AsyncSession):
    """Считает активные абонементы только конкретного клуба"""
    try:
        from services.analytics import reporting_periods

        now = reporting_periods()["now"]
        # Фильтруем и по дате, и по club_id
        stmt = (
            select(func.count(Student.id))
            .where(
                Student.expire_date > now,
                Student.club_id == club_id,
                Student.balance_lessons > 0,
                func.coalesce(Student.is_frozen, 0) == 0,
            )
        )
        result = await session.execute(stmt)
        return result.scalar() or 0
    except Exception as e:
        logger.error(f"❌ Ошибка счета активных подписок для клуба {club_id}: {e}")
        return 0


async def purchase_student_freeze(
        student_id: int,
        club_id: int,
        days: int,
        session: AsyncSession,
):
    """Применяет уже оплаченную заморозку. Отдельно от бесплатного лимита."""
    if days < 1:
        return None
    try:
        student = await session.get(Student, student_id, with_for_update=True)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expire = student.expire_date.replace(tzinfo=None) if student and student.expire_date else None
        if not student or student.club_id != club_id or not expire or expire <= now:
            return None
        if getattr(student, "is_frozen", 0) == 1:
            return None

        student.expire_date = student.expire_date + timedelta(days=days)
        student.is_frozen = 1
        student.frozen_at = now
        student.frozen_days = days
        await session.flush()
        return student.expire_date, student.parent_id
    except Exception:
        await session.rollback()
        logger.exception(f"Ошибка платной заморозки Student {student_id}")
        return None

async def create_db_backup() -> str | None:
    # Добавляем расширение .gz, так как файл будет сжатым архивом
    backup_dir = Path(".")
    backup_name = f"backup_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.sql.gz"
    backup_path = backup_dir / backup_name
    db_password = os.getenv("DB_PASSWORD")
    if not db_password:
        logger.error("❌ DB_PASSWORD не задан, резервная копия отменена")
        return None

    try:
        process = await asyncio.create_subprocess_exec(
            "pg_dump", "-h", "db", "-p", "5432", "-U", "postgres", "crm_db",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PGPASSWORD": db_password},
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"❌ Ошибка внутри pg_dump: {error_msg}")
            # Если файл успел создаться пустым, удаляем его
            if backup_path.exists():
                backup_path.unlink()
            return None

        with gzip.open(backup_path, "wb") as backup_file:
            backup_file.write(stdout)
        logger.info(f"📦 Бэкап базы данных успешно создан: {backup_path}")
        await prune_old_backups(keep_last=14)
        return str(backup_path)

    except Exception as e:
        logger.error(f"❌ Критическая ошибка при создании бэкапа: {e}")
        return None


async def prune_old_backups(keep_last: int = 14) -> int:
    """
    Удаляет старые локальные бэкапы, оставляя только последние keep_last файлов.
    Не трогает успешную текущую копию и не влияет на отправку администратору.
    """
    try:
        backup_files = sorted(
            Path(".").glob("backup_*.sql.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        stale_files = backup_files[keep_last:]
        removed = 0
        for file_path in stale_files:
            try:
                file_path.unlink()
                removed += 1
            except Exception as exc:
                logger.warning(f"⚠️ Не удалось удалить старый бэкап {file_path}: {exc}")
        if removed:
            logger.info(f"🧹 Удалено старых бэкапов: {removed}")
        return removed
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке старых бэкапов: {e}")
        return 0


def validate_backup_archive(backup_path: str | Path) -> bool:
    """
    Безопасная проверка архива перед тестовым восстановлением.
    Проверяет, что файл читается как gzip и похож на SQL-dump PostgreSQL.
    """
    try:
        path = Path(backup_path)
        if not path.exists() or path.stat().st_size == 0:
            return False
        with gzip.open(path, "rb") as fh:
            head = fh.read(256)
        return b"PostgreSQL database dump" in head or head.startswith(b"--")
    except Exception as exc:
        logger.error(f"❌ Ошибка проверки backup-архива {backup_path}: {exc}")
        return False
