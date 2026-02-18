from typing import Optional
from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column, sessionmaker
from sqlalchemy import BigInteger, DateTime, String, func, create_engine, select
from config import db_file
from datetime import datetime, timedelta


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
    Base.metadata.create_all(engine)
    print('База готова')


def get_expire_users():
    today = datetime.now()
    # Считаем на 3 дня вперед
    three_days_limit = today + timedelta(days=3)

    with Session() as session:
        # Выбираем ВЕСЬ объект User, чтобы IDE видела его поля
        stmt = select(User).where(
            User.expire_date <= three_days_limit,
            User.expire_date >= today
        )
        # .scalars() превращает результат в список объектов User
        return session.scalars(stmt).all()


def has_subscription(user_id: int):
    with Session() as session:
        user = session.get(User, user_id)
        if user and user.expire_date:
            if user.expire_date > datetime.now():
                return True, user.expire_date
            return False, user.expire_date
        return None, None


def add_abon(user_id: int, full_name: str = None):
    with Session() as session:
        user = session.get(User, user_id)
        now = datetime.now()
        if not user:
            user = User(user_id=user_id, full_name=full_name)
            session.add(user)
            current_expire_val = None
        else:
            if full_name:
                user.full_name = full_name
            current_expire_val: datetime | None = user.expire_date
        if current_expire_val:
            start_date = max(now, current_expire_val)
        else:
            start_date = now

        new_expire = start_date + timedelta(days=30)
        user.expire_date = new_expire
        user.can_freeze = 1
        user.is_frozen = 0

        session.commit()
        return new_expire.strftime('%Y-%m-%d %H:%M:%S')


def get_all_users_count():
    with Session() as session:
        return session.scalar(
            select(func.count(User.user_id))
        )


def get_active_subs_count():
    with Session() as session:
        return session.scalar(
            select(func.count(User.user_id)).where(User.expire_date > datetime.now()))


def get_daily_stats():
    with Session() as session:
        #посещения
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt_visit = select(func.count(User.user_id)).where(User.last_visit >= today_start)
        visits_count = session.scalar(stmt_visit)
        # абонементы
        stmt_active = select(func.count(User.user_id)).where(User.expire_date >= datetime.now())
        active_count = session.scalar(stmt_active)

        return visits_count, active_count







