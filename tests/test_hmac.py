import pytest
from handlers.user_option import generate_signature


def test_signature_length():
    """проверям что функция возвращает 10 символов """
    sig = generate_signature(user_id=123, time_salt="2023-10-10")
    assert len(sig) == 10
    assert isinstance(sig, str)


def test_signature_consistency():
    """Проверям что подпись совпадает при идентичных данных"""
    user_id = 999
    salt = 'random_salt'
    sig1 = generate_signature(user_id, salt)
    sig2 = generate_signature(user_id, salt)
    assert sig1 == sig2


def test_signature_changes_on_input_change():
    """Проверям что при изменении вводных данных подпись меняется """

    user_id = 1
    salt = 'salt'
    origin_signature = generate_signature(user_id, salt)

    assert origin_signature != generate_signature(2, salt)
    assert origin_signature != generate_signature(user_id, 'salt2')


@pytest.mark.parametrize("user_id, time_salt", [
    (0, ""),
    (999999999, 'abc'),
    (-1, '!!!')
])
def test_signature_with_different_types(user_id, time_salt):
    """Проверям что функция не падает на разных типах данных"""
    sig = generate_signature(user_id, time_salt)
    assert len(sig) == 10
