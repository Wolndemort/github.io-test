from pathlib import Path


def test_async_summary_loaders_do_not_replace_operation_mount_points():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    for marker in ("data-cash-summary", "data-sales-summary", "data-checkin-summary"):
        assert marker in source
    assert "document.querySelector('#cash').innerHTML" not in source
    assert "document.querySelector('#sales').innerHTML" not in source
    assert "document.querySelector('#checkin').innerHTML" not in source
