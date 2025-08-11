# main.py — Render + webhook (aiogram v3)

import os
import asyncio
import json
import random
from io import BytesIO

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from PIL import Image, ImageDraw, ImageFont

# --- Webhook сервер на aiohttp
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# --- Google Sheets (ленивое подключение через ENV)
SHEET_ID = os.getenv("SHEET_ID")  # например: 1392i1U93gV5...
SHEETS_CREDS_JSON = os.getenv("SHEETS_CREDS_JSON")  # полный JSON сервисного аккаунта в одну строку

API_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "super_secret_123")
BASE_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("BASE_URL")
PORT = int(os.getenv("PORT", "10000"))

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

user_data = {}
referrals = {}

# ---------- Google Sheets helpers ----------
def get_worksheet():
    """
    Возвращает sheet1 или None, если переменные окружения не заданы.
    Подключаемся «лениво», чтобы отсутствие ключей не ломало бота.
    """
    if not (SHEET_ID and SHEETS_CREDS_JSON):
        return None
    import gspread
    from google.oauth2.service_account import Credentials

    creds_dict = json.loads(SHEETS_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.sheet1

def save_guest_to_sheets(user_id, first_name, last_name, company):
    try:
        ws = get_worksheet()
        if not ws:
            return
        # при желании можно сделать заголовки, если лист пустой
        ws.append_row([first_name, last_name, company, str(user_id)])
    except Exception as e:
        print("Ошибка при записи в таблицу:", e)

# ---------- Хэндлеры ----------
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    text = message.text or ""
    parts = text.split(maxsplit=1)
    args = parts[1] if len(parts) > 1 else None
    inviter_id = int(args) if (args and args.isdigit()) else None
    user_id = message.from_user.id

    if inviter_id and inviter_id != user_id:
        referrals.setdefault(inviter_id, [])
        if user_id not in referrals[inviter_id]:
            referrals[inviter_id].append(user_id)

    # баннер, если есть
    if os.path.exists("templates/banner.png"):
        banner = FSInputFile("templates/banner.png")
        await message.answer_photo(photo=banner)

    await message.answer(
        "Привет, рады тебя видеть!\n\n"
        "Этот бот поможет тебе оформить красивый инвайт, а также даёт право на участие в розыгрыше VIP билета на PRO PARTY от Digital CPA Club. "
        "Вечеринка пройдёт 14 августа в Москве в noorbar.com, с кейс-программой, "
        "танцами, нетворкингом и коктейлями.\n\n"
        "Подробная информация и регистрация на мероприятие "
        "[здесь](https://digitalclub.timepad.ru/event/3457454/)",
        parse_mode="Markdown"
    )
    await message.answer("Как тебя зовут?")
    user_data[user_id] = {}

@dp.message(lambda m: m.from_user.id in user_data and 'first_name' not in user_data[m.from_user.id])
async def get_first_name(message: types.Message):
    user_data[message.from_user.id]['first_name'] = (message.text or "").strip()
    await message.answer("Какая у тебя фамилия?")

@dp.message(lambda m: m.from_user.id in user_data and 'last_name' not in user_data[m.from_user.id])
async def get_last_name(message: types.Message):
    user_data[message.from_user.id]['last_name'] = (message.text or "").strip()
    await message.answer("Из какой компании?")

@dp.message(lambda m: m.from_user.id in user_data and 'company' not in user_data[m.from_user.id])
async def get_company(message: types.Message):
    user_data[message.from_user.id]['company'] = (message.text or "").strip()
    first = user_data[message.from_user.id]['first_name'] or "Гость"
    await message.answer(f"{first}, приятно познакомиться.")
    await message.answer("Теперь пришли свою фотографию:")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await message.answer("Спасибо! Ещё секунду 😊")

    # Скачиваем фото
    photo_size = message.photo[-1]
    file = await bot.get_file(photo_size.file_id)
    bio = BytesIO()
    await bot.download_file(file.file_path, bio)
    bio.seek(0)

    # Загружаем шаблон
    if not os.path.exists("templates/template.png"):
        await message.answer("Не найден файл templates/template.png (1080×1080). Загрузите шаблон и попробуйте снова.")
        return

    template = Image.open("templates/template.png").convert("RGBA")
    overlay = Image.new('RGBA', template.size, (255, 255, 255, 0))

    # Готовим аватар
    avatar = Image.open(bio).convert("RGBA")
    w, h = avatar.size
    tw, th = 471, 613  # твои размеры кадра

    scale = max(tw / w, th / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    avatar = avatar.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - tw) // 2
    top = (new_h - th) // 2
    avatar = avatar.crop((left, top, left + tw, top + th))

    # Скругление + внутренняя обводка
    mask = Image.new('L', (tw, th), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, tw, th), radius=40, fill=255)
    avatar.putalpha(mask)

    border = Image.new('RGBA', (tw + 4, th + 4), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border)
    bd.rounded_rectangle((0, 0, tw + 2, th + 2), radius=40, outline='#FD693C', width=2)
    border.paste(avatar, (2, 2), avatar)

    # Позиционирование (правый нижний угол с твоими отступами)
    pos = (template.width - 80 - tw, template.height - 377 - th)
    overlay.paste(border, pos, border)
    final = Image.alpha_composite(template, overlay)

    # Подписи
    draw = ImageDraw.Draw(final)
    try:
        name_font = ImageFont.truetype("fonts/GothamPro-Black.ttf", 35)
        comp_font = ImageFont.truetype("fonts/GothamPro-Medium.ttf", 30)
    except Exception:
        # запасной вариант, если шрифты не нашли
        name_font = ImageFont.truetype("arial.ttf", 35)
        comp_font = ImageFont.truetype("arial.ttf", 30)

    uid = message.from_user.id
    full_name = f"{user_data[uid].get('first_name','')} {user_data[uid].get('last_name','')}".strip()
    company = user_data[uid].get('company', '')

    # Белый текст (как у тебя)
    draw.text((pos[0], pos[1] + th + 50), full_name, font=name_font, fill=(255, 255, 255))
    draw.text((pos[0], pos[1] + th + 100), company, font=comp_font, fill=(255, 255, 255))

    # Отправляем картинку
    path = f"invite_{uid}.png"
    final.convert("RGB").save(path, format="PNG")
    await message.answer_photo(photo=FSInputFile(path))

    await message.answer(
        "Чтобы участвовать в розыгрыше VIP билета —\n"
        "Опубликуй картинку в сторис TG, FB или IG, прикрепи ссылку на Timepad (https://digitalclub.timepad.ru/event/3457454/)\n"
    )
    await message.answer(
        "🎁 Победитель будет выбран случайным образом 12 августа.\n\n"
        "Следи за розыгрышем и его результатами в клубе [здесь](https://t.me/+l6rrLeN7Eho3ZjQy)\n\n"
        "Желаем тебе удачи! 🍀",
        parse_m_
