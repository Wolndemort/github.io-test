from fastapi import FastAPI
from sqladmin import Admin, ModelView
from database.db import engine, Student, User


class UserAdmin(ModelView,model=User):
    column_list = [User.user_id, User.full_name]
    column_searchable_list = [User.full_name]
    name = "Родитель"
    name_plural = "Родители"


class StudentAdmin(ModelView, model=Student):
    column_list = [Student.name, Student.expire_date, Student.balance_lessons]
    column_filters = [Student.is_frozen]
    name = "Ученик"
    name_plural = "Атлеты"


def setup_admin(app, engine):
    admin = Admin(app, engine)
    admin.add_view(UserAdmin)
    admin.add_view(StudentAdmin)
