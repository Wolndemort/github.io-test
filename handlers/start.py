from aiogram import Router, types, F
from aiogram.filters import Command
from handlers.buttons import get_main_menu_keyboard
import sqlite3
from config import db_file

router = Router()  # Создаем локальный роутер для этого файла


@router.message(Command("start"))
async def start_handler(message: types.Message):
    user_name = message.from_user.first_name
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
        INSERT INTO users (user_id , full_name)
        VALUES(?, ?)
        ON CONFLICT(user_id) DO UPDATE SET full_name = excluded.full_name
        """, (user_id, full_name))
        conn.commit()
    await message.answer(
        f"<b>Здравствуйте, {user_name}! Какой вопрос?</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard()

    )
