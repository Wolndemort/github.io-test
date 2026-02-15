from aiogram import Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo


router = Router()


def get_profile_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text='Проверить статус абонемента', callback_data='profile'))
    builder.row(types.InlineKeyboardButton(text='Купить абонемент  💳', callback_data='buy'))
    builder.row(types.InlineKeyboardButton(text='НАЧАЛО ◀️🔙', callback_data='begin'))

    return builder.as_markup()


def get_main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text='BJJ', callback_data='bjj')
    builder.button(text='GRAPPLING KIDS', callback_data='kids')
    builder.button(text="Абонементы", web_app=WebAppInfo(url='https://wolndemort.github.io'))
    builder.button(text="Контакты", callback_data='contact')
    builder.button(text='Открыть сайт', url="https://aemaykop.ru")
    builder.row(types.InlineKeyboardButton(text='Проверить статус абонемента', callback_data='profile'))
    builder.adjust(2)

    return builder.as_markup()


def get_bjj_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text='Стоимость абонементов🥋💸', callback_data='price_bjj'),
        types.InlineKeyboardButton(text='Расписание занятий🥋🗓', callback_data='schedule_bjj')
    )
    builder.row(types.InlineKeyboardButton(text='Купить абонемент BJJ  💳', callback_data='buy_bjj'))
    builder.row(types.InlineKeyboardButton(text='НАЧАЛО ◀️🔙', callback_data='begin'))

    return builder.as_markup()


def get_kids_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text='Стоимость 💸', callback_data='price_kids'),
        types.InlineKeyboardButton(text='Расписание 🗓', callback_data='schedule_kids')
    )
    builder.row(types.InlineKeyboardButton(text='Записать ребенка (Купить) 👶💳', callback_data='buy_kids'))
    builder.row(types.InlineKeyboardButton(text='В главное меню 🏠', callback_data='begin'))

    return builder.as_markup()


def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="📢 Сделать рассылку",
        callback_data="admin_broadcast")
    )
    builder.row(types.InlineKeyboardButton(
        text="📊 Выгрузить базу (Excel)",
        callback_data="export_db")
    )
    return builder.as_markup()

