from fastapi import Request
from sqladmin import Admin, ModelView, BaseView, expose
from starlette.responses import RedirectResponse
from database.db import Student, User


class UserAdmin(ModelView, model=User):
    # Явно передаем свойства модели вместо строк, чтобы избежать багов WTForms
    column_list = [User.user_id, User.full_name]
    column_searchable_list = [User.full_name]
    name = "Родитель"
    name_plural = "Родители"
    page_size = 10

    # ВКЛЮЧАЕМ УПРАВЛЕНИЕ: Разрешаем удаление и редактирование родителей
    can_delete = True
    can_edit = True


class StudentAdmin(ModelView, model=Student):
    # ФИКС ОШИБКИ LIST: Добавляем также телефон и твою новую колонку birthday
    column_list = [
        Student.id,
        Student.name,
        Student.expire_date,
        Student.balance_lessons,
        Student.parent_phone,
        Student.birthday  # Теперь колонка накатана алембиком, её можно выводить!
    ]
    column_searchable_list = [Student.name, Student.parent_phone]

    name = "Ученик"
    name_plural = "Атлеты"
    page_size = 10

    # ФИКС УДАЛЕНИЯ: Без этих флагов кнопки удаления физически не будет в интерфейсе!
    can_delete = True  # Появится иконка корзины для удаления атлетов!
    can_edit = True  # Разрешаем редактировать баланс занятий и ДР прямо из браузера
    can_create = True  # Разрешаем админу создавать записи через веб, если нужно


class AnalyticsAdmin(BaseView):
    name = "Статистика"
    icon = "fa-solid fa-chart-line"

    @expose("/analytics", methods=["GET"])
    async def analytics_page(self, request: Request):
        return RedirectResponse(url="/revenue")


def setup_admin(app, engine):
    admin = Admin(app, engine, base_url="/master-dashboard")

    admin.add_view(UserAdmin)
    admin.add_view(StudentAdmin)
    admin.add_view(AnalyticsAdmin)
