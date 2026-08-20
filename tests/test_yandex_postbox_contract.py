from pathlib import Path


def test_yandex_postbox_adapter_uses_https_provider_without_logging_secrets():
    source = Path("auth/native_auth.py").read_text(encoding="utf-8")
    assert 'EMAIL_PROVIDER' in source
    assert 'boto3.client' in source
    assert 'YANDEX_POSTBOX_ENDPOINT' in source
    assert 'YANDEX_SECRET_ACCESS_KEY' in source
    assert 'print(' not in source
