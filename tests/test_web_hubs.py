from types import SimpleNamespace
import pytest
from auth import forecast_routes

@pytest.mark.asyncio
async def test_hubs_link_migrated_sections():
    staff=(await forecast_routes.web_student_hub_page(7,SimpleNamespace(actor_type="staff",permissions={"analytics_view"}))).body.decode()
    client=(await forecast_routes.web_client_hub_page(SimpleNamespace())).body.decode()
    assert "/staff/students/7/payments" in staff and "/staff/students/7/discounts" in staff
    assert "/client/cabinet" in client and "/client/products" in client
