from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_admin_product_images_preserve_full_aspect_ratio():
    for name in ("admin_products.html", "admin_product_sale.html"):
        page = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert "object-fit:contain" in page
        assert "object-fit:cover" not in page
