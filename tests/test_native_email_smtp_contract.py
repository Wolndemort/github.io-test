from pathlib import Path


def test_email_adapter_supports_alter_reference_smtp_names_without_secret_defaults():
    source = Path("auth/native_auth.py").read_text(encoding="utf-8")
    assert 'SMTP_USERNAME' in source
    assert 'SMTP_PASSWORD' in source
    assert 'SMTP_FROM_EMAIL' in source
    assert 'SMTP_USE_TLS' in source
    assert 'SMTP_PASSWORD", ""' not in source
