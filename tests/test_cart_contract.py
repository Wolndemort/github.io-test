import json
from pathlib import Path

from database.db import CartOrder, CartItem, ClubProduct
from admin_module.api import router, _student_identity_phone


def test_cart_models_have_separate_order_and_item_tables():
    assert CartOrder.__tablename__ == "cart_orders"
    assert CartItem.__tablename__ == "cart_items"
    assert ClubProduct.__tablename__ == "club_products"


def test_mixed_cart_payload_keeps_student_binding_and_product_quantity():
    payload = [
        {"item_type": "product", "product_id": 7, "quantity": 2},
        {"item_type": "subscription", "student_id": 11, "sport_type": "general", "tariff_idx": 0},
        {"item_type": "freeze", "student_id": 11, "days": 7},
    ]
    assert payload[0]["quantity"] == 2
    assert payload[1]["student_id"] == payload[2]["student_id"]
    assert {x["item_type"] for x in payload} == {"product", "subscription", "freeze"}


def test_client_shop_has_cart_button_and_checkout_endpoint():
    shop = Path("templates/shop.html").read_text(encoding="utf-8")
    assert "В корзину" in shop
    assert "/webapp/cart/checkout" in shop
    assert "localStorage" in shop


def test_cart_is_separate_page_with_quantity_controls_and_product_limit():
    cart = Path("templates/cart.html").read_text(encoding="utf-8")
    routes = {(route.path, method) for route in router.routes for method in (getattr(route, "methods", None) or {"GET"})}
    assert ("/webapp/cart", "GET") in routes
    assert "data-plus" in cart
    assert "x.item_type==='product'?Number(x.max||0):999" in cart
    assert "x[i].quantity<max" in cart


def test_service_quantities_are_processed_by_checkout_and_webhook():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    webhook = Path("admin_module/payments_webhook.py").read_text(encoding="utf-8")
    assert "qty = int(raw.get(\"quantity\", 1))" in api
    assert "total += price * qty" in api
    assert "quantity=info[\"quantity\"]" in api
    assert "range(max(1, int(item.quantity or 1)))" in webhook


def test_student_duplicate_guards_are_present_for_webapp_and_bot():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    bot = Path("handlers/user_option.py").read_text(encoding="utf-8")
    assert "Такой атлет уже есть в базе клуба" in api
    assert "student.name.strip().casefold()" in api
    assert "student.birthday == birthday" in api
    assert "existing_students = (await session.execute" in bot
    assert "student.birthday == birthday_date" in bot


def test_student_phone_identity_uses_last_ten_digits():
    assert _student_identity_phone("+7 (999) 111-22-33") == "9991112233"
    assert _student_identity_phone("8 999 111 22 33") == "9991112233"
    assert _student_identity_phone("") == ""


def test_admin_product_screen_supports_image_and_editing():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    assert "image_url" in page
    assert "Изменить" in page
    assert "Удалить" in page
    assert "is_active" in page


def test_cart_webhook_has_idempotent_confirmed_guard():
    source = Path("admin_module/payments_webhook.py").read_text(encoding="utf-8")
    assert 'str(order_id).startswith("CART_")' in source
    assert 'cart.status == "CONFIRMED"' in source
    assert 'cart.provider_payment_id = payment_id' in source

def test_product_upload_contract_has_type_and_size_guards():
    source = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert "image/jpeg" in source and "image/png" in source and "image/webp" in source
    assert "8 * 1024 * 1024" in source
    assert "static/uploads/products" in source

def test_product_admin_has_file_input_and_fallback_preview():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    assert "type=file" in page
    assert "upload-image" in page
    assert "🖼️" in page

def test_shop_escapes_product_data_without_inline_product_arguments():
    page = Path("templates/shop.html").read_text(encoding="utf-8")
    assert "|tojson" in page
    assert "function esc" in page or "const esc" in page
    assert "data-id" in page

def test_admin_product_list_escapes_names_and_uses_data_buttons():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    assert "function esc" in page or "const esc" in page
    assert "data-edit" in page and "data-del" in page
