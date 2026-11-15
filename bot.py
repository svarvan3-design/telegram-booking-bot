"""Telegram-бот записи клиентов (демо для стоматологии «Белый Квартал»).

Запуск:  python bot.py   (нужен файл .env с BOT_TOKEN и ADMIN_ID, см. README.md)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from storage import SERVICES, Storage

# ---------- конфиг ----------
def load_env(path: str = ".env") -> None:
    p = Path(__file__).with_name(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()
TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
CLINIC = os.getenv("CLINIC_NAME", "Белый Квартал")
ADDRESS = os.getenv("CLINIC_ADDRESS", "ул. Садовая, 12, 2 этаж")
PHONE = os.getenv("CLINIC_PHONE", "+7 (000) 000-00-00")
DAYS_AHEAD = 7

db = Storage(str(Path(__file__).with_name("bookings.db")))
router = Router()
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


# ---------- callback-данные ----------
class Svc(CallbackData, prefix="svc"):
    id: str

class Day(CallbackData, prefix="day"):
    d: str

class Slot(CallbackData, prefix="slot"):
    t: str

class Act(CallbackData, prefix="act"):
    a: str
    id: int = 0


class Book(StatesGroup):
    service = State()
    day = State()
    slot = State()
    name = State()
    phone = State()
    confirm = State()


# ---------- клавиатуры ----------
MAIN = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📅 Записаться")],
              [KeyboardButton(text="💰 Цены"), KeyboardButton(text="🗂 Мои записи")],
              [KeyboardButton(text="📍 Контакты")]],
    resize_keyboard=True,
)

def kb_services():
    b = InlineKeyboardBuilder()
    for sid, (title, price, _) in SERVICES.items():
        b.button(text=f"{title} · {'бесплатно' if not price else f'от {price:,} ₽'.replace(',', ' ')}",
                 callback_data=Svc(id=sid))
    b.adjust(1)
    return b.as_markup()

def kb_days():
    b = InlineKeyboardBuilder()
    today = date.today()
    for i in range(DAYS_AHEAD):
        d = today + timedelta(days=i)
        if not db.free_slots(d):
            continue
        label = "Сегодня" if i == 0 else "Завтра" if i == 1 else f"{WD[d.weekday()]} {d:%d.%m}"
        b.button(text=label, callback_data=Day(d=d.isoformat()))
    b.button(text="← Назад", callback_data=Act(a="back_svc"))
    b.adjust(3)
    return b.as_markup()

def kb_slots(d: date):
    b = InlineKeyboardBuilder()
    for t in db.free_slots(d):
        b.button(text=t.strftime("%H:%M"), callback_data=Slot(t=t.strftime("%H:%M")))
    b.button(text="← Другой день", callback_data=Act(a="back_day"))
    b.adjust(4)
    return b.as_markup()

PHONE_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
    resize_keyboard=True, one_time_keyboard=True,
)


def human_day(d: date) -> str:
    return f"{WD[d.weekday()]}, {d:%d.%m}"


# ---------- общие команды ----------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        f"Здравствуйте! Я бот стоматологии «{CLINIC}».\n\n"
        "Запишу вас на приём за минуту: выберите услугу, день и время. "
        "Первая консультация бесплатная.",
        reply_markup=MAIN,
    )

@router.message(Command("cancel"))
@router.message(F.text.lower() == "отмена")
async def cancel_flow(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Хорошо, запись не оформлена.", reply_markup=MAIN)

@router.message(F.text == "💰 Цены")
async def prices(m: Message):
    lines = [f"• {t} — {'бесплатно' if not p else f'от {p:,} ₽'.replace(',', ' ')}" for t, p, _ in SERVICES.values()]
    await m.answer("Цены на услуги:\n\n" + "\n".join(lines) +
                   "\n\nТочную сумму врач назовёт на консультации и зафиксирует в договоре.")

@router.message(F.text == "📍 Контакты")
async def contacts(m: Message):
    await m.answer(f"📍 {ADDRESS}\n📞 {PHONE}\n🕘 Ежедневно 9:00–21:00")

@router.message(F.text == "🗂 Мои записи")
async def my(m: Message):
    items = db.user_active(m.from_user.id)
    if not items:
        await m.answer("У вас нет предстоящих записей.")
        return
    for b in items:
        kb = InlineKeyboardBuilder()
        kb.button(text="Отменить", callback_data=Act(a="cancel", id=b.id))
        await m.answer(f"🗓 {human_day(b.day)} в {b.slot:%H:%M}\n🦷 {b.service_title}", reply_markup=kb.as_markup())


# ---------- сценарий записи ----------
@router.message(F.text == "📅 Записаться")
@router.message(Command("book"))
async def book_start(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Book.service)
    await m.answer("Выберите услугу:", reply_markup=kb_services())

@router.callback_query(Book.service, Svc.filter())
async def pick_service(c: CallbackQuery, callback_data: Svc, state: FSMContext):
    await state.update_data(service=callback_data.id)
    await state.set_state(Book.day)
    await c.message.edit_text(f"Услуга: {SERVICES[callback_data.id][0]}\n\nВыберите день:", reply_markup=kb_days())
    await c.answer()

@router.callback_query(Book.day, Act.filter(F.a == "back_svc"))
async def back_svc(c: CallbackQuery, state: FSMContext):
    await state.set_state(Book.service)
    await c.message.edit_text("Выберите услугу:", reply_markup=kb_services())
    await c.answer()

@router.callback_query(Book.day, Day.filter())
async def pick_day(c: CallbackQuery, callback_data: Day, state: FSMContext):
    d = date.fromisoformat(callback_data.d)
    await state.update_data(day=callback_data.d)
    await state.set_state(Book.slot)
    await c.message.edit_text(f"{human_day(d)}. Свободное время:", reply_markup=kb_slots(d))
    await c.answer()

@router.callback_query(Book.slot, Act.filter(F.a == "back_day"))
async def back_day(c: CallbackQuery, state: FSMContext):
    await state.set_state(Book.day)
    await c.message.edit_text("Выберите день:", reply_markup=kb_days())
    await c.answer()

@router.callback_query(Book.slot, Slot.filter())
async def pick_slot(c: CallbackQuery, callback_data: Slot, state: FSMContext):
    await state.update_data(slot=callback_data.t)
    await state.set_state(Book.name)
    await c.message.edit_text(f"Время {callback_data.t} ✅")
    await c.message.answer("Как к вам обращаться? Напишите имя.", reply_markup=ReplyKeyboardRemove())
    await c.answer()

@router.message(Book.name, F.text)
async def get_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if not 2 <= len(name) <= 40:
        await m.answer("Напишите, пожалуйста, имя (от 2 до 40 символов).")
        return
    await state.update_data(name=name)
    await state.set_state(Book.phone)
    await m.answer("Оставьте номер телефона: нажмите кнопку ниже или напишите его.", reply_markup=PHONE_KB)

def norm_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    return f"+{digits[0]} {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:]}"

@router.message(Book.phone)
async def get_phone(m: Message, state: FSMContext):
    raw = m.contact.phone_number if m.contact else (m.text or "")
    phone = norm_phone(raw)
    if not phone:
        await m.answer("Не похоже на номер. Пример: +7 900 123-45-67")
        return
    await state.update_data(phone=phone)
    data = await state.get_data()
    d = date.fromisoformat(data["day"])
    await state.set_state(Book.confirm)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=Act(a="confirm"))
    kb.button(text="✖️ Отменить", callback_data=Act(a="abort"))
    await m.answer("Проверьте данные:", reply_markup=ReplyKeyboardRemove())
    await m.answer(
        f"🦷 {SERVICES[data['service']][0]}\n🗓 {human_day(d)} в {data['slot']}\n"
        f"👤 {data['name']}\n📞 {phone}",
        reply_markup=kb.as_markup(),
    )

@router.callback_query(Book.confirm, Act.filter(F.a == "abort"))
async def abort(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Запись отменена.")
    await c.message.answer("Если передумаете, нажмите «📅 Записаться».", reply_markup=MAIN)
    await c.answer()

@router.callback_query(Book.confirm, Act.filter(F.a == "confirm"))
async def confirm(c: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    d, t = date.fromisoformat(data["day"]), time.fromisoformat(data["slot"])
    b = db.add(c.from_user.id, data["service"], d, t, data["name"], data["phone"])
    if b is None:
        await state.set_state(Book.slot)
        await c.message.edit_text("Это время только что заняли 😔 Выберите другое:", reply_markup=kb_slots(d))
        await c.answer()
        return
    await state.clear()
    await c.message.edit_text(
        f"Готово! Ждём вас {human_day(d)} в {t:%H:%M}.\n📍 {ADDRESS}\n\n"
        "Отменить или посмотреть запись можно в разделе «🗂 Мои записи»."
    )
    await c.message.answer("Чем ещё помочь?", reply_markup=MAIN)
    await c.answer("Записали!")
    if ADMIN_ID:
        user = f"@{c.from_user.username}" if c.from_user.username else f"id {c.from_user.id}"
        await bot.send_message(ADMIN_ID, f"🆕 Новая запись #{b.id}\n{b.human()}\n👤 {b.name}, {b.phone} ({user})")


# ---------- отмена записи клиентом ----------
@router.callback_query(Act.filter(F.a == "cancel"))
async def user_cancel(c: CallbackQuery, callback_data: Act, bot: Bot):
    b = db.cancel(callback_data.id, user_id=c.from_user.id)
    if not b:
        await c.answer("Запись не найдена или уже отменена", show_alert=True)
        return
    await c.message.edit_text(f"❌ Запись отменена: {b.human()}")
    await c.answer()
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f"❌ Клиент отменил запись #{b.id}\n{b.human()}\n👤 {b.name}, {b.phone}")


# ---------- админ ----------
def is_admin(m: Message) -> bool:
    return bool(ADMIN_ID) and m.from_user.id == ADMIN_ID

@router.message(Command("today", "tomorrow"))
async def admin_day(m: Message):
    if not is_admin(m):
        return
    d = date.today() + timedelta(days=1 if m.text.startswith("/tomorrow") else 0)
    items = db.day_list(d)
    if not items:
        await m.answer(f"На {human_day(d)} записей нет.")
        return
    await m.answer(f"Записи на {human_day(d)}:\n\n" + "\n".join(
        f"{b.slot:%H:%M} — {b.service_title}\n   {b.name}, {b.phone} (#{b.id})" for b in items))


# ---------- напоминания ----------
async def reminder_loop(bot: Bot):
    """Раз в час напоминает о визитах, до которых осталось 23–24 часа."""
    sent: set[int] = set()
    while True:
        now = datetime.now()
        for d in (now.date(), now.date() + timedelta(days=1)):
            for b in db.day_list(d):
                left = datetime.combine(b.day, b.slot) - now
                if b.id not in sent and timedelta(hours=23) <= left <= timedelta(hours=24):
                    try:
                        await bot.send_message(b.user_id, f"⏰ Напоминаем: завтра в {b.slot:%H:%M} ждём вас на «{b.service_title}».\n📍 {ADDRESS}")
                        sent.add(b.id)
                    except Exception as e:  # клиент мог заблокировать бота
                        logging.warning("reminder %s failed: %s", b.id, e)
        await asyncio.sleep(3600)


async def main():
    if not TOKEN:
        raise SystemExit("Нет BOT_TOKEN. Создайте файл .env по образцу .env.example")
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(reminder_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
