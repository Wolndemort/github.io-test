from aiogram import Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import WebAppInfo, KeyboardButton


router = Router()


def get_profile_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='➕ Добавить атлета', callback_data='add_athlete'))
    builder.row(types.InlineKeyboardButton(text='Проверить статус абонемента', callback_data='check_status_now'))
    builder.row(types.InlineKeyboardButton(text='Купить абонемент 💳', callback_data='choose_section'))
    builder.row(types.InlineKeyboardButton(text='НАЧАЛО ◀️🔙', callback_data='begin'))
    builder.row(types.InlineKeyboardButton(text='Заморозить абонемент', callback_data='freeze_sub'))
    builder.row(types.InlineKeyboardButton(text='📲 МОЙ QR-ПРОПУСК', callback_data='show_qr'))

    return builder.as_markup()


def get_main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text='Персональные тренировки', callback_data='personal_trainig')
    builder.button(text='BJJ', callback_data='bjj')
    builder.button(text='GRAPPLING KIDS', callback_data='kids')
    builder.button(text="Абонементы", web_app=WebAppInfo(url='https://wolndemort.github.io'))
    builder.button(text="Контакты", callback_data='contact')
    builder.button(text='Открыть сайт', url="https://aemaykop.ru")
    builder.row(types.InlineKeyboardButton(text='Проверить статус абонемента', callback_data='check_status_now'))
    builder.row(types.InlineKeyboardButton(text='👤 МОЙ ПРОФИЛЬ / ЗАМОРОЗКА', callback_data='profile'))
    builder.adjust(2)
    return builder.as_markup()


def get_bjj_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='💳 Купить абонемент (5000₽)', callback_data='buy_bjj'))
    builder.row(types.InlineKeyboardButton(text='🔙 Назад к секциям', callback_data='choose_section'))
    return builder.as_markup()


def get_kids_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='💳 Купить за 4000₽', callback_data='buy_kids'))
    builder.row(types.InlineKeyboardButton(text='🔙 Назад к секциям', callback_data='choose_section'))
    return builder.as_markup()


def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='📊 Дневной отчет', callback_data='daily_report'))
    builder.row(types.InlineKeyboardButton(text='📢 Рассылка всем', callback_data='admin_broadcast'))
    builder.row(types.InlineKeyboardButton(text='📥 Выгрузка БД (CSV)', callback_data='export_db'))
    builder.row(types.InlineKeyboardButton(text='🔙 Назад', callback_data='begin'))
    return builder.as_markup()


def get_scanner_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(
        text="📸 ОТКРЫТЬ СКАНЕР (ВХОД)",
        web_app=WebAppInfo(url="https://wolndemort.github.io/github.io-test/scanner.html")
    ))
    return builder.as_markup(resize_keyboard=True)


def discipline():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='ММА 🥊', callback_data='buy_mma'))
    builder.row(types.InlineKeyboardButton(text='БЖЖ 🥋', callback_data='buy_bjj'))
    builder.row(types.InlineKeyboardButton(text='ДЕТИ (Kids) 👶', callback_data='buy_kids'))
    builder.row(types.InlineKeyboardButton(text='⬅️ Назад в профиль', callback_data='profile'))
    return builder.as_markup()
