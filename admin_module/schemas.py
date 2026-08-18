from pydantic import BaseModel


class WebAppActionPayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int
    payment_method: str = "bank_card"


class AdminFreezePayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int
    action: str


class StudentInvitePayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int
    parent_slot: int = 1


class WebAppClubPayload(BaseModel):
    init_data: str
    club_id: int


class WebAppBuySubscriptionPayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int
    sport_type: str
    tariff_idx: int
    payment_method: str = "bank_card"


class WebAppCashSubscriptionPayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int
    sport_type: str
    tariff_idx: int
    idempotency_key: str | None = None
    discount_id: int | None = None
    discount_ids: list[int] = []


class WebAppBindPhonePayload(BaseModel):
    init_data: str
    club_id: int
    phone: str


class WebAppCreateStudentPayload(BaseModel):
    init_data: str
    club_id: int
    name: str
    phone: str | None = None
    phone_secondary: str | None = None
    birthday: str | None = None
    discipline: str | None = None


class WebAppHistoryQuery(BaseModel):
    init_data: str
    club_id: int
    student_id: int | None = None


class StudentCreate(BaseModel):
    name: str
    phone: str | None = None
    birthday: str | None = None


class BiometricCheckIn(BaseModel):
    init_data: str
    student_id: int
    # Токен, который Telegram WebApp возвращает после Face ID.
    # Поле было случайно потеряно из общей схемы, из-за чего endpoint
    # падал уже после успешной валидации запроса.
    biometric_token: str | None = None


class BiometricEnable(BaseModel):
    init_data: str


class AdminStudentUpdate(BaseModel):
    init_data: str
    club_id: int
    name: str | None = None
    birthday: str | None = None
    balance_lessons: int | None = None
    expire_date: str | None = None
    can_freeze: int | None = None
    is_frozen: int | None = None
    frozen_at: str | None = None
    frozen_days: int | None = None
    discipline: str | None = None
    parent_phone: str | None = None
    parent_phone_secondary: str | None = None
    comment: str | None = None


class AdminStudentCreate(BaseModel):
    init_data: str
    club_id: int
    name: str
    phone: str | None = None
    phone_secondary: str | None = None
    birthday: str | None = None
    discipline: str
    tariff_idx: int | None = None
    comment: str | None = None
