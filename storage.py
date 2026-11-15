"""SQLite-хранилище записей и расчёт свободных слотов."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

SERVICES: dict[str, tuple[str, int, int]] = {
    # id: (название, цена от, длительность в минутах)
    "cons": ("Консультация и 3D-снимок", 0, 40),
    "hyg": ("Профгигиена", 4500, 60),
    "car": ("Лечение кариеса", 3900, 60),
    "impl": ("Консультация имплантолога", 0, 40),
    "kid": ("Детский приём", 2900, 40),
}

WORK_START = time(9, 0)
WORK_END = time(21, 0)
SLOT_MIN = 60  # шаг сетки записи


@dataclass
class Booking:
    id: int
    user_id: int
    service: str
    day: date
    slot: time
    name: str
    phone: str
    status: str

    @property
    def service_title(self) -> str:
        return SERVICES.get(self.service, (self.service, 0, 0))[0]

    def human(self) -> str:
        return f"{self.day:%d.%m} в {self.slot:%H:%M} — {self.service_title}"


class Storage:
    def __init__(self, path: str = "bookings.db") -> None:
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                day TEXT NOT NULL,
                slot TEXT NOT NULL,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created TEXT NOT NULL
            )"""
        )
        # один активный визит на слот
        self.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_per_slot ON bookings(day, slot) WHERE status='active'"
        )
        self.db.commit()

    @staticmethod
    def _row(r: tuple) -> Booking:
        return Booking(r[0], r[1], r[2], date.fromisoformat(r[3]), time.fromisoformat(r[4]), r[5], r[6], r[7])

    def busy_slots(self, day: date) -> set[time]:
        rows = self.db.execute(
            "SELECT slot FROM bookings WHERE day=? AND status='active'", (day.isoformat(),)
        ).fetchall()
        return {time.fromisoformat(r[0]) for r in rows}

    def free_slots(self, day: date, now: datetime | None = None) -> list[time]:
        now = now or datetime.now()
        busy = self.busy_slots(day)
        out, t = [], datetime.combine(day, WORK_START)
        end = datetime.combine(day, WORK_END)
        while t + timedelta(minutes=SLOT_MIN) <= end:
            if t > now + timedelta(hours=1) and t.time() not in busy:
                out.append(t.time())
            t += timedelta(minutes=SLOT_MIN)
        return out

    def add(self, user_id: int, service: str, day: date, slot: time, name: str, phone: str) -> Booking | None:
        """Возвращает None, если слот уже заняли."""
        try:
            cur = self.db.execute(
                "INSERT INTO bookings(user_id,service,day,slot,name,phone,created) VALUES(?,?,?,?,?,?,?)",
                (user_id, service, day.isoformat(), slot.strftime("%H:%M"), name, phone, datetime.now().isoformat()),
            )
        except sqlite3.IntegrityError:
            return None
        self.db.commit()
        return self.get(cur.lastrowid)

    def get(self, booking_id: int) -> Booking | None:
        r = self.db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        return self._row(r) if r else None

    def user_active(self, user_id: int, today: date | None = None) -> list[Booking]:
        today = today or date.today()
        rows = self.db.execute(
            "SELECT * FROM bookings WHERE user_id=? AND status='active' AND day>=? ORDER BY day, slot",
            (user_id, today.isoformat()),
        ).fetchall()
        return [self._row(r) for r in rows]

    def day_list(self, day: date) -> list[Booking]:
        rows = self.db.execute(
            "SELECT * FROM bookings WHERE day=? AND status='active' ORDER BY slot", (day.isoformat(),)
        ).fetchall()
        return [self._row(r) for r in rows]

    def cancel(self, booking_id: int, user_id: int | None = None) -> Booking | None:
        b = self.get(booking_id)
        if not b or b.status != "active" or (user_id is not None and b.user_id != user_id):
            return None
        self.db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
        self.db.commit()
        b.status = "cancelled"
        return b
