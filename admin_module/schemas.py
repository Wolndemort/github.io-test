from pydantic import BaseModel


class WebAppActionPayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int


class WebAppClubPayload(BaseModel):
    init_data: str
    club_id: int


class WebAppBuySubscriptionPayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int
    sport_type: str
    tariff_idx: int


class WebAppBindPhonePayload(BaseModel):
    init_data: str
    club_id: int
    phone: str


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


class BiometricEnable(BaseModel):
    init_data: str


class AdminStudentUpdate(BaseModel):
    init_data: str
    club_id: int
    birthday: str | None = None
    balance_lessons: int | None = None
    expire_date: str | None = None
    can_freeze: int | None = None
    is_frozen: int | None = None
    frozen_at: str | None = None
    frozen_days: int | None = None
    discipline: str | None = None


class AdminStudentCreate(BaseModel):
    init_data: str
    club_id: int
    name: str
    phone: str | None = None
    birthday: str | None = None
    discipline: str
    tariff_idx: int | None = None
