from typing import Optional, List
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column, sessionmaker, relationship
from sqlalchemy import BigInteger, DateTime, String, func, create_engine, select, Integer, ForeignKey
from config import db_file
from datetime import datetime, timedelta
from loguru import logger


engine = create_engine(db_file, echo=False)
Session = sessionmaker(bind=engine)


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


def init_db():
    try:
        Base.metadata.create_all(engine)
        print('База готова')
        logger.success("✅ База данных успешно инициализирована и синхронизирована")
    except Exception as e:
        logger.critical(f"❌ Ошибка при инициализации базы данных: {e}")
        raise e


def get_student_list(parent_id: int):
    with Session() as session:
        return session.query(Student).filter(Student.parent_id == parent_id).all()


def get_all_subscriptions(user_id: int):
    try:
        with Session() as session:
            return session.query(Student).filter(Student.parent_id == user_id).all()
    except Exception as e:
        logger.error(f"❌ Ошибка получения подписок для {user_id}: {e}")
        return []


def get_expire_students():
    today = datetime.now()
    three_days_limit = today + timedelta(days=3)
    logger.debug(f"🔎 Поиск атлетов, истекающих с {today.strftime('%d.%m')} по {three_days_limit.strftime('%d.%m')}")
    try:
        with Session() as session:
            stmt = select(Student).where(
                Student.expire_date <= three_days_limit,
                Student.expire_date >= today
            )
            students = session.scalars(stmt).all()
            if students:
                logger.info(f"✅ Найдено {len(students)} атлетов для уведомления родителей")
            else:
                logger.debug("Рассылка пуста: истекающих абонементов не найдено")
            return students
    except Exception as e:
        logger.error(f"❌ Ошибка при запросе истекающих абонементов в БД: {e}")
        return []


def has_subscription(user_id: int):
    try:
        with Session() as session:
            stmt = select(Student).where(Student.parent_id == user_id)
            students = session.scalars(stmt).all()

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


def add_abon(student_id: int):
    try:
        with Session() as session:
            student = session.get(Student, student_id)
            if not student:
                logger.error(f"❌ Студент с ID {student_id} не найден в базе")
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
            session.commit()
            logger.success(f"✅ Продлен абонемент для {student.name} до {new_expire.strftime('%d.%m.%Y %H:%M')}")
            return new_expire.strftime('%d.%m.%Y'), student.parent_id
    except Exception as e:
        logger.error(f"❌ Ошибка при продлении для студента {student_id}: {e}")
        return None


def get_daily_stats():
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        with Session() as session:
            logger.debug(f"📊 Сбор статистики по СТУДЕНТАМ за {today_start.strftime('%d.%m.%Y')}")
            stmt_visit = select(func.count(Student.id)).where(Student.last_visit >= today_start)
            visits_count = session.scalar(stmt_visit) or 0
            stmt_active = select(func.count(Student.id)).where(Student.expire_date >= now)
            active_count = session.scalar(stmt_active) or 0
            logger.info(f"📈 Статистика: Посещений сегодня: {visits_count} | Активных абонементов: {active_count}")
            return visits_count, active_count

    except Exception as e:
        logger.error(f"❌ Ошибка при сборе дневной статистики: {e}")
        return 0, 0


def get_all_users_count():
    try:
        with Session() as session:
            # Считаем всех уникальных родителей в таблице User
            return session.query(func.count(User.user_id)).scalar() or 0
    except Exception as e:
        logger.error(f" Ошибка счета юзеров: {e}")
        return 0


def get_active_subs_count():
    try:
        with Session() as session:
            now = datetime.now()
            # Считаем АТЛЕТОВ, у которых дата окончания в будущем
            return session.query(func.count(Student.id)).filter(Student.expire_date > now).scalar() or 0
    except Exception as e:
        logger.error(f" Ошибка счета активных подписок: {e}")
        return 0
