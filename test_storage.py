"""Проверка логики без Telegram: python -m pytest -q  (или python test_storage.py)"""
from datetime import date, datetime, time, timedelta

from bot import norm_phone
from storage import Storage


def make():
    return Storage(":memory:")


def test_slots_and_double_booking():
    s = make()
    d = date.today() + timedelta(days=2)
    free = s.free_slots(d)
    assert free[0] == time(9, 0) and free[-1] == time(20, 0) and len(free) == 12
    b = s.add(1, "hyg", d, time(10, 0), "Анна", "+7 900 000-00-00")
    assert b and b.id == 1
    assert time(10, 0) not in s.free_slots(d)
    assert s.add(2, "car", d, time(10, 0), "Олег", "+7 900 000-00-01") is None  # слот занят


def test_past_slots_hidden_today():
    s = make()
    d = date.today()
    noon = datetime.combine(d, time(12, 0))
    assert all(t > time(13, 0) for t in s.free_slots(d, now=noon))


def test_cancel_only_own_and_frees_slot():
    s = make()
    d = date.today() + timedelta(days=1)
    b = s.add(1, "cons", d, time(15, 0), "Анна", "+7")
    assert s.cancel(b.id, user_id=999) is None
    assert s.cancel(b.id, user_id=1).status == "cancelled"
    assert time(15, 0) in s.free_slots(d)
    assert s.add(2, "cons", d, time(15, 0), "Олег", "+7")  # слот снова доступен


def test_phone_normalization():
    assert norm_phone("89001234567") == "+7 900 123-45-67"
    assert norm_phone("+7 (900) 123-45-67") == "+7 900 123-45-67"
    assert norm_phone("9001234567") == "+7 900 123-45-67"
    assert norm_phone("12345") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
