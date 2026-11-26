import os

import pytest


os.environ.setdefault("SECRET_KEY", "test-secret-key")


@pytest.fixture
def secret_key():
    return "super-secret-key-for-testing"


@pytest.fixture
def sample_payload():
    return "user_id=123&action=login"
