from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_offer_states_no_refunds_except_when_club_cannot_provide_paid_services():
    page = (ROOT / "templates/oferta.html").read_text(encoding="utf-8")
    assert "Оплаченные услуги, абонементы, занятия, заморозки и товары возврату не подлежат" in page
    assert "Клуб по своей вине не может предоставить Пользователю уже оплаченные услуги" in page


def test_privacy_page_links_the_same_payment_and_refund_rule():
    page = (ROOT / "templates/privacy.html").read_text(encoding="utf-8")
    assert "Оплаченные услуги, абонементы, занятия, заморозки и товары Клубом не возвращаются" in page
    assert 'href="/oferta"' in page
