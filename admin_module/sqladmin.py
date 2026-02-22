from fastapi import Request
from sqladmin import Admin, ModelView, BaseView, expose
from starlette.responses import RedirectResponse
from database.db import Student, User


class UserAdmin(ModelView, model=User):
    column_list = ["user_id", "full_name"]
    column_searchable_list = ["full_name"]
    name = "Родитель"
    name_plural = "Родители"
    page_size = 10


class StudentAdmin(ModelView, model=Student):
    column_list = ["id", "name", "expire_date", "balance_lessons"]
    name = "Ученик"
    name_plural = "Атлеты"
    page_size = 10


class AnalyticsAdmin(BaseView):
    name = "Статистика"
    icon = "fa-solid fa-chart-line"

    @expose("/analytics", methods=["GET"])
    async def analytics_page(self, request: Request):
        # Просто перенаправляем пользователя на ваш рабочий API-метод
        return RedirectResponse(url="/revenue")


def setup_admin(app, engine):
    admin = Admin(app, engine)
    admin.add_view(UserAdmin)
    admin.add_view(StudentAdmin)
    admin.add_view(AnalyticsAdmin)
