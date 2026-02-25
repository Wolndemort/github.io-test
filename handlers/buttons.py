from aiogram import Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import WebAppInfo, KeyboardButton

router = Router()


def get_profile_keyboard(is_authorized: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='➕ Добавить атлета', callback_data='add_athlete'))
    builder.row(types.InlineKeyboardButton(text='Проверить статус абонемента', callback_data='check_status_now'))
    builder.row(types.InlineKeyboardButton(text='Купить абонемент 💳', callback_data='choose_section'))
    builder.row(types.InlineKeyboardButton(
        text='❄️ Заморозить абонемент',
        callback_data='freeze_sub'
    ))
    builder.row(types.InlineKeyboardButton(text='📲 МОЙ QR-ПРОПУСК', callback_data='show_qr'))
    builder.row(types.InlineKeyboardButton(text='НАЧАЛО ◀️🔙', callback_data='begin'))
    if not is_authorized:
        builder.row(types.InlineKeyboardButton(text='🔐 Привязать профиль по номеру', callback_data='auth_by_phone'))
    return builder.as_markup()


def get_main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text='Персональные тренировки', callback_data='personal_trainig')
    builder.button(text='BJJ', callback_data='bjj')
    builder.button(text='GRAPPLING KIDS', callback_data='kids')
    builder.button(text="Контакты", callback_data='contact')
    builder.button(text='Открыть сайт', url="https://aemaykop.ru")
    builder.row(types.InlineKeyboardButton(text='👤 МОЙ ПРОФИЛЬ ', callback_data='profile'))
    builder.adjust(2)
    return builder.as_markup()


def get_bjj_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='Информация по ценам на абонемент по Джиу-Джитсу',
                                           callback_data='price_bjj'))
    builder.row(types.InlineKeyboardButton(text='Расписание Джиу-Джитсу', callback_data='schedule_bjj'))
    builder.row(types.InlineKeyboardButton(text='💳 Купить абонемент ', callback_data='buy_bjj'))
    builder.row(types.InlineKeyboardButton(text='🔙 Назад', callback_data='begin'))
    return builder.as_markup()


def get_kids_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='Информация по ценам на десткий абонемент', callback_data='price_kids'))
    builder.row(types.InlineKeyboardButton(text='Расписание Джиу-джитсу для детей', callback_data='schedule_kids'))
    builder.row(types.InlineKeyboardButton(text='💳 Купить абонемент', callback_data='buy_kids'))
    builder.row(types.InlineKeyboardButton(text='🔙 Назад ', callback_data='begin'))
    return builder.as_markup()


def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='📊 Дневной отчет', callback_data='daily_report'))
    builder.row(types.InlineKeyboardButton(text='📢 Рассылка', callback_data='admin_broadcast'))
    builder.row(types.InlineKeyboardButton(text='📥 Выгрузка БД (CSV)', callback_data='export_db'))
    builder.row(types.InlineKeyboardButton(text='🔙 Назад', callback_data='begin'))
    builder.row(types.InlineKeyboardButton(text="🆕 Добавить нового (вручную)", callback_data="admin_add_manual"))
    builder.row(types.InlineKeyboardButton(text="💵 Принять наличку", callback_data="admin_cash_list"))
    builder.row(types.InlineKeyboardButton(text="📝 Отметить посещение", callback_data="admin_manual_visit"))
    builder.button(text="Таблица для админа",
                   web_app=WebAppInfo(url='https://518c7250-6314-4f7a-8d21-d28071f7def2-e1.tunnel4.com/admin'))
    builder.button(text="Отчет Pandas",
                   web_app=WebAppInfo(url='https://518c7250-6314-4f7a-8d21-d28071f7def2-e1.tunnel4.com/stats/revenue'))
    builder.button(text="Выгрузка в Exel",
                   web_app=WebAppInfo
                   (url='https://518c7250-6314-4f7a-8d21-d28071f7def2-e1.tunnel4.com/stats/export/excel'))

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
    builder.row(types.InlineKeyboardButton(text='Взрослые BJJ 🥋', callback_data='buy_bjj'))
    builder.row(types.InlineKeyboardButton(text='ДЕТИ (Kids) BJJ👶', callback_data='buy_kids'))
    builder.row(types.InlineKeyboardButton(text='⬅️ В главное меню', callback_data='begin'))
    return builder.as_markup()
