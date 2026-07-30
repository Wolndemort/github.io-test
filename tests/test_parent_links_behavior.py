import pytest

from database.db import get_student_parent_ids


class _Result:
    def __init__(self, values):
        self._values = values

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return self._values


class _Session:
    def __init__(self):
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        return _Result([100] if self.calls == 1 else [100, 200, 200])


@pytest.mark.asyncio
async def test_student_parent_ids_contains_legacy_and_secondary_links_once():
    assert await get_student_parent_ids(7, _Session()) == [100, 200]
