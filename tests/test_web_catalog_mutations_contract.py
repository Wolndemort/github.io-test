from pathlib import Path


def test_catalog_mutations_have_common_safety_contract():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    for marker, audit in (("@catalog_router.post(\"/products\")", "web_product_created"), ("@catalog_router.patch(\"/products/{product_id}\")", "web_product_updated"), ("@catalog_router.delete(\"/products/{product_id}\")", "web_product_archived")):
        start = source.index(marker); end = source.find("\n\n@", start + 2); block = source[start:] if end < 0 else source[start:end]
        for value in ("WEB_CATALOG_MUTATIONS_ENABLED", "products_manage", "require_csrf", "actor.club_id", "idempotency", audit):
            assert value in block
