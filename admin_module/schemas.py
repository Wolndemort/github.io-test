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
