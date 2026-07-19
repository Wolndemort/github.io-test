import hmac
import os

from fastapi import Request
from sqladmin import Admin, ModelView, BaseView, expose
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.responses import RedirectResponse
from database.db import Club, Student, User


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        expected_user = os.getenv("ADMIN_PANEL_USER")
        expected_password = os.getenv("ADMIN_PANEL_PASSWORD")
        if not expected_user or not expected_password:
            return False
        if hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password):
            request.session["admin_authenticated"] = True
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("admin_authenticated") is True


class UserAdmin(ModelView, model=User):
    column_list = [User.user_id, User.full_name]
    column_searchable_list = [User.full_name]
    form_columns = [
        "user_id",
        "club_id",
        "is_accepted",
        "full_name",
        "is_biometric_enabled",
    ]
    name = "Родитель"
    name_plural = "Родители"
    page_size = 10
    can_delete = True
    can_edit = True
    can_create = True


class StudentAdmin(ModelView, model=Student):
    column_list = [
        Student.id,
        Student.name,
        Student.expire_date,
        Student.balance_lessons,
        Student.parent_phone,
        Student.birthday
    ]
    column_searchable_list = [Student.name, Student.parent_phone]

    # ИСПРАВЛЕНО: Вместо "parent_id" мы пишем отношение "parent".
    # SQLAdmin свяжет это поле с формой AJAX-поиска, описанной ниже.
    # База НЕ упадет в дедлок, так как загрузка будет порционной через AJAX.
    form_columns = [
        "name",
        "club_id",
        "parent",
        "balance_lessons",
        "expire_date",
        "birthday",
        "parent_phone",
        "discipline",
        "can_freeze",
        "is_frozen",
        "frozen_at",
        "last_visit",
    ]

    # Настройка AJAX для связи "parent" теперь работает корректно,
    # так как поле "parent" явно присутствует в списке form_columns.
    form_ajax_refs = {
        "parent": {
            "fields": ["full_name"],
            "placeholder": "Выберите родителя",
        }
    }

    name = "Ученик"
    name_plural = "Атлеты"
    page_size = 10
    can_delete = True
    can_edit = True
    can_create = True


class ClubAdmin(ModelView, model=Club):
    """Полное управление клубом и его JSON-конфигурацией из master-dashboard."""

    column_list = [
        Club.id,
        Club.name,
        Club.owner_id,
        Club.subscription_expire_at,
    ]
    column_searchable_list = [Club.name, Club.bot_token]
    form_columns = [
        "name",
        "bot_token",
        "owner_id",
        "subscription_expire_at",
        "club_settings",
    ]
    name = "Клуб"
    name_plural = "Клубы"
    page_size = 10
    can_delete = True
    can_edit = True
    can_create = True


class AnalyticsAdmin(BaseView):
    name = "Статистика"
    icon = "fa-solid fa-chart-line"

    @expose("/analytics", methods=["GET"])
    async def analytics_page(self, request: Request):
        return RedirectResponse(url="/revenue")


def setup_admin(app, engine):
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = Admin(
        app=app,
        engine=engine,
        session_maker=async_session_factory,
        base_url="/master-dashboard",
        authentication_backend=AdminAuth(secret_key=os.getenv("SECRET_KEY", "")),
    )
    admin.add_view(UserAdmin)
    admin.add_view(StudentAdmin)
    admin.add_view(ClubAdmin)
    admin.add_view(AnalyticsAdmin)
