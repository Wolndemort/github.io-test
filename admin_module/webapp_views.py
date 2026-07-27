from __future__ import annotations

import copy
import os
import uuid

import httpx
from aiogram import Bot
from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from admin_module.router_base import router, templates
from admin_module.api import (
    AdminProductSalePayload,
    CashEntryPayload,
    ProductPayload,
    ScheduleChangePayload,
    TariffChangePayload,
)
from admin_module.utils import verify_webapp_staff
from admin_module.webapp_shared import get_club_id_from_host, telegram_init_gate, webapp_auth_gate, verify_webapp_admin
from admin_module.webapp_verify import verify_telegram_data
from database.db import Club, ClubProduct, Student, User, get_session
from database.db import CartItem, CartOrder
from services.audit import audit_event
from admin_module.api import audit_actor_context
from admin_module.api import (
    build_owner_receipt_text,
    build_staff_alert_text,
    format_order_items,
    notify_product_staff,
)
from services.schedule_utils import normalize_schedule_block

@router.get("/webapp/shop", response_class=HTMLResponse)
async def client_shop(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data: return telegram_init_gate('/webapp/shop', club_id, 'Откройте магазин из Telegram')
    if not club or not verify_telegram_data(init_data, club.bot_token): raise HTTPException(403, "Доступ запрещён")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id, ClubProduct.is_active.is_(True), ClubProduct.stock > 0).order_by(ClubProduct.category, ClubProduct.name))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "image_url": p.image_url, "details": p.details} for p in products]
    categories = sorted({(p.category or "other").strip() or "other" for p in products})
    sbp_enabled = bool((club.club_settings or {}).get("payments", {}).get("yookassa_sbp_enabled", True))
    return templates.TemplateResponse("shop.html", {"request": request, "club": club, "club_id": club_id, "products": product_data, "categories": categories, "sbp_enabled": sbp_enabled})


@router.get("/webapp/cart", response_class=HTMLResponse)
async def client_cart(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/cart', club_id, 'Откройте корзину из Telegram')
    if not club or not verify_telegram_data(init_data, club.bot_token):
        raise HTTPException(403, "Доступ запрещён")
    sbp_enabled = bool((club.club_settings or {}).get("payments", {}).get("yookassa_sbp_enabled", True))
    return templates.TemplateResponse("cart.html", {"request": request, "club": club, "club_id": club_id, "sbp_enabled": sbp_enabled})

@router.get("/webapp/admin-products", response_class=HTMLResponse)
async def admin_products_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data: return telegram_init_gate('/webapp/admin-products', club_id, 'Откройте каталог из Telegram')
    await verify_webapp_staff(club, init_data, session, "products_view")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id).order_by(ClubProduct.id.desc()))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "is_active": p.is_active, "image_url": p.image_url} for p in products]
    return templates.TemplateResponse("admin_products.html", {"request": request, "club": club, "club_id": club_id, "products": product_data})

@router.get("/webapp/admin-product-sale", response_class=HTMLResponse)
async def admin_product_sale_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-product-sale', club_id, 'Откройте продажу из Telegram')
    await verify_webapp_staff(club, init_data, session, "cash_sale")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id, ClubProduct.is_active.is_(True), ClubProduct.stock > 0).order_by(ClubProduct.category, ClubProduct.name))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "image_url": p.image_url, "details": p.details} for p in products]
    categories = sorted({(p.category or "other").strip() or "other" for p in products})
    return templates.TemplateResponse("admin_product_sale.html", {"request": request, "club_id": club_id, "products": product_data, "categories": categories})

@router.post("/webapp/admin-product-sale")
async def admin_product_sale(payload: AdminProductSalePayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "cash_sale")
    if not payload.items:
        raise HTTPException(400, "Корзина пуста")
    ids = [int(item.get("product_id")) for item in payload.items]
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == payload.club_id, ClubProduct.id.in_(ids), ClubProduct.is_active.is_(True)).with_for_update())).scalars().all()
    by_id = {p.id: p for p in products}
    normalized, total = [], 0
    for raw in payload.items:
        product = by_id.get(int(raw.get("product_id", 0)))
        quantity = int(raw.get("quantity", 0))
        if not product or quantity < 1 or quantity > 99 or product.stock < quantity:
            raise HTTPException(400, "Товар недоступен или закончился")
        product.stock -= quantity
        total += product.price_kopecks * quantity
        normalized.append((product, quantity))
    order_id = f"CASH_PRODUCT_{uuid.uuid4().hex[:12].upper()}"
    order = CartOrder(id=order_id, club_id=payload.club_id, user_id=None, amount_kopecks=total, status="CONFIRMED", provider_payment_id=f"CASH:{order_id}")
    session.add(order)
    await session.flush()
    for product, quantity in normalized:
        session.add(CartItem(cart_order_id=order_id, product_id=product.id, item_type="product", title=product.name, quantity=quantity, unit_price_kopecks=product.price_kopecks, payload={"category": product.category, "payment_method": "cash"}))
    await session.commit()
    audit_event(
        "product_sale_cash_created",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-product-sale"),
        club_id=club.id,
        action="create",
        object_type="cart_order",
        object_id=order_id,
        source="cash_sale",
        method="cash",
        amount_kopecks=total,
        items=[{"product_id": product.id, "name": product.name, "quantity": quantity} for product, quantity in normalized],
    )
    try:
        bot = Bot(club.bot_token)
        notice_items = [
            type("ItemView", (), {"title": product.name, "quantity": quantity, "product_id": product.id})()
            for product, quantity in normalized
        ]
        owner_text = build_owner_receipt_text(
            title="Наличная продажа товаров",
            order_id=order_id,
            buyer_label="Наличная продажа",
            items_text=format_order_items(notice_items, product_only=True),
            amount_kopecks=total,
            extra_lines=["Способ: <b>Наличные</b>"],
        )
        if club.owner_id:
            await bot.send_message(club.owner_id, owner_text, parse_mode="HTML")
        await notify_product_staff(
            bot,
            club,
            session,
            build_staff_alert_text(
                title="Новая продажа товаров",
                order_id=order_id,
                buyer_label="Наличная продажа",
                items_text=format_order_items(notice_items, product_only=True),
                amount_kopecks=total,
                badge="☕",
            ),
        )
        await bot.session.close()
    except Exception:
        logger.exception("Не удалось отправить уведомление о наличной продаже товаров %s", order_id)
    return {"ok": True, "order_id": order_id, "total_kopecks": total}

@router.post("/webapp/admin-products")
async def create_product(payload: ProductPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id); tg_user = await verify_webapp_staff(club, payload.init_data, session, "products_manage")
    if not payload.name.strip() or payload.price_kopecks <= 0 or payload.stock < 0: raise HTTPException(400, "Некорректные данные товара")
    p = ClubProduct(club_id=payload.club_id, name=payload.name.strip()[:120], image_url=(payload.image_url or "")[:500] or None, category=payload.category[:30], price_kopecks=payload.price_kopecks, stock=payload.stock, is_active=payload.is_active, details=(payload.details or "").strip()[:1000] or None)
    session.add(p); await session.commit()
    audit_event(
        "product_created",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-products"),
        club_id=payload.club_id,
        action="create",
        object_type="product",
        object_id=p.id,
        name=p.name,
        category=p.category,
        price_kopecks=p.price_kopecks,
        stock=p.stock,
        is_active=p.is_active,
    )
    return {"success": True}

@router.post("/webapp/admin-products/upload-image")
async def upload_product_image(club_id: int, init_data: str, image: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id); await verify_webapp_staff(club, init_data, session, "products_manage")
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if image.content_type not in allowed: raise HTTPException(400, "Разрешены JPG, PNG и WEBP")
    data = await image.read()
    if len(data) > 8 * 1024 * 1024: raise HTTPException(400, "Изображение не должно быть больше 8 МБ")
    folder = "static/uploads/products"; os.makedirs(folder, exist_ok=True)
    filename = f"club_{club_id}_{uuid.uuid4().hex}{allowed[image.content_type]}"
    path = os.path.join(folder, filename)
    with open(path, "wb") as handle: handle.write(data)
    return {"image_url": f"/static/uploads/products/{filename}"}

@router.patch("/webapp/admin-products/{product_id}")
async def update_product(product_id: int, payload: ProductPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id); tg_user = await verify_webapp_staff(club, payload.init_data, session, "products_manage")
    p = await session.get(ClubProduct, product_id)
    if not p or p.club_id != payload.club_id: raise HTTPException(404, "Товар не найден")
    if not payload.name.strip() or payload.price_kopecks <= 0 or payload.stock < 0: raise HTTPException(400, "Некорректные данные товара")
    before = {"name": p.name, "image_url": p.image_url, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "is_active": p.is_active, "details": p.details}
    p.name=payload.name.strip()[:120]; p.image_url=(payload.image_url or "")[:500] or None; p.category=payload.category[:30]; p.price_kopecks=payload.price_kopecks; p.stock=payload.stock; p.is_active=payload.is_active; p.details=(payload.details or "").strip()[:1000] or None
    await session.commit()
    audit_event(
        "product_updated",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-products"),
        club_id=payload.club_id,
        action="update",
        object_type="product",
        object_id=p.id,
        changes={k: {"before": before[k], "after": getattr(p, k)} for k in before if before[k] != getattr(p, k)},
    )
    return {"success": True}

@router.delete("/webapp/admin-products/{product_id}")
async def delete_product(product_id: int, club_id: int, init_data: str, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id); tg_user = await verify_webapp_staff(club, init_data, session, "products_manage")
    p = await session.get(ClubProduct, product_id)
    if not p or p.club_id != club_id: raise HTTPException(404, "Товар не найден")
    await session.delete(p); await session.commit()
    audit_event(
        "product_deleted",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-products"),
        club_id=club_id,
        action="delete",
        object_type="product",
        object_id=product_id,
        name=p.name,
        category=p.category,
        price_kopecks=p.price_kopecks,
        stock=p.stock,
    )
    return {"success": True}

@router.get("/webapp/admin-tariffs", response_class=HTMLResponse)
async def webapp_admin_tariffs_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-tariffs', club_id, 'Откройте тарифы из Telegram')
    await verify_webapp_staff(club, init_data, session, "tariffs_manage")
    return templates.TemplateResponse("admin_tariffs.html", {"request": request, "club": club, "club_id": club_id, "disciplines": (club.club_settings or {}).get("disciplines", {})})


@router.post("/webapp/admin-tariffs/change")
async def change_admin_tariff(payload: TariffChangePayload, request: Request, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    if not club:
        raise HTTPException(404, "???? ?? ??????")
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "tariffs_manage")
    settings = dict(club.club_settings or {})
    disciplines = dict(settings.get("disciplines", {}))
    block = dict(disciplines.get(payload.discipline, {}))
    if not block:
        raise HTTPException(404, "???????????? ?? ???????")
    tariffs = list(block.get("tariffs", []) or [])

    def _as_float(value, default=0.0):
        try:
            text = str(value).strip()
            return float(text) if text else float(default)
        except (TypeError, ValueError):
            return float(default)

    def _as_int(value, default=0):
        try:
            text = str(value).strip()
            return int(float(text)) if text else int(default)
        except (TypeError, ValueError):
            return int(default)

    if payload.action == "toggle_active":
        block["active"] = not bool(block.get("active", False))
    elif payload.action == "toggle_type":
        block["type"] = "lessons" if block.get("type", "lessons") == "unlimited" else "unlimited"
        if block["type"] == "unlimited":
            for tariff in tariffs:
                tariff["count"] = 999
    elif payload.action == "create_discipline":
        raw_name = str((payload.tariff or {}).get("name", "")).strip()
        if not raw_name:
            raise HTTPException(400, "Введите название дисциплины")
        code = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw_name).strip("_")
        code = "_".join(part for part in code.split("_") if part)[:32] or "discipline"
        base_code = code
        suffix = 2
        while code in disciplines:
            code = f"{base_code}_{suffix}"
            suffix += 1
        disciplines[code] = {
            "name": raw_name,
            "active": True,
            "type": "lessons",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": [],
        }
        settings["disciplines"] = disciplines
        await session.execute(update(Club).where(Club.id == club.id).values(club_settings=settings))
        await session.commit()
        redis = getattr(request.app.state, "redis_client", None)
        if redis:
            await redis.delete(f"club_config:{club.bot_token}")
        audit_event(
            "discipline_created",
            **await audit_actor_context(session, club, tg_user, "webapp/admin-tariffs/change"),
            club_id=club.id,
            action="create",
            object_type="discipline",
            object_id=code,
            discipline=code,
            tariff={"name": raw_name},
        )
        return {"success": True, "discipline_code": code}
    elif payload.action == "add":
        tariff = payload.tariff or {}
        tariffs.append({"price": _as_float(tariff.get("price", 0)), "days": max(1, _as_int(tariff.get("days", 30), 30)), "count": 999 if block.get("type") == "unlimited" else max(0, _as_int(tariff.get("count", 0), 0)), "min_age": max(0, _as_int(tariff.get("min_age", 0), 0))})
    elif payload.action in {"update", "delete"}:
        if payload.index is None or payload.index < 0 or payload.index >= len(tariffs):
            raise HTTPException(400, "????? ?? ??????")
        if payload.action == "delete":
            tariffs.pop(payload.index)
        else:
            tariff = payload.tariff or {}
            tariffs[payload.index] = {"price": _as_float(tariff.get("price", 0)), "days": max(1, _as_int(tariff.get("days", 30), 30)), "count": 999 if block.get("type") == "unlimited" else max(0, _as_int(tariff.get("count", 0), 0)), "min_age": max(0, _as_int(tariff.get("min_age", 0), 0))}
    else:
        raise HTTPException(400, "??????????? ????????")
    if any(_as_float(t.get("price", 0)) <= 0 or _as_int(t.get("days", 0)) <= 0 or _as_int(t.get("count", 0)) < 0 for t in tariffs):
        raise HTTPException(400, "????, ???? ? ?????????? ?????? ???? ??????????????")
    block["tariffs"] = tariffs
    disciplines[payload.discipline] = block
    settings["disciplines"] = disciplines
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=settings))
    await session.commit()
    redis = getattr(request.app.state, "redis_client", None)
    if redis:
        await redis.delete(f"club_config:{club.bot_token}")
    audit_event(
        "tariff_changed",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-tariffs/change"),
        club_id=club.id,
        action=payload.action,
        object_type="tariff",
        object_id=payload.discipline,
        discipline=payload.discipline,
        tariff_index=payload.index,
        tariff=payload.tariff,
    )
    return {"success": True}

@router.get("/webapp/admin-schedule", response_class=HTMLResponse)
async def webapp_admin_schedule_page(
        request: Request,
        club_id: int = Query(...),
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
):
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    if not init_data:
        return telegram_init_gate('/webapp/admin-schedule', club_id, 'Откройте админское расписание из Telegram')
    await verify_webapp_staff(club, init_data, session, "schedule_view")
    return templates.TemplateResponse("admin_schedule.html", {"request": request, "club": club, "club_id": club_id, "disciplines": (club.club_settings or {}).get("disciplines", {})})

@router.post("/webapp/admin-schedule/change")
async def change_admin_schedule(payload: ScheduleChangePayload, session: AsyncSession = Depends(get_session)):
    club = (await session.execute(select(Club).where(Club.id == payload.club_id))).scalar_one_or_none()
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "schedule_edit")
    settings = dict(club.club_settings or {})
    disciplines = dict(settings.get("disciplines", {}))
    block = dict(disciplines.get(payload.discipline, {}))
    schedule = normalize_schedule_block(block.get("schedule", {}))
    lessons = list(schedule.get(payload.day, []))
    if payload.action == "delete":
        if payload.index is None or payload.index < 0 or payload.index >= len(lessons):
            raise HTTPException(400, "Занятие не найдено")
        lessons.pop(payload.index)
    elif payload.action == "copy_day":
        source_day = (payload.source_day or "").strip()
        source_lessons = list(schedule.get(source_day, []))
        if not source_day:
            raise HTTPException(400, "Источник копирования не указан")
        if source_day == payload.day:
            raise HTTPException(400, "Нельзя копировать день в сам себя")
        if not source_lessons:
            raise HTTPException(400, "В исходном дне нет занятий")
        lessons = [copy.deepcopy(lesson) for lesson in source_lessons]
    elif payload.action in {"add", "update"}:
        lesson = payload.lesson or {}
        raw_max_slots = lesson.get("max_slots", lesson.get("slots", lesson.get("limit", 0)))
        try:
            parsed_max_slots = int(raw_max_slots if raw_max_slots is not None else 0)
        except (ValueError, TypeError):
            parsed_max_slots = 0
        item = {
            "time": str(lesson.get("time", "00:00"))[:5],
            "coach": str(lesson.get("coach", lesson.get("info", "")))[:100],
            "max_slots": max(0, min(999, parsed_max_slots)),
        }
        if payload.action == "add": lessons.append(item)
        elif payload.index is not None and 0 <= payload.index < len(lessons): lessons[payload.index] = item
        else: raise HTTPException(400, "Занятие не найдено")
    else: raise HTTPException(400, "Неизвестное действие")
    lessons.sort(key=lambda x: str(x.get("time", "99:99")))
    schedule[payload.day] = lessons; block["schedule"] = schedule; disciplines[payload.discipline] = block; settings["disciplines"] = disciplines
    club.club_settings = settings
    await session.commit()
    audit_event(
        "schedule_changed",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-schedule/change"),
        club_id=club.id,
        action=payload.action,
        object_type="schedule",
        object_id=f"{payload.discipline}:{payload.day}",
        discipline=payload.discipline,
        day=payload.day,
        lesson=payload.lesson,
        index=payload.index,
        source_day=payload.source_day,
    )
    return {"success": True}


@router.get("/webapp/admin-work-schedule", response_class=HTMLResponse)
async def webapp_admin_work_schedule_page(
        request: Request,
        club_id: int = Query(...),
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-work-schedule', club_id, 'Откройте график работы из Telegram')
    await verify_webapp_admin(club, init_data)
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    work_schedule = settings.get("work_schedule", {})
    return templates.TemplateResponse("admin_work_schedule.html", {"request": request, "club": club, "club_id": club_id, "work_schedule": work_schedule})


@router.post("/webapp/admin-work-schedule/change")
async def change_admin_work_schedule(payload: dict, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, int(payload.get("club_id", 0)))
    if not club:
        raise HTTPException(404, "Клуб не найден")
    tg_user = await verify_webapp_admin(club, payload.get("init_data"))
    settings = dict(club.club_settings or {})
    work_schedule = dict(settings.get("work_schedule", {}))
    day = str(payload.get("day", "")).strip()
    if day not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
        raise HTTPException(400, "Некорректный день")
    action = str(payload.get("action", "")).strip()
    item = payload.get("item") or {}
    if action in {"add", "update"}:
        open_at = str(item.get("open", "09:00"))[:5]
        close_at = str(item.get("close", "18:00"))[:5]
        note = str(item.get("note", ""))[:180]
        work_schedule[day] = {"open": open_at, "close": close_at, "note": note}
    elif action == "delete":
        work_schedule.pop(day, None)
    else:
        raise HTTPException(400, "Неизвестное действие")
    settings["work_schedule"] = work_schedule
    club.club_settings = settings
    await session.commit()
    audit_event(
        "work_schedule_changed",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-work-schedule/change"),
        club_id=club.id,
        action=action,
        object_type="work_schedule",
        object_id=day,
        day=day,
        work_schedule=work_schedule.get(day),
    )
    return {"success": True}


@router.get("/webapp/schedule", response_class=HTMLResponse)
async def webapp_schedule_page(
        request: Request,
        club_id: int = None,
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None)
):
    from database.db import Club
    from sqlalchemy.future import select

    if not club_id:
        try:
            club_id = get_club_id_from_host(request)
        except Exception:
            club_id = None

    if not club_id:
        return HTMLResponse(content="<h1>❌ Ошибка: Не удалось определить ID клуба</h1>", status_code=400)

    stmt = select(Club).where(Club.id == club_id)
    result = await session.execute(stmt)
    club = result.scalar_one_or_none()

    if not club:
        return HTMLResponse(content="<h1>🏰 Клуб не найден в системе SpeedyCRM</h1>", status_code=404)
    # Публичная клиентская страница: только чтение, без Telegram-аутентификации.

    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    disciplines_data = settings.get("disciplines", {})

    day_names = {
        "mon": "Понедельник", "tue": "Вторник", "wed": "Среда",
        "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"
    }

    # Парсим JSON-настройки клуба в структурированный список для Jinja2 шаблона
    parsed_disciplines = []

    if isinstance(disciplines_data, dict):
        for disc_key, disc_content in disciplines_data.items():
            if not isinstance(disc_content, dict):
                continue
            if not disc_content.get("active", False):
                continue

            disc_name = disc_content.get("name", "Спортивная секция")
            schedule_data = normalize_schedule_block(disc_content.get("schedule", {}))

            parsed_days = []
            for day_key, day_title in day_names.items():
                lessons = schedule_data.get(day_key, [])
                if not lessons or not isinstance(lessons, list):
                    continue

                parsed_lessons = []
                for lesson in lessons:
                    if not isinstance(lesson, dict):
                        continue

                    max_slots = lesson.get("max_slots") or lesson.get("slots") or lesson.get("limit") or 50
                    taken_slots = lesson.get("taken_slots") or 0

                    try:
                        max_slots = int(max_slots)
                    except (ValueError, TypeError):
                        max_slots = 50

                    try:
                        taken_slots = int(taken_slots)
                    except (ValueError, TypeError):
                        taken_slots = 0

                    parsed_lessons.append({
                        "time": str(lesson.get("time", "00:00")),
                        "coach": str(lesson.get("coach", "Инструктор")),
                        "max_slots": max_slots,
                        "free_slots": max(0, max_slots - taken_slots)
                    })

                if parsed_lessons:
                    parsed_days.append({
                        "key": day_key,
                        "title": day_title,
                        "lessons": parsed_lessons
                    })

            parsed_disciplines.append({
                "code": disc_key,
                "name": disc_name,
                "days": parsed_days
            })

    # Отдаем чистый контекст в шаблон
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    context = {
        "request": request,
        "club_name": club.name or 'Без названия',
        "disciplines": parsed_disciplines
        ,"loading": {"enabled": bool(loading.get("enabled", False)),
                     "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))),
                     "message": str(loading.get("message", "Загружаем приложение…"))},
        "logo_url": str(ui.get("logo_url", ""))
    }
    return templates.TemplateResponse("schedule.html", context)


@router.get("/privacy", response_class=HTMLResponse)
async def get_privacy_page(request: Request):
    """Страница политики конфиденциальности для WebApp"""
    return templates.TemplateResponse("privacy.html", {"request": request})


@router.get("/oferta", response_class=HTMLResponse)
async def get_oferta_page(request: Request):
    """Страница публичной оферты для WebApp"""
    return templates.TemplateResponse("oferta.html", {"request": request})


@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    await verify_webapp_admin(club, init_data)
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})


# 2. Роут генерации стрима
@router.get("/webapp/live_cam/stream")
async def video_stream(
        club_id: int = Query(...),
        camera_src: str | None = None,
        init_data: str | None = Query(default=None),
        session: AsyncSession = Depends(get_session)
):
    """
    Проксирует MJPEG видеопоток из внутреннего контейнера Docker (go2rtc)
    напрямую в WebApp смартфона, динамически подставляя камеру из настроек клуба.
    """
    # Вытаскиваем настройки именно этого клуба из БД для изоляции SaaS
    result = await session.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    if isinstance(init_data, str) or init_data is None:
        await verify_webapp_admin(club, init_data)

    # Берем имя камеры из club_settings. Если там пусто, ставим дефолтное "camera1"
    settings = club.club_settings or {}
    turnstile_settings = settings.get("turnstile", {})
    if not isinstance(turnstile_settings, dict):
        turnstile_settings = {}
    camera_src = camera_src or turnstile_settings.get("camera_src") or "camera1"

    # Смартфон не видит внутреннюю Docker-сеть. Поэтому FastAPI сам подключается
    # к go2rtc и передает полученные байты наружу без изменения.
    # go2rtc работает в той же Docker-сети, что и API. Не используем
    # host.docker.internal: это ломается на Docker Desktop и при деплое на Linux.
    go2rtc_mjpeg_api = "http://go2rtc:1984/api/stream.mjpeg"
    # Некоторые RTSP-источники поднимают первый кадр заметно дольше обычного.
    # Даем go2rtc больше времени именно на установление соединения с источником.
    timeout = httpx.Timeout(connect=150.0, read=None, write=10.0, pool=10.0)
    client = httpx.AsyncClient(timeout=timeout)

    try:
        request = client.build_request(
            "GET",
            go2rtc_mjpeg_api,
            params={"src": camera_src}
        )
        response = await client.send(request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        logger.error(
            "Не удалось подключиться к go2rtc: club_id={}, camera_src={}, error={}",
            club_id,
            camera_src,
            exc
        )
        raise HTTPException(
            status_code=502,
            detail="Сервис камер временно недоступен"
        ) from exc

    if response.status_code != 200:
        status_code = response.status_code
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc не отдал MJPEG-поток: club_id={}, camera_src={}, status={}",
            club_id,
            camera_src,
            status_code
        )
        raise HTTPException(
            status_code=502,
            detail=f"Камера не отдала видеопоток (go2rtc: {status_code})"
        )

    # В MJPEG заголовок Content-Type содержит boundary — имя разделителя кадров.
    # Нельзя подставлять его вручную: у разных источников оно может отличаться.
    content_type = response.headers.get(
        "content-type",
        "multipart/x-mixed-replace; boundary=frame"
    )

    if "multipart/x-mixed-replace" not in content_type.lower():
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc вернул неожиданный Content-Type: club_id={}, camera_src={}, content_type={}",
            club_id,
            camera_src,
            content_type
        )
        raise HTTPException(
            status_code=502,
            detail="Камера доступна, но не отдает MJPEG. Проверьте кодек в go2rtc"
        )

    async def stream_generator():
        try:
            # aiter_raw сохраняет multipart-разметку и границы JPEG-кадров как есть.
            async for chunk in response.aiter_raw():
                yield chunk
        except httpx.HTTPError as exc:
            logger.warning(
                "MJPEG-поток оборвался: club_id={}, camera_src={}, error={}",
                club_id,
                camera_src,
                exc
            )
        finally:
            await response.aclose()
            await client.aclose()

    # X-Accel-Buffering запрещает Nginx копить кадры в буфере.
    return StreamingResponse(
        stream_generator(),
        headers={
            "Content-Type": content_type,
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/pass-app", response_class=HTMLResponse)
async def get_web_app_page(
        request: Request,
        user_id: int,
        init_data: str | None = Query(default=None),
        db: AsyncSession = Depends(get_session),
):
    """
    Эндпоинт, который открывается в Telegram WebApp по ссылке:
    https://твоя_куча_поддоменов.ru/admin/pass-app?user_id={telegram_id}
    """
    if not init_data:
        return HTMLResponse(f"""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script><script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='Откройте приложение из Telegram';
else location.replace(location.pathname+'?user_id={user_id}&init_data='+encodeURIComponent(tg.initData));
</script>""", status_code=401)

    # Вытаскиваем родителя и проверяем подписанные данные Telegram до выдачи детей.
    query = select(User).where(User.user_id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    students_for_club = await db.execute(select(Student.club_id).where(Student.parent_id == user_id, Student.club_id.isnot(None)).limit(1))
    club_id = students_for_club.scalar()
    club = await db.get(Club, club_id) if club_id else None
    tg_user = verify_telegram_data(init_data, club.bot_token if club else "")
    if not tg_user or int(tg_user.get("id", 0)) != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    freeze_price_per_day = settings.get("limits", {}).get("freeze_price_per_day", 0)
    # Достаем список студентов для этого родителя
    students_query = select(Student).where(Student.parent_id == user_id, Student.club_id == club.id)
    students_result = await db.execute(students_query)
    students = students_result.scalars().all()

    # Рендерим HTML страницу и передаем туда список детей
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students,
        "club_id": club_id, "user_id": user_id, "club_name": club.name if club else "", "logo_url": ui.get("logo_url", ""),
        "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Загружаем приложение…"))}})
