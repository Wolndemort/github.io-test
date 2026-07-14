from fastapi import Request
from sqladmin import Admin, ModelView, BaseView, expose
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.responses import RedirectResponse
from database.db import Student, User


class UserAdmin(ModelView, model=User):
    column_list = [User.user_id, User.full_name]
    column_searchable_list = [User.full_name]
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
        "is_frozen"
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
        base_url="/master-dashboard"
    )
    admin.add_view(UserAdmin)
    admin.add_view(StudentAdmin)
    admin.add_view(AnalyticsAdmin)
