import os
from pathlib import Path

def test_client_mutation_flags_are_documented_and_default_disabled_in_source():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    contracts = Path("WEB_MUTATION_CONTRACTS.md").read_text(encoding="utf-8")
    assert 'WEB_CLIENT_STUDENT_MUTATIONS_ENABLED", "0"' in source
    assert 'WEB_CLIENT_BIND_PHONE_ENABLED", "0"' in source
    assert "WEB_CLIENT_STUDENT_MUTATIONS_ENABLED=1" in contracts
    assert "WEB_CLIENT_BIND_PHONE_ENABLED=1" in contracts
