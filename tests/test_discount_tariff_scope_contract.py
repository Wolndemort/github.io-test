from types import SimpleNamespace

from services.discounts import apply_discounts, discount_applies_to_tariff, preferred_discounts, tariff_ref


def _discount(refs):
    return SimpleNamespace(tariff_refs=refs)


def test_club_discount_has_priority_and_discounts_do_not_stack():
    personal = SimpleNamespace(id=1, is_global=False, priority=1, kind="percent", value=50)
    club = SimpleNamespace(id=2, is_global=True, priority=100, kind="percent", value=20)

    assert preferred_discounts([personal, club]) == [club]
    price, applied = apply_discounts(10000, [personal, club])
    assert price == 8000
    assert [discount for discount, _ in applied] == [club]


def test_two_personal_discounts_choose_only_the_highest_priority():
    first = SimpleNamespace(id=1, is_global=False, priority=10, kind="fixed", value=1000)
    second = SimpleNamespace(id=2, is_global=False, priority=20, kind="fixed", value=5000)

    price, applied = apply_discounts(10000, [second, first])
    assert price == 9000
    assert [discount for discount, _ in applied] == [first]


def test_tariff_discount_matches_only_the_creation_snapshot():
    tariff = {"price": 6000, "count": 999, "days": 30}
    refs = [tariff_ref("boxing", 1, tariff)]
    discount = _discount(refs)

    assert discount_applies_to_tariff(discount, "boxing", 1, tariff)
    assert not discount_applies_to_tariff(discount, "boxing", 0, tariff)
    assert not discount_applies_to_tariff(discount, "boxing", 1, {**tariff, "price": 7000})
    assert not discount_applies_to_tariff(discount, "jiu_jitsu", 1, tariff)


def test_legacy_unrestricted_discount_remains_unrestricted():
    discount = _discount([])
    assert discount_applies_to_tariff(discount, "boxing", 0, {"price": 1000, "count": 8, "days": 30})


def test_discount_schema_and_checkout_paths_are_tariff_scoped():
    from pathlib import Path
    root = Path(__file__).parents[1]
    model = (root / "database/db.py").read_text(encoding="utf-8")
    create = (root / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert "tariff_refs" in model
    assert "tariff_ref(discipline, idx, tariffs[idx])" in create
    assert "tariff=(payload.sport_type, payload.tariff_idx, selected_tariff)" in (root / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert "tariff=(str(raw.get(\"sport_type\")), idx, t)" in (root / "admin_module/api.py").read_text(encoding="utf-8")
    page = (root / "templates/admin_discounts.html").read_text(encoding="utf-8")
    assert "tariffDiscipline" in page
    assert "tariffChoice" in page
    assert "tariff_options|tojson" in page
    assert "noTariffFilters" in page
    assert "__all__" in page
    assert "addAllUnlimited" in page


def test_multiple_tariffs_and_selective_discount_assignment_are_wired():
    from pathlib import Path
    root = Path(__file__).parents[1]
    page = (root / "templates/admin_discounts.html").read_text(encoding="utf-8")
    views = (root / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert "const refs=new Map()" in page
    assert "hidden.className='tariffRef'" in page
    assert "student_ids:[...selected]" in page
    assert "Student.id.in_(selected_ids)" in views
    assert "for target_student in target_students" in views


def test_side_bot_and_staff_preview_use_the_same_discounted_subscription_flow():
    from pathlib import Path
    root = Path(__file__).parents[1]
    payments = (root / "handlers/payments.py").read_text(encoding="utf-8")
    cabinet = (root / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert "async def _subscription_price" in payments
    assert "tariff_idx=tariff_idx" in payments
    assert "discount_amount=discount_amount_kopecks / 100" in payments
    assert "staff_mode = await _is_staff_webapp_user(db, club, user_id)" in cabinet
    assert "staff_preview" in cabinet


def test_global_discount_is_explicit_and_assignment_queries_allow_it():
    from pathlib import Path
    root = Path(__file__).parents[1]
    model = (root / "database/db.py").read_text(encoding="utf-8")
    service = (root / "services/discounts.py").read_text(encoding="utf-8")
    views = (root / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    page = (root / "templates/admin_discounts.html").read_text(encoding="utf-8")
    assert "is_global" in model
    assert "Discount.is_global.is_(True)" in service
    assert ".outerjoin(DiscountAssignment" in service
    assert "is_global=bool(raw.get(\"is_global\", False))" in views
    assert "discountGlobal" in page
    assert "change('toggle'" in page
    assert "В рассылку" in page


def test_global_broadcast_does_not_create_personal_assignments_and_has_lifecycle_job():
    from pathlib import Path
    root = Path(__file__).parents[1]
    views = (root / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    scheduler = (root / "services/scheduler_jobs.py").read_text(encoding="utf-8")
    broadcast = (root / "templates/admin_broadcast.html").read_text(encoding="utf-8")
    assert "if broadcast_discount.is_global" in views
    assert "notify:club-discount-start" in scheduler
    assert "notify:club-discount-expired" in scheduler
    assert "discount_id" in broadcast


def test_all_discount_display_and_staff_checkout_paths_are_wired():
    from pathlib import Path
    root = Path(__file__).parents[1]
    bot = (root / "handlers/user_option.py").read_text(encoding="utf-8")
    cabinet = (root / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    web = (root / "templates/webapp_buy_subscription.html").read_text(encoding="utf-8")
    payments = (root / "handlers/payments.py").read_text(encoding="utf-8")
    assert "Вам доступна скидка" in bot
    assert 'active_discount(session, club.id, callback.from_user.id, "subscriptions", tariff=(code, idx, t))' in bot
    assert "staff_mode = await _is_staff_webapp_user(db, club, user_id)" in cabinet
    assert "if not user and not staff_mode" in cabinet
    assert "Number(r.price)===Number(t.price)" in web
    assert "discount_note" in payments


def test_discount_audit_covers_every_price_and_notification_surface():
    from pathlib import Path
    root = Path(__file__).parents[1]
    api = (root / "admin_module/api.py").read_text(encoding="utf-8")
    views = (root / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    cabinet = (root / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    cash = (root / "templates/admin_cash_subscription.html").read_text(encoding="utf-8")
    cart = (root / "templates/cart.html").read_text(encoding="utf-8")
    product = (root / "templates/admin_product_sale.html").read_text(encoding="utf-8")
    jobs = (root / "services/scheduler_jobs.py").read_text(encoding="utf-8")
    assert 'active_discounts(db, club.id, student.parent_id, "subscriptions"' in api
    assert 'active_discounts(session, club.id, int(tg_user["id"]), "subscriptions"' in api
    assert 'active_discounts(session, club.id, buyer_user_id, "products"' in views
    assert 'active_discount(db, club.id, user_id, "subscriptions", student.id' in cabinet
    assert "discountData" in cash and "скидки применены" in cash
    assert "webapp/cart/checkout" in cart
    assert "/webapp/cart/preview" in views
    assert "refreshCartPreview" in cart
    assert "discount_ids" in product
    assert "notify:club-discount-start" in jobs and "notify:club-discount-expired" in jobs


def test_bot_tariff_menu_shows_discount_and_returns_to_discipline():
    from pathlib import Path
    root = Path(__file__).parents[1]
    buttons = (root / "handlers/buttons.py").read_text(encoding="utf-8")
    payments = (root / "handlers/payments.py").read_text(encoding="utf-8")
    page = (root / "templates/admin_discounts.html").read_text(encoding="utf-8")
    assert "discount_by_idx" in buttons
    assert "скидка {discount_label}" in buttons
    assert 'callback_data=f"section_{sport_type}"' in buttons
    assert "_tariff_discount_map" in payments
    assert "window.change=async function" in page


def test_discount_edit_flow_updates_rules_without_touching_order_snapshots():
    from pathlib import Path
    root = Path(__file__).parents[1]
    views = (root / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    page = (root / "templates/admin_discounts.html").read_text(encoding="utf-8")
    db = (root / "database/db.py").read_text(encoding="utf-8")
    assert 'payload.action == "update"' in views
    assert 'discount.name = str(raw.get("name", discount.name))' in views
    assert 'discount.tariff_refs = normalized_refs' in views
    assert 'data-edit' in page
    assert "Сохранить изменения" in page
    assert "Отменить редактирование" in page
    assert "original_amount_kopecks" in db and "discount_name" in db
