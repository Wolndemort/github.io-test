from typing import Optional
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column, sessionmaker
from sqlalchemy import BigInteger, DateTime, String, func, create_engine, select
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
    expire_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    can_freeze: Mapped[int] = mapped_column(default=1)
    is_frozen: Mapped[int] = mapped_column(default=0)
    balance_lessons: Mapped[int] = mapped_column(default=0)
    last_visit: Mapped[Optional[datetime]] = mapped_column(DateTime)


def init_db():
    try:
        Base.metadata.create_all(engine)
        print('База готова')
        logger.success("✅ База данных успешно инициализирована и синхронизирована")
    except Exception as e:
        logger.critical(f"❌ Ошибка при инициализации базы данных: {e}")
        raise e


def get_expire_users():
    today = datetime.now()
    three_days_limit = today + timedelta(days=3)
    logger.debug(f"Поиск абонементов, истекающих с {today.strftime('%d.%m')} по {three_days_limit.strftime('%d.%m')}")
    try:
        with Session() as session:
            stmt = select(User).where(
                User.expire_date <= three_days_limit,
                User.expire_date >= today
        )
            users = session.scalars(stmt).all()
            if users:
                logger.info(f"🔎 Найдено {len(users)} пользователей для уведомления о продлении")
            else:
                logger.debug("Рассылка пуста: подходящих пользователей не найдено")
            return users
    except Exception as e:
        logger.error(f"❌ Ошибка при запросе истекающих абонементов в БД: {e}")
        return []


def has_subscription(user_id: int):
    try:
        with Session() as session:
            user = session.get(User, user_id)
            if not user:
                logger.debug(f"Проверка подписки: Пользователь {user_id} не найден в БД")
                return None, None
            if not user.expire_date:
                logger.debug(f"Проверка подписки: У пользователя {user_id} отсутствует дата (нет абонемента)")
                return False, None

            is_active = user.expire_date > datetime.now()
            status = "АКТИВЕН" if is_active else "ИСТЕК"
            logger.debug(f"Проверка подписки: ID {user_id} | Статус: {status} | До: {user.expire_date.strftime('%d.%m.%Y %H:%M')}")
            return is_active, user.expire_date
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке подписки для ID {user_id}: {e}")
        return None, None


def add_abon(user_id: int, full_name: str = None):
    try:
        with Session() as session:
            user = session.get(User, user_id)
            now = datetime.now()
            is_new_user = False
            if not user:
                logger.info(f"🆕 Регистрация нового пользователя: ID {user_id} ({full_name or 'Атлет'})")
                user = User(user_id=user_id, full_name=full_name)
                session.add(user)
                current_expire_val = None
                is_new_user = True
            else:
                if full_name:
                    user.full_name = full_name
                current_expire_val: datetime | None = user.expire_date
                logger.info(f"🔄 Продление абонемента для ID {user_id}. Текущая дата: {current_expire_val}")
            if current_expire_val:
                start_date = max(now, current_expire_val)
                logger.debug(f"Абонемент еще активен, добавляем 30 дней к {start_date}")
            else:
                start_date = now
                logger.debug(f"Абонемент истек или новый, отсчет от сегодня: {start_date}")
            new_expire = start_date + timedelta(days=30)
            user.expire_date = new_expire
            user.can_freeze = 1
            user.is_frozen = 0

            session.commit()
            log_msg = "Создан" if is_new_user else "Продлен"
            logger.success(f"✅ {log_msg} абонемент для {user_id} до {new_expire.strftime('%d.%m.%Y %H:%M')}")
            return new_expire.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении абонемента для ID {user_id}: {e}")
        return None


def get_all_users_count():
    try:
        with Session() as session:
            count = session.scalar(select(func.count(User.user_id))) or 0
            logger.debug(f"📊 Запрос общего кол-ва пользователей в БД. Результат: {count}")
            return count
    except Exception as e:
        logger.error(f"❌ Ошибка при подсчете всех пользователей: {e}")
        return 0


def get_active_subs_count():
    now = datetime.now()
    try:
        with Session() as session:
            stmt = select(func.count(User.user_id)).where(
                User.expire_date.is_not(None),
                User.expire_date >= now
            )
            count = session.scalar(stmt) or 0
            logger.debug(f"📊 Запрос активных абонементов. Результат: {count}")
            return count
    except Exception as e:
        logger.error(f"❌ Ошибка при подсчете активных абонементов: {e}")
        return 0


def get_daily_stats():
    now = datetime.now()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        with Session() as session:
            logger.debug(f"📊 Сбор дневной статистики за {today_start.strftime('%d.%m.%Y')}")
            #посещения
            stmt_visit = select(func.count(User.user_id)).where(User.last_visit >= today_start)
            visits_count = session.scalar(stmt_visit) or 0

            # Абонементы
            stmt_active = select(func.count(User.user_id)).where(User.expire_date >= now)
            active_count = session.scalar(stmt_active) or 0

            logger.info(f"📈 Статистика собрана: Посещений: {visits_count} | Активных: {active_count}")
            return visits_count, active_count
    except Exception as e:
        logger.error(f"❌ Ошибка при сборе дневной статистики: {e}")
        return 0, 0






