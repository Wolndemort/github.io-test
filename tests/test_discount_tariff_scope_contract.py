from types import SimpleNamespace

from services.discounts import discount_applies_to_tariff, tariff_ref


def _discount(refs):
    return SimpleNamespace(tariff_refs=refs)


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
