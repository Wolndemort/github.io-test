from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timezone

import httpx
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError
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
    CashSaleDeletePayload,
    ProductPayload,
    ProductCategoryChangePayload,
    ScheduleChangePayload,
    TariffChangePayload,
)
from admin_module.utils import verify_webapp_staff
from admin_module.webapp_shared import get_club_id_from_host, telegram_init_gate, webapp_auth_gate, verify_webapp_admin
from admin_module.webapp_verify import verify_telegram_data
from database.db import Club, ClubProduct, Student, User, get_session, get_student_parent_ids
from database.db import CartItem, CartOrder
from services.audit import audit_event
from admin_module.api import audit_actor_context
from admin_module.api import (
    build_owner_receipt_text,
    build_staff_alert_text,
    format_order_items,
    notify_product_staff,
)
from services.order_notifications import resolve_user_label
from services.schedule_utils import normalize_schedule_block
from services.payment_requisites import get_payment_info_text

def _normalize_category(value: str | None) -> str:
    return " ".join((value or "other").strip().casefold().replace("ё", "е").split()) or "other"

async def _canonical_product_category(session: AsyncSession, club_id: int, value: str | None, *, exclude_id: int | None = None) -> str:
    raw = " ".join((value or "other").strip().split()) or "other"
    wanted = _normalize_category(raw)
    query = select(ClubProduct).where(ClubProduct.club_id == club_id)
    if exclude_id is not None:
        query = query.where(ClubProduct.id != exclude_id)
    products = (await session.execute(query.order_by(ClubProduct.id.asc()))).scalars().all()
    for product in products:
        existing = " ".join((product.category or "other").strip().split()) or "other"
        if _normalize_category(existing) == wanted:
            return existing
    return raw[:30]

def _build_category_list(products):
    labels = {}
    for product in products:
        raw = (getattr(product, "category", None) or "other").strip() or "other"
        labels.setdefault(_normalize_category(raw), raw)
    return [labels[key] for key in sorted(labels)]

@router.get("/webapp/shop", response_class=HTMLResponse)
async def client_shop(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data: return telegram_init_gate('/webapp/shop', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РјР°РіР°Р·РёРЅ РёР· Telegram')
    if not club or not verify_telegram_data(init_data, club.bot_token): raise HTTPException(403, "Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id, ClubProduct.is_active.is_(True), ClubProduct.stock > 0).order_by(ClubProduct.category, ClubProduct.name))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "image_url": p.image_url, "details": p.details} for p in products]
    categories = _build_category_list(products)
    sbp_enabled = bool((club.club_settings or {}).get("payments", {}).get("yookassa_sbp_enabled", True))
    payment_info = get_payment_info_text(club.club_settings or {})
    return templates.TemplateResponse("shop.html", {"request": request, "club": club, "club_id": club_id, "products": product_data, "categories": categories, "sbp_enabled": sbp_enabled, "payment_info": payment_info})


@router.get("/webapp/cart", response_class=HTMLResponse)
async def client_cart(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/cart', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РєРѕСЂР·РёРЅСѓ РёР· Telegram')
    if not club or not verify_telegram_data(init_data, club.bot_token):
        raise HTTPException(403, "Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ")
    sbp_enabled = bool((club.club_settings or {}).get("payments", {}).get("yookassa_sbp_enabled", True))
    payment_info = get_payment_info_text(club.club_settings or {})
    return templates.TemplateResponse("cart.html", {"request": request, "club": club, "club_id": club_id, "sbp_enabled": sbp_enabled, "payment_info": payment_info})

@router.get("/webapp/admin-products", response_class=HTMLResponse)
async def admin_products_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data: return telegram_init_gate('/webapp/admin-products', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РєР°С‚Р°Р»РѕРі РёР· Telegram')
    await verify_webapp_staff(club, init_data, session, "products_view")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id).order_by(ClubProduct.id.desc()))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "is_active": p.is_active, "image_url": p.image_url, "details": p.details} for p in products]
    return templates.TemplateResponse("admin_products.html", {"request": request, "club": club, "club_id": club_id, "products": product_data})

@router.get("/webapp/admin-product-sale", response_class=HTMLResponse)
async def admin_product_sale_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-product-sale', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РїСЂРѕРґР°Р¶Сѓ РёР· Telegram')
    await verify_webapp_staff(club, init_data, session, "cash_sale")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id, ClubProduct.is_active.is_(True)).order_by(ClubProduct.category, ClubProduct.name))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "image_url": p.image_url, "details": p.details} for p in products]
    categories = _build_category_list(products)
    students = (await session.execute(select(Student).where(Student.club_id == club_id).order_by(Student.name))).scalars().all()
    student_data = [{"id": s.id, "name": s.name, "parent_id": s.parent_id} for s in students]
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    recent_orders = (await session.execute(select(CartOrder).where(CartOrder.club_id == club_id, CartOrder.status == "CONFIRMED", CartOrder.created_at >= today).order_by(CartOrder.created_at.desc()).limit(50))).scalars().all()
    recent_sales = []
    for order in recent_orders:
        item_rows = (await session.execute(select(CartItem).where(CartItem.cart_order_id == order.id).order_by(CartItem.id.asc()))).scalars().all()
        recent_sales.append({
            "id": order.id,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "amount_kopecks": order.amount_kopecks,
            "items": [{"title": item.title, "quantity": item.quantity} for item in item_rows],
            "payment_method": "Наличные" if str(order.provider_payment_id or "").startswith("CASH:") else "Онлайн",
        })
    return templates.TemplateResponse("admin_product_sale.html", {"request": request, "club_id": club_id, "products": product_data, "categories": categories, "students": student_data, "recent_sales": recent_sales, "today": today.date().isoformat()})

@router.post("/webapp/admin-product-sale")
async def admin_product_sale(payload: AdminProductSalePayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "cash_sale")
    if not payload.items:
        raise HTTPException(400, "РљРѕСЂР·РёРЅР° РїСѓСЃС‚Р°")
    ids = [int(item.get("product_id")) for item in payload.items]
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == payload.club_id, ClubProduct.id.in_(ids), ClubProduct.is_active.is_(True)).with_for_update())).scalars().all()
    by_id = {p.id: p for p in products}
    normalized, total = [], 0
    for raw in payload.items:
        product = by_id.get(int(raw.get("product_id", 0)))
        quantity = int(raw.get("quantity", 0))
        if not product or quantity < 1 or quantity > 99 or product.stock < quantity:
            raise HTTPException(400, "РўРѕРІР°СЂ РЅРµРґРѕСЃС‚СѓРїРµРЅ РёР»Рё Р·Р°РєРѕРЅС‡РёР»СЃСЏ")
        product.stock -= quantity
        total += product.price_kopecks * quantity
        normalized.append((product, quantity))
    selected_student = None
    selected_parent_id = None
    selected_parent_ids = []
    if payload.student_id is not None:
        selected_student = await session.get(Student, int(payload.student_id))
        if not selected_student or selected_student.club_id != payload.club_id:
            raise HTTPException(400, "Selected athlete is unavailable")
        selected_parent_ids = await get_student_parent_ids(selected_student.id, session)
        selected_parent_id = selected_parent_ids[0] if selected_parent_ids else None
    order_id = f"CASH_PRODUCT_{uuid.uuid4().hex[:12].upper()}"
    buyer_user_id = selected_parent_id or int(tg_user.get("id"))
    order = CartOrder(id=order_id, club_id=payload.club_id, user_id=buyer_user_id, amount_kopecks=total, status="CONFIRMED", provider_payment_id=f"CASH:{order_id}")
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
        student_id=selected_student.id if selected_student else None,
        parent_id=selected_parent_id,
        items=[{"product_id": product.id, "name": product.name, "quantity": quantity} for product, quantity in normalized],
    )

    try:
        bot = Bot(club.bot_token)
        notice_items = [
            type("ItemView", (), {"title": product.name, "quantity": quantity, "product_id": product.id})()
            for product, quantity in normalized
        ]
        buyer_label = await resolve_user_label(session, buyer_user_id, empty_label="Наличная продажа")
        owner_text = build_owner_receipt_text(
            title="Наличная продажа товаров",
            order_id=order_id,
            buyer_label=buyer_label,
            items_text=format_order_items(notice_items, product_only=True),
            amount_kopecks=total,
            extra_lines=["Способ: <b>Наличные</b>"],
        )
        if club.owner_id:
            try:
                await bot.send_message(club.owner_id, owner_text, parse_mode="HTML")
            except Exception:
                logger.exception("Не удалось отправить чек владельцу по продаже %s", order_id)
        if buyer_user_id:
            student_label = escape(selected_student.name if selected_student else "Атлет")
            parent_text = build_owner_receipt_text(
                title="Покупка товаров подтверждена",
                order_id=order_id,
                buyer_label=buyer_label,
                items_text=format_order_items(notice_items, product_only=True),
                amount_kopecks=total,
                extra_lines=[
                    f"Атлет: <b>{student_label}</b>",
                    f"Клуб: <b>{escape(club.name)}</b>",
                    "Чек из магазина сформирован на кассе.",
                ],
            )
            for parent_id in (selected_parent_ids or [buyer_user_id]):
                try:
                    await bot.send_message(parent_id, parent_text, parse_mode="HTML")
                except Exception:
                    logger.exception("Не удалось отправить чек родителю %s по продаже %s", parent_id, order_id)
        await notify_product_staff(
            bot,
            club,
            session,
            build_staff_alert_text(
                title="Новая продажа товаров",
                order_id=order_id,
                buyer_label=buyer_label,
                items_text=format_order_items(notice_items, product_only=True),
                amount_kopecks=total,
                badge="📦",
            ),
        )
        await bot.session.close()
    except Exception:
        logger.exception("Не удалось отправить уведомление о наличной продаже товаров %s", order_id)
        try:
            await bot.session.close()
        except Exception:
            pass
    return {"ok": True, "order_id": order_id, "total_kopecks": total}

@router.post("/admin/cash/sales/{order_id}/delete")
async def delete_cash_product_sale(order_id: str, payload: CashSaleDeletePayload, session: AsyncSession = Depends(get_session)):
    if not payload.confirmed:
        raise HTTPException(400, "Требуется подтверждение удаления продажи")
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_admin(club, payload.init_data)
    order = await session.get(CartOrder, order_id)
    if not order or order.club_id != payload.club_id or order.status != "CONFIRMED":
        raise HTTPException(404, "Наличная продажа не найдена")
    if not str(order.provider_payment_id or "").startswith("CASH:"):
        raise HTTPException(403, "Удалять можно только наличные продажи товаров")
    items = (await session.execute(select(CartItem).where(CartItem.cart_order_id == order.id))).scalars().all()
    restored_items = []
    for item in items:
        if item.product_id:
            product = await session.get(ClubProduct, item.product_id)
            if product:
                product.stock += item.quantity or 1
        restored_items.append({"product_id": item.product_id, "quantity": item.quantity})
        await session.delete(item)
    order.status = "CANCELLED"
    await session.commit()
    audit_event("product_sale_cash_deleted", **await audit_actor_context(session, club, tg_user, "admin/cash/sales/delete"), club_id=payload.club_id, action="delete", object_type="cart_order", object_id=order_id, method="cash", amount_kopecks=order.amount_kopecks, restored_items=restored_items)
    return {"success": True}

@router.post("/webapp/admin-products")
async def create_product(payload: ProductPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id); tg_user = await verify_webapp_staff(club, payload.init_data, session, "products_manage")
    if not payload.name.strip() or payload.price_kopecks <= 0 or payload.stock < 0: raise HTTPException(400, "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Рµ РґР°РЅРЅС‹Рµ С‚РѕРІР°СЂР°")
    category = await _canonical_product_category(session, payload.club_id, payload.category)
    p = ClubProduct(club_id=payload.club_id, name=payload.name.strip()[:120], image_url=(payload.image_url or "")[:500] or None, category=category, price_kopecks=payload.price_kopecks, stock=payload.stock, is_active=payload.is_active, details=(payload.details or "").strip()[:1000] or None)
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

@router.post("/webapp/admin-product-categories/delete")
async def delete_product_category(payload: ProductCategoryChangePayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "products_manage")
    source = " ".join((payload.category or "").strip().split())
    replacement = " ".join((payload.replacement_category or "other").strip().split()) or "other"
    if not source:
        raise HTTPException(400, "Категория не указана")
    if _normalize_category(source) == _normalize_category(replacement):
        raise HTTPException(400, "Выберите другую категорию для переноса")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == payload.club_id))).scalars().all()
    affected = [p for p in products if _normalize_category(p.category) == _normalize_category(source)]
    if not affected:
        raise HTTPException(404, "Категория не найдена")
    target = await _canonical_product_category(session, payload.club_id, replacement)
    for product in affected:
        product.category = target
    await session.commit()
    audit_event(
        "product_category_deleted",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-product-categories/delete"),
        club_id=payload.club_id,
        action="delete",
        object_type="product_category",
        object_id=source,
        category=source,
        replacement_category=target,
        affected_products=len(affected),
    )
    return {"success": True, "replacement_category": target, "affected_products": len(affected)}

@router.post("/webapp/admin-products/upload-image")
async def upload_product_image(club_id: int, init_data: str, image: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id); await verify_webapp_staff(club, init_data, session, "products_manage")
    data = await image.read()
    if len(data) > 8 * 1024 * 1024: raise HTTPException(400, "РР·РѕР±СЂР°Р¶РµРЅРёРµ РЅРµ РґРѕР»Р¶РЅРѕ Р±С‹С‚СЊ Р±РѕР»СЊС€Рµ 8 РњР‘")
    # Mobile Telegram/WebView may send a real JPEG with application/octet-stream
    # (or an empty MIME type). Detect the bytes instead of trusting the header and
    # normalize the result so EXIF orientation and all supported formats work.
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(400, "Разрешены JPG, PNG и WEBP")
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            output = BytesIO()
            normalized.save(output, format="JPEG", quality=88, optimize=True)
            data = output.getvalue()
    except UnidentifiedImageError as exc:
        raise HTTPException(400, "Файл не является корректным изображением JPG, PNG или WEBP") from exc
    folder = "static/uploads/products"; os.makedirs(folder, exist_ok=True)
    filename = f"club_{club_id}_{uuid.uuid4().hex}.jpg"
    path = os.path.join(folder, filename)
    with open(path, "wb") as handle: handle.write(data)
    return {"image_url": f"/static/uploads/products/{filename}"}

@router.patch("/webapp/admin-products/{product_id}")
async def update_product(product_id: int, payload: ProductPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id); tg_user = await verify_webapp_staff(club, payload.init_data, session, "products_manage")
    p = await session.get(ClubProduct, product_id)
    if not p or p.club_id != payload.club_id: raise HTTPException(404, "РўРѕРІР°СЂ РЅРµ РЅР°Р№РґРµРЅ")
    if not payload.name.strip() or payload.price_kopecks <= 0 or payload.stock < 0: raise HTTPException(400, "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Рµ РґР°РЅРЅС‹Рµ С‚РѕРІР°СЂР°")
    before = {"name": p.name, "image_url": p.image_url, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "is_active": p.is_active, "details": p.details}
    category = await _canonical_product_category(session, payload.club_id, payload.category, exclude_id=p.id)
    p.name=payload.name.strip()[:120]; p.image_url=(payload.image_url or "")[:500] or None; p.category=category; p.price_kopecks=payload.price_kopecks; p.stock=payload.stock; p.is_active=payload.is_active; p.details=(payload.details or "").strip()[:1000] or None
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
    if not p or p.club_id != club_id: raise HTTPException(404, "РўРѕРІР°СЂ РЅРµ РЅР°Р№РґРµРЅ")
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
        return telegram_init_gate('/webapp/admin-tariffs', club_id, 'РћС‚РєСЂРѕР№С‚Рµ С‚Р°СЂРёС„С‹ РёР· Telegram')
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
    elif payload.action == "copy_from":
        source_disc = str((payload.tariff or {}).get("source_discipline", "")).strip()
        if not source_disc:
            raise HTTPException(400, "Источник копирования не указан")
        if source_disc == payload.discipline:
            raise HTTPException(400, "Нельзя копировать дисциплину в саму себя")
        source_block = dict(disciplines.get(source_disc, {}))
        if not source_block:
            raise HTTPException(404, "Источник копирования не найден")
        source_tariffs = copy.deepcopy(source_block.get("tariffs", []) or [])
        if not source_tariffs:
            raise HTTPException(400, "Р’ РёСЃС…РѕРґРЅРѕР№ РґРёСЃС†РёРїР»РёРЅРµ РЅРµС‚ С‚Р°СЂРёС„РѕРІ")
        block["type"] = source_block.get("type", block.get("type", "lessons"))
        block["tariffs"] = source_tariffs
        block["active"] = True
        disciplines[payload.discipline] = block
        settings["disciplines"] = disciplines
        await session.execute(update(Club).where(Club.id == club.id).values(club_settings=settings))
        await session.commit()
        redis = getattr(request.app.state, "redis_client", None)
        if redis:
            await redis.delete(f"club_config:{club.bot_token}")
        audit_event(
            "discipline_copied",
            **await audit_actor_context(session, club, tg_user, "webapp/admin-tariffs/change"),
            club_id=club.id,
            action="copy",
            object_type="discipline",
            object_id=payload.discipline,
            discipline=payload.discipline,
            source_discipline=source_disc,
        )
        return {"success": True, "copied_from": source_disc}
    elif payload.action == "create_discipline":
        raw_name = str((payload.tariff or {}).get("name", "")).strip()
        if not raw_name:
            raise HTTPException(400, "Р’РІРµРґРёС‚Рµ РЅР°Р·РІР°РЅРёРµ РґРёСЃС†РёРїР»РёРЅС‹")
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
        return telegram_init_gate('/webapp/admin-schedule', club_id, 'РћС‚РєСЂРѕР№С‚Рµ Р°РґРјРёРЅСЃРєРѕРµ СЂР°СЃРїРёСЃР°РЅРёРµ РёР· Telegram')
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
    elif payload.action == "copy_from":
        source_disc = (payload.source_discipline or "").strip()
        if not source_disc:
            raise HTTPException(400, "Источник копирования не указан")
        if source_disc == payload.discipline:
            raise HTTPException(400, "Нельзя копировать дисциплину в саму себя")
        source_block = dict(disciplines.get(source_disc, {}))
        if not source_block:
            raise HTTPException(404, "Источник копирования не найден")
        source_schedule = normalize_schedule_block(source_block.get("schedule", {}))
        if not any(source_schedule.values()):
            raise HTTPException(400, "В исходной дисциплине нет занятий")
        schedule = {day_key: [copy.deepcopy(lesson) for lesson in source_schedule.get(day_key, [])] for day_key in schedule.keys()}
        block["schedule"] = schedule
        disciplines[payload.discipline] = block
        settings["disciplines"] = disciplines
        club.club_settings = settings
        session.add(club)
        await session.commit()
        audit_event(
            "schedule_copied",
            **await audit_actor_context(session, club, tg_user, "webapp/admin-schedule/change"),
            action="copy",
            object_type="schedule",
            object_id=payload.discipline,
            discipline=payload.discipline,
            source_discipline=source_disc,
        )
        return {"success": True}
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
        else: raise HTTPException(400, "Р—Р°РЅСЏС‚РёРµ РЅРµ РЅР°Р№РґРµРЅРѕ")
    else: raise HTTPException(400, "РќРµРёР·РІРµСЃС‚РЅРѕРµ РґРµР№СЃС‚РІРёРµ")
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
        return telegram_init_gate('/webapp/admin-work-schedule', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РіСЂР°С„РёРє СЂР°Р±РѕС‚С‹ РёР· Telegram')
    await verify_webapp_admin(club, init_data)
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    work_schedule = settings.get("work_schedule", {})
    return templates.TemplateResponse("admin_work_schedule.html", {"request": request, "club": club, "club_id": club_id, "work_schedule": work_schedule})


@router.post("/webapp/admin-work-schedule/change")
async def change_admin_work_schedule(payload: dict, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, int(payload.get("club_id", 0)))
    if not club:
        raise HTTPException(404, "РљР»СѓР± РЅРµ РЅР°Р№РґРµРЅ")
    tg_user = await verify_webapp_admin(club, payload.get("init_data"))
    settings = dict(club.club_settings or {})
    work_schedule = dict(settings.get("work_schedule", {}))
    day = str(payload.get("day", "")).strip()
    if day not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
        raise HTTPException(400, "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ РґРµРЅСЊ")
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
        raise HTTPException(400, "РќРµРёР·РІРµСЃС‚РЅРѕРµ РґРµР№СЃС‚РІРёРµ")
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
        return HTMLResponse(content="<h1>вќЊ РћС€РёР±РєР°: РќРµ СѓРґР°Р»РѕСЃСЊ РѕРїСЂРµРґРµР»РёС‚СЊ ID РєР»СѓР±Р°</h1>", status_code=400)

    stmt = select(Club).where(Club.id == club_id)
    result = await session.execute(stmt)
    club = result.scalar_one_or_none()

    if not club:
        return HTMLResponse(content="<h1>рџЏ° РљР»СѓР± РЅРµ РЅР°Р№РґРµРЅ РІ СЃРёСЃС‚РµРјРµ SpeedyCRM</h1>", status_code=404)
    # РџСѓР±Р»РёС‡РЅР°СЏ РєР»РёРµРЅС‚СЃРєР°СЏ СЃС‚СЂР°РЅРёС†Р°: С‚РѕР»СЊРєРѕ С‡С‚РµРЅРёРµ, Р±РµР· Telegram-Р°СѓС‚РµРЅС‚РёС„РёРєР°С†РёРё.

    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    disciplines_data = settings.get("disciplines", {})

    day_names = {
        "mon": "РџРѕРЅРµРґРµР»СЊРЅРёРє", "tue": "Р’С‚РѕСЂРЅРёРє", "wed": "РЎСЂРµРґР°",
        "thu": "Р§РµС‚РІРµСЂРі", "fri": "РџСЏС‚РЅРёС†Р°", "sat": "РЎСѓР±Р±РѕС‚Р°", "sun": "Р’РѕСЃРєСЂРµСЃРµРЅСЊРµ"
    }

    # РџР°СЂСЃРёРј JSON-РЅР°СЃС‚СЂРѕР№РєРё РєР»СѓР±Р° РІ СЃС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅС‹Р№ СЃРїРёСЃРѕРє РґР»СЏ Jinja2 С€Р°Р±Р»РѕРЅР°
    parsed_disciplines = []

    if isinstance(disciplines_data, dict):
        for disc_key, disc_content in disciplines_data.items():
            if not isinstance(disc_content, dict):
                continue
            if not disc_content.get("active", False):
                continue

            disc_name = disc_content.get("name", "РЎРїРѕСЂС‚РёРІРЅР°СЏ СЃРµРєС†РёСЏ")
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
                        "coach": str(lesson.get("coach", "РРЅСЃС‚СЂСѓРєС‚РѕСЂ")),
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

    # РћС‚РґР°РµРј С‡РёСЃС‚С‹Р№ РєРѕРЅС‚РµРєСЃС‚ РІ С€Р°Р±Р»РѕРЅ
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    context = {
        "request": request,
        "club_name": club.name or 'Р‘РµР· РЅР°Р·РІР°РЅРёСЏ',
        "disciplines": parsed_disciplines
        ,"loading": {"enabled": bool(loading.get("enabled", False)),
                     "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))),
                     "message": str(loading.get("message", "Р—Р°РіСЂСѓР¶Р°РµРј РїСЂРёР»РѕР¶РµРЅРёРµвЂ¦"))},
        "logo_url": str(ui.get("logo_url", ""))
    }
    return templates.TemplateResponse("schedule.html", context)


@router.get("/privacy", response_class=HTMLResponse)
async def get_privacy_page(request: Request):
    """РЎС‚СЂР°РЅРёС†Р° РїРѕР»РёС‚РёРєРё РєРѕРЅС„РёРґРµРЅС†РёР°Р»СЊРЅРѕСЃС‚Рё РґР»СЏ WebApp"""
    return templates.TemplateResponse("privacy.html", {"request": request})


@router.get("/oferta", response_class=HTMLResponse)
async def get_oferta_page(request: Request):
    """РЎС‚СЂР°РЅРёС†Р° РїСѓР±Р»РёС‡РЅРѕР№ РѕС„РµСЂС‚С‹ РґР»СЏ WebApp"""
    return templates.TemplateResponse("oferta.html", {"request": request})


@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    await verify_webapp_admin(club, init_data)
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})


# 2. Р РѕСѓС‚ РіРµРЅРµСЂР°С†РёРё СЃС‚СЂРёРјР°
@router.get("/webapp/live_cam/stream")
async def video_stream(
        club_id: int = Query(...),
        camera_src: str | None = None,
        init_data: str | None = Query(default=None),
        session: AsyncSession = Depends(get_session)
):
    """
    РџСЂРѕРєСЃРёСЂСѓРµС‚ MJPEG РІРёРґРµРѕРїРѕС‚РѕРє РёР· РІРЅСѓС‚СЂРµРЅРЅРµРіРѕ РєРѕРЅС‚РµР№РЅРµСЂР° Docker (go2rtc)
    РЅР°РїСЂСЏРјСѓСЋ РІ WebApp СЃРјР°СЂС‚С„РѕРЅР°, РґРёРЅР°РјРёС‡РµСЃРєРё РїРѕРґСЃС‚Р°РІР»СЏСЏ РєР°РјРµСЂСѓ РёР· РЅР°СЃС‚СЂРѕРµРє РєР»СѓР±Р°.
    """
    # Р’С‹С‚Р°СЃРєРёРІР°РµРј РЅР°СЃС‚СЂРѕР№РєРё РёРјРµРЅРЅРѕ СЌС‚РѕРіРѕ РєР»СѓР±Р° РёР· Р‘Р” РґР»СЏ РёР·РѕР»СЏС†РёРё SaaS
    result = await session.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="РљР»СѓР± РЅРµ РЅР°Р№РґРµРЅ")
    if isinstance(init_data, str) or init_data is None:
        await verify_webapp_admin(club, init_data)

    # Р‘РµСЂРµРј РёРјСЏ РєР°РјРµСЂС‹ РёР· club_settings. Р•СЃР»Рё С‚Р°Рј РїСѓСЃС‚Рѕ, СЃС‚Р°РІРёРј РґРµС„РѕР»С‚РЅРѕРµ "camera1"
    settings = club.club_settings or {}
    turnstile_settings = settings.get("turnstile", {})
    if not isinstance(turnstile_settings, dict):
        turnstile_settings = {}
    camera_src = camera_src or turnstile_settings.get("camera_src") or "camera1"

    # РЎРјР°СЂС‚С„РѕРЅ РЅРµ РІРёРґРёС‚ РІРЅСѓС‚СЂРµРЅРЅСЋСЋ Docker-СЃРµС‚СЊ. РџРѕСЌС‚РѕРјСѓ FastAPI СЃР°Рј РїРѕРґРєР»СЋС‡Р°РµС‚СЃСЏ
    # Рє go2rtc Рё РїРµСЂРµРґР°РµС‚ РїРѕР»СѓС‡РµРЅРЅС‹Рµ Р±Р°Р№С‚С‹ РЅР°СЂСѓР¶Сѓ Р±РµР· РёР·РјРµРЅРµРЅРёСЏ.
    # go2rtc СЂР°Р±РѕС‚Р°РµС‚ РІ С‚РѕР№ Р¶Рµ Docker-СЃРµС‚Рё, С‡С‚Рѕ Рё API. РќРµ РёСЃРїРѕР»СЊР·СѓРµРј
    # host.docker.internal: СЌС‚Рѕ Р»РѕРјР°РµС‚СЃСЏ РЅР° Docker Desktop Рё РїСЂРё РґРµРїР»РѕРµ РЅР° Linux.
    go2rtc_mjpeg_api = "http://go2rtc:1984/api/stream.mjpeg"
    # РќРµРєРѕС‚РѕСЂС‹Рµ RTSP-РёСЃС‚РѕС‡РЅРёРєРё РїРѕРґРЅРёРјР°СЋС‚ РїРµСЂРІС‹Р№ РєР°РґСЂ Р·Р°РјРµС‚РЅРѕ РґРѕР»СЊС€Рµ РѕР±С‹С‡РЅРѕРіРѕ.
    # Р”Р°РµРј go2rtc Р±РѕР»СЊС€Рµ РІСЂРµРјРµРЅРё РёРјРµРЅРЅРѕ РЅР° СѓСЃС‚Р°РЅРѕРІР»РµРЅРёРµ СЃРѕРµРґРёРЅРµРЅРёСЏ СЃ РёСЃС‚РѕС‡РЅРёРєРѕРј.
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
            "РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє go2rtc: club_id={}, camera_src={}, error={}",
            club_id,
            camera_src,
            exc
        )
        raise HTTPException(
            status_code=502,
            detail="РЎРµСЂРІРёСЃ РєР°РјРµСЂ РІСЂРµРјРµРЅРЅРѕ РЅРµРґРѕСЃС‚СѓРїРµРЅ"
        ) from exc

    if response.status_code != 200:
        status_code = response.status_code
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc РЅРµ РѕС‚РґР°Р» MJPEG-РїРѕС‚РѕРє: club_id={}, camera_src={}, status={}",
            club_id,
            camera_src,
            status_code
        )
        raise HTTPException(
            status_code=502,
            detail=f"РљР°РјРµСЂР° РЅРµ РѕС‚РґР°Р»Р° РІРёРґРµРѕРїРѕС‚РѕРє (go2rtc: {status_code})"
        )

    # Р’ MJPEG Р·Р°РіРѕР»РѕРІРѕРє Content-Type СЃРѕРґРµСЂР¶РёС‚ boundary вЂ” РёРјСЏ СЂР°Р·РґРµР»РёС‚РµР»СЏ РєР°РґСЂРѕРІ.
    # РќРµР»СЊР·СЏ РїРѕРґСЃС‚Р°РІР»СЏС‚СЊ РµРіРѕ РІСЂСѓС‡РЅСѓСЋ: Сѓ СЂР°Р·РЅС‹С… РёСЃС‚РѕС‡РЅРёРєРѕРІ РѕРЅРѕ РјРѕР¶РµС‚ РѕС‚Р»РёС‡Р°С‚СЊСЃСЏ.
    content_type = response.headers.get(
        "content-type",
        "multipart/x-mixed-replace; boundary=frame"
    )

    if "multipart/x-mixed-replace" not in content_type.lower():
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc РІРµСЂРЅСѓР» РЅРµРѕР¶РёРґР°РЅРЅС‹Р№ Content-Type: club_id={}, camera_src={}, content_type={}",
            club_id,
            camera_src,
            content_type
        )
        raise HTTPException(
            status_code=502,
            detail="РљР°РјРµСЂР° РґРѕСЃС‚СѓРїРЅР°, РЅРѕ РЅРµ РѕС‚РґР°РµС‚ MJPEG. РџСЂРѕРІРµСЂСЊС‚Рµ РєРѕРґРµРє РІ go2rtc"
        )

    async def stream_generator():
        try:
            # aiter_raw СЃРѕС…СЂР°РЅСЏРµС‚ multipart-СЂР°Р·РјРµС‚РєСѓ Рё РіСЂР°РЅРёС†С‹ JPEG-РєР°РґСЂРѕРІ РєР°Рє РµСЃС‚СЊ.
            async for chunk in response.aiter_raw():
                yield chunk
        except httpx.HTTPError as exc:
            logger.warning(
                "MJPEG-РїРѕС‚РѕРє РѕР±РѕСЂРІР°Р»СЃСЏ: club_id={}, camera_src={}, error={}",
                club_id,
                camera_src,
                exc
            )
        finally:
            await response.aclose()
            await client.aclose()

    # X-Accel-Buffering Р·Р°РїСЂРµС‰Р°РµС‚ Nginx РєРѕРїРёС‚СЊ РєР°РґСЂС‹ РІ Р±СѓС„РµСЂРµ.
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
    Р­РЅРґРїРѕРёРЅС‚, РєРѕС‚РѕСЂС‹Р№ РѕС‚РєСЂС‹РІР°РµС‚СЃСЏ РІ Telegram WebApp РїРѕ СЃСЃС‹Р»РєРµ:
    https://С‚РІРѕСЏ_РєСѓС‡Р°_РїРѕРґРґРѕРјРµРЅРѕРІ.ru/admin/pass-app?user_id={telegram_id}
    """
    if not init_data:
        return HTMLResponse(f"""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script><script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='РћС‚РєСЂРѕР№С‚Рµ РїСЂРёР»РѕР¶РµРЅРёРµ РёР· Telegram';
else location.replace(location.pathname+'?user_id={user_id}&init_data='+encodeURIComponent(tg.initData));
</script>""", status_code=401)

    # Р’С‹С‚Р°СЃРєРёРІР°РµРј СЂРѕРґРёС‚РµР»СЏ Рё РїСЂРѕРІРµСЂСЏРµРј РїРѕРґРїРёСЃР°РЅРЅС‹Рµ РґР°РЅРЅС‹Рµ Telegram РґРѕ РІС‹РґР°С‡Рё РґРµС‚РµР№.
    query = select(User).where(User.user_id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ")

    students_for_club = await db.execute(select(Student.club_id).where(Student.parent_id == user_id, Student.club_id.isnot(None)).limit(1))
    club_id = students_for_club.scalar()
    club = await db.get(Club, club_id) if club_id else None
    tg_user = verify_telegram_data(init_data, club.bot_token if club else "")
    if not tg_user or int(tg_user.get("id", 0)) != user_id:
        raise HTTPException(status_code=403, detail="Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ")
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    freeze_price_per_day = settings.get("limits", {}).get("freeze_price_per_day", 0)
    # Р”РѕСЃС‚Р°РµРј СЃРїРёСЃРѕРє СЃС‚СѓРґРµРЅС‚РѕРІ РґР»СЏ СЌС‚РѕРіРѕ СЂРѕРґРёС‚РµР»СЏ
    students_query = select(Student).where(Student.parent_id == user_id, Student.club_id == club.id)
    students_result = await db.execute(students_query)
    students = students_result.scalars().all()

    # Р РµРЅРґРµСЂРёРј HTML СЃС‚СЂР°РЅРёС†Сѓ Рё РїРµСЂРµРґР°РµРј С‚СѓРґР° СЃРїРёСЃРѕРє РґРµС‚РµР№
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students,
        "club_id": club_id, "user_id": user_id, "club_name": club.name if club else "", "logo_url": ui.get("logo_url", ""),
        "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Р—Р°РіСЂСѓР¶Р°РµРј РїСЂРёР»РѕР¶РµРЅРёРµвЂ¦"))}})

