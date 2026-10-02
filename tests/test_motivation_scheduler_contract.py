from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_legacy_motivation_is_removed_but_empty_page_contract_remains():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    page = (ROOT / "admin_module" / "webapp_views.py").read_text(encoding="utf-8")

    assert "from services.motivation_engine import motivation_accrual_job" in main
    assert "scheduler.add_job(motivation_accrual_job, 'interval', minutes=1" in main
    assert "accrue_motivation_job" not in scheduler
    assert not (ROOT / "services" / "motivation_accrual.py").exists()
    motivation_page = page[page.index('async def admin_motivation_page'):page.index('@router.post("/webapp/admin-motivation/adjust")')]
    assert '"staff": []' in motivation_page


def test_motivation_tables_are_removed_by_migration():
    migration = next((ROOT / "migrations" / "versions").glob("*remove_legacy_motivation.py"))
    text = migration.read_text(encoding="utf-8")
    for table in ("motivation_individuals", "motivation_accruals", "motivation_adjustments", "motivation_rates"):
        assert f"DROP TABLE IF EXISTS {table}" in text
