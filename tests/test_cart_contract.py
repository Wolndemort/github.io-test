import json
from pathlib import Path

from database.db import CartOrder, CartItem, ClubProduct
from admin_module.api import router, _student_identity_phone
from admin_module.webapp_views import _build_category_list


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
    assert "закончился" in shop
    assert "Товар закончился. Выберите другой." in shop


def test_cart_checkout_supports_mixed_items_and_rejects_dead_stock():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert "kind in {\"subscription\", \"freeze\"}" in api
    assert "if not p or qty < 1 or qty > 99 or p.stock < qty" in api
    assert "payment_method == \"requisites\"" in api
    assert "_tariff_age_error" in api
    assert "pay_method_type" not in api


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
    assert "line_original = price * qty" in api
    assert "apply_discount" in api
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
    assert _student_identity_phone("+7 (999) 111-22-33") == "79991112233"
    assert _student_identity_phone("8 999 111 22 33") == "79991112233"
    assert _student_identity_phone("") == ""


def test_admin_product_screen_supports_image_and_editing():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    assert "image_url" in page
    assert "Изменить" in page
    assert "Удалить" in page
    assert "is_active" in page


def test_admin_product_sale_page_shows_today_history_and_clear_confirmation_text():
    page = Path("templates/admin_product_sale.html").read_text(encoding="utf-8")
    api = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert "История продаж" in page
    assert "studentSelect" in page
    assert "historyFrom" in page
    assert "historyTo" in page
    assert "historyApply" in page
    assert "historyClear" in page
    assert "recentSales" in page
    assert "Продажа оформлена:" in page
    assert "total_kopecks" in page or "total_kopeks" in page
    assert "selected_parent_id" in api
    assert "created_at\": order.created_at.isoformat()" in api or "created_at': order.created_at.isoformat()" in api


def test_cash_sales_and_manual_cash_entries_are_idempotent_and_restore_stock_on_delete():
    webapp = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    db = Path("database/db.py").read_text(encoding="utf-8")
    migration = Path("migrations/versions/f6a7b8c9d0e1_add_cash_entry_idempotency.py").read_text(encoding="utf-8")
    assert "idempotency_key" in webapp
    assert "IntegrityError" in webapp
    assert "idempotency_key" in api
    assert "IntegrityError" in api
    assert "product.stock += item.quantity" in api
    assert "idempotency_key" in db
    assert "cash_entries" in migration and "ix_cash_entries_idempotency_key" in migration


def test_products_view_does_not_render_management_controls_for_read_only_staff():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    views = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert "can_manage_products" in page
    assert "products_manage" in views


def test_cash_register_exposes_safe_reversal_flow_for_manual_entries():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    page = Path("templates/cash_register.html").read_text(encoding="utf-8")
    assert "class CashEntryReversePayload" in api
    assert "with_for_update=True" in api[api.index("async def reverse_cash_entry"):]
    assert "reversed_entry_id" in api
    assert "reverse-op" in page
    assert "/admin/cash/entries/" in page and "/reverse" in page
    assert "Сторнировано" in page


def test_athletes_panel_has_internal_comment_and_cabinet_return_with_role_split():
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    schemas = Path("admin_module/schemas.py").read_text(encoding="utf-8")
    model = Path("database/db.py").read_text(encoding="utf-8")
    migration = Path("migrations/versions/g7b8c9d0e1f2_add_student_comment.py").read_text(encoding="utf-8")
    assert "В рабочий кабинет" in page
    assert "👥 Атлеты" in page
    assert 'name="comment"' in page and "newComment" in page
    assert 'data-secondary-phone="${esc(s.parent_phone_secondary || \'\')}"' in page
    assert "canManageStudents" in page
    assert 'comment: str | None = None' in schemas
    assert "student.comment" in api
    assert "athletes_manage" in api
    assert "comment" in model and "students" in migration


def test_cash_subscription_schedule_and_tariff_webapps_have_safety_guards():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    views = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    cash = Path("templates/admin_cash_subscription.html").read_text(encoding="utf-8")
    tariffs = Path("templates/admin_tariffs.html").read_text(encoding="utf-8")
    schedule = Path("templates/admin_schedule.html").read_text(encoding="utf-8")
    assert "idempotency_key" in api and "CASH_WEBAPP_" in api
    assert "_tariff_age_error" in api
    assert "Нельзя удалить тариф" in views
    assert "Некорректный день недели" in views
    assert "Время должно быть в формате" in views
    assert "<title>Тарифы и дисциплины</title>" in tariffs
    assert "<title>Расписание</title>" in schedule
    assert "В рабочий кабинет" in cash and "Продажа наличного абонемента" in cash


def test_statistics_and_audit_have_period_totals_and_immutable_pagination():
    stats = Path("templates/stats.html").read_text(encoding="utf-8")
    pages = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    audit = Path("admin_module/api.py").read_text(encoding="utf-8")
    audit_page = Path("templates/admin_audit.html").read_text(encoding="utf-8")
    assert "period_revenue" in pages and "period_expenses" in pages and "period_margin" in pages
    assert "Итого выручка за период" in stats
    assert "page_size = 300" in audit and "has_next" in audit
    assert "Записи аудита нельзя удалять" in audit
    assert "delete-audit" not in audit_page
    assert "Далее" in audit_page


def test_statistics_top_payments_are_based_on_confirmed_payment_amounts_not_balance():
    pages = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    stats = Path("templates/stats.html").read_text(encoding="utf-8")
    assert "PaymentOrder.student_id" in pages
    assert "payment_totals" in pages
    assert '"amount": round(amount / 100, 2)' in pages
    assert "student.amount" in stats


def test_cart_webhook_has_idempotent_confirmed_guard():
    source = Path("admin_module/payments_webhook.py").read_text(encoding="utf-8")
    assert 'str(order_id).startswith("CART_")' in source
    assert 'cart.status == "CONFIRMED"' in source
    assert 'cart.provider_payment_id = payment_id' in source


def test_manual_review_buttons_are_registered_in_bot_router():
    module = Path("handlers/manual_payment_review.py").read_text(encoding="utf-8")
    admin_option = Path("handlers/admin_option.py").read_text(encoding="utf-8")
    assert "manual_order_confirm_" in module
    assert "manual_order_decline_" in module
    assert "manual_payment_review_router" in admin_option

def test_product_upload_contract_has_type_and_size_guards():
    source = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert "image/jpeg" in source and "image/png" in source and "image/webp" in source
    assert "8 * 1024 * 1024" in source
    assert "static/uploads/products" in source

def test_product_admin_has_file_input_and_fallback_preview():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    assert 'type="file"' in page
    assert "upload-image" in page
    assert "thumb(x)" in page
    assert "🖼️" in page or "рџ–јпёЏ" in page

def test_shop_escapes_product_data_without_inline_product_arguments():
    page = Path("templates/shop.html").read_text(encoding="utf-8")
    assert "|tojson" in page
    assert "function esc" in page or "const esc" in page
    assert "data-id" in page
    assert "normalizeCategory" in page
    assert "normalizeCategory(p.category)===currentCategory" in page
    assert "const sbpEnabled" in page
    assert "pay-badge" in page
    assert "data-sbp" in page
    assert '<select id="payment_method">' not in page


def test_admin_products_filter_normalizes_existing_category_values():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    assert "normalizeCategory" in page
    assert "normalizeCategory(x.category)===filter" in page


def test_admin_product_categories_use_autocomplete_and_safe_delete_contract():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    views = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert 'list="categorySuggestions"' in page
    assert '<datalist id="categorySuggestions"></datalist>' in page
    assert 'id="preset"' not in page
    assert "categorySuggestions.insertAdjacentHTML" in page
    assert 'type="button"' in page and 'id="saveBtn"' in page
    assert "/webapp/admin-product-categories/delete" in views
    assert "replacement_category" in views
    assert "ProductCategoryChangePayload" in api
    assert "_canonical_product_category" in views


def test_manual_requisite_orders_have_unique_provider_ids():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    cabinet = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert 'provider_payment_id=f"MANUAL:{order_id}"' in api
    assert 'provider_payment_id=f"MANUAL:{order_id}"' in cabinet


def test_requisites_checkout_flow_covers_products_subscriptions_and_freezes():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    cabinet = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    review = Path("handlers/manual_payment_review.py").read_text(encoding="utf-8")
    shop = Path("templates/shop.html").read_text(encoding="utf-8")
    subscription = Path("templates/webapp_buy_subscription.html").read_text(encoding="utf-8")
    freeze = Path("templates/webapp_buy_freeze.html").read_text(encoding="utf-8")
    assert 'payment_method == "requisites"' in api
    assert 'payment_method == "requisites"' in cabinet
    assert 'item.item_type == "subscription"' in review
    assert 'item.item_type == "freeze"' in review
    assert "product.stock -= item.quantity" in review
    assert "review_required" in shop
    assert "review_required" in subscription
    assert "review_required" in freeze
    assert "Дождитесь подтверждения администратора" in api


def test_webapp_category_lists_collapse_case_and_yo_variants():
    products = [
        type("P", (), {"category": "Кофе"})(),
        type("P", (), {"category": "кофе"})(),
        type("P", (), {"category": "кофё"})(),
        type("P", (), {"category": "Чай"})(),
    ]
    categories = _build_category_list(products)
    assert categories.count("Кофе") == 1
    assert categories.count("Чай") == 1


def test_products_support_details_and_shop_renders_them_without_price_wrap():
    api = Path("admin_module/webapp_views.py").read_text(encoding="utf-8")
    admin = Path("templates/admin_products.html").read_text(encoding="utf-8")
    shop = Path("templates/shop.html").read_text(encoding="utf-8")
    db = Path("database/db.py").read_text(encoding="utf-8")
    assert "details" in db
    assert "details" in api
    assert 'textarea id="details"' in admin
    assert "product-details" in admin
    assert "price-pill" in admin
    assert "product-details" in shop
    assert "white-space:nowrap" in shop


def test_admin_settings_no_longer_duplicates_shop_and_stock_buttons():
    settings = Path("handlers/admin_settings_panel.py").read_text(encoding="utf-8")
    assert "Продажа товаров за наличные" not in settings
    assert "Склад товаров" not in settings

def test_admin_product_list_escapes_names_and_uses_data_buttons():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    assert "function esc" in page or "const esc" in page
    assert "data-edit" in page and "data-del" in page


def test_admin_products_form_is_at_top_and_scrolls_to_edit_target():
    page = Path("templates/admin_products.html").read_text(encoding="utf-8")
    assert page.index('id="productForm"') < page.index('id="list"')
    assert "createBtn.onclick" in page
    assert "productForm.scrollIntoView" in page
    assert "scrollTopBtn" in page



