# main.py — Render + webhook (aiogram v3) + stages + accepts photo & image docs + 2s pauses + Sheets safe

import os
import json
import random
import logging
import asyncio
from io import BytesIO

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from PIL import Image, ImageDraw, ImageFont

from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ---------- Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("invite-bot")

# ---------- ENV
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "super_secret_123")
BASE_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("BASE_URL")
PORT = int(os.getenv("PORT", "10000"))

SHEET_ID = os.getenv("SHEET_ID")                 # напр. 1392i1U93gV5...
SHEETS_CREDS_JSON = os.getenv("SHEETS_CREDS_JSON")  # JSON сервисного аккаунта (в одну строку)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Память по пользователю
user_data: dict[int, dict] = {}
referrals: dict[int, list[int]] = {}

TEMPLATE_PATH = "templates/template.png"
FONT_NAME = "fonts/GothamPro-Black.ttf"
FONT_COMP = "fonts/GothamPro-Medium.ttf"

# ---------- Google Sheets helpers ----------
def _normalize_creds(creds_json_str: str) -> dict | None:
    try:
        creds_dict = json.loads(creds_json_str)
    except Exception as e:
        log.exception("Sheets: не удалось распарсить JSON ключа: %s", e)
        return None

    pk = creds_dict.get("private_key", "")
    # Если ключ вставлен как одна строка с символами \n — превратим их в реальные переводы строк
    if "\\n" in pk and "\n" not in pk:
        pk = pk.replace("\\n", "\n")
        creds_dict["private_key"] = pk

    if "BEGIN PRIVATE KEY" not in pk:
        log.error("Sheets: private_key не содержит BEGIN PRIVATE KEY — проверь переменную SHEETS_CREDS_JSON")
        return None
    return creds_dict

def get_worksheet():
    """
    Возвращает sheet1 или None (если переменные не заданы/ключ некорректный).
    Безопасно: не валит бота при ошибках.
    """
    if not (SHEET_ID and SHEETS_CREDS_JSON):
        log.info("Sheets: переменные не заданы (SHEET_ID/SHEETS_CREDS_JSON)")
        return None

    try:
        from google.oauth2.service_account import Credentials
        import gspread

        creds_dict = _normalize_creds(SHEETS_CREDS_JSON)
        if not creds_dict:
            return None

        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        ws = sh.sheet1

        # Автосоздаём заголовки, если лист пустой
        if not ws.get_all_values():
            ws.append_row(["Имя", "Фамилия", "Компания", "ID"])
            log.info("Sheets: добавлена шапка листа")

        return ws

    except Exception as e:
        log.exception("Sheets: ошибка подключения: %s", e)
        return None

def save_guest_to_sheets(user_id: int, first_name: str, last_name: str, company: str):
    try:
        ws = get_worksheet()
        if not ws:
            log.info("Sheets: пропустили запись (ws=None)")
            return
        ws.append_row([first_name, last_name, company, str(user_id)])
        log.info("Sheets: записан гость %s %s (%s), id=%s", first_name, last_name, company, user_id)
    except Exception as e:
        log.exception("Sheets: ошибка при записи: %s", e)

# ---------- Common image processing ----------
def make_invite(image_bytes: BytesIO, first_name: str, last_name: str, company: str, uid: int) -> str:
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Не найден шаблон {TEMPLATE_PATH}")

    template = Image.open(TEMPLATE_PATH).convert("RGBA")
    overlay = Image.new('RGBA', template.size, (255, 255, 255, 0))

    # Аватар (471×613), скругление 40, внутренняя обводка #FD693C 2px
    avatar = Image.open(image_bytes).convert("RGBA")
    w, h = avatar.size
    tw, th = 471, 613
    log.info("Original avatar size: %dx%d", w, h)

    scale = max(tw / w, th / h)
    avatar = avatar.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    left = (avatar.width - tw) // 2
    top = (avatar.height - th) // 2
    avatar = avatar.crop((left, top, left + tw, top + th))

    mask = Image.new('L', (tw, th), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, tw, th), radius=40, fill=255)
    avatar.putalpha(mask)

    border = Image.new('RGBA', (tw + 4, th + 4), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border)
    bd.rounded_rectangle((0, 0, tw + 2, th + 2), radius=40, outline='#FD693C', width=2)
    border.paste(avatar, (2, 2), avatar)

    # Позиционирование (правый нижний угол с отступами)
    pos = (template.width - 80 - tw, template.height - 377 - th)
    overlay.paste(border, pos, border)
    final = Image.alpha_composite(template, overlay)

    # Подписи
    draw = ImageDraw.Draw(final)
    try:
        name_font = ImageFont.truetype(FONT_NAME, 35)
        comp_font = ImageFont.truetype(FONT_COMP, 30)
    except Exception:
        name_font = ImageFont.truetype("arial.ttf", 35)
        comp_font = ImageFont.truetype("arial.ttf", 30)

    full_name = f"{first_name} {last_name}".strip()
    draw.text((pos[0], pos[1] + th + 50), full_name, font=name_font, fill=(255, 255, 255))
    draw.text((pos[0], pos[1] + th + 100), company, font=comp_font, fill=(255, 255, 255))

    path = f"invite_{uid}.png"
    final.convert("RGB").save(path, format="PNG")
    log.info("Invite saved to %s (uid=%s)", path, uid)
    return path

async def download_file_to_memory(file_id: str) -> BytesIO:
    file = await bot.get_file(file_id)
    bio = BytesIO()
    # aiogram v3: bot.download(file, destination=...)
    await bot.download(file, destination=bio)
    bio.seek(0)
    log.info("Downloaded bytes: %d", bio.getbuffer().nbytes)
    return bio

# ---------- Handlers ----------
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    text = message.text or ""
    parts = text.split(maxsplit=1)
    args = parts[1] if len(parts) > 1 else None
    inviter_id = int(args) if (args and args.isdigit()) else None

    if inviter_id and inviter_id != user_id:
        referrals.setdefault(inviter_id, [])
        if user_id not in referrals[inviter_id]:
            referrals[inviter_id].append(user_id)
    log.info("START from %s, inviter=%s", user_id, inviter_id)

    if os.path.exists("templates/banner.png"):
        await message.answer_photo(FSInputFile("templates/banner.png"))

    await message.answer(
        "Привет, рады тебя видеть!\n\n"
        "Этот бот поможет оформить красивый инвайт и даёт право участвовать в розыгрыше VIP билета на PRO PARTY от Digital CPA Club. "
        "Вечеринка пройдёт 14 августа в Москве в noorbar.com: кейс-программа, танцы, нетворкинг и коктейли.\n\n"
        "Регистрация: [Timepad](https://digitalclub.timepad.ru/event/3457454/)",
        parse_mode="Markdown",
    )
    await asyncio.sleep(2)
    user_data[user_id] = {"stage": "ask_first"}
    await message.answer("Как тебя зовут?")
    log.info("Stage set to ask_first for %s", user_id)

@dp.message(F.text)
async def text_router(message: types.Message):
    uid = message.from_user.id
    st = user_data.get(uid)
    txt = (message.text or "").strip()
    log.info("TEXT from %s, stage=%s, text=%r", uid, st.get("stage") if st else None, txt)

    if not st:
        user_data[uid] = {"stage": "ask_first"}
        await message.answer("Как тебя зовут?")
        return

    if st["stage"] == "ask_first":
        st["first_name"] = txt
        st["stage"] = "ask_last"
        await message.answer("Какая у тебя фамилия?")
        return

    if st["stage"] == "ask_last":
        st["last_name"] = txt
        st["stage"] = "ask_company"
        await asyncio.sleep(2)
        await message.answer("Из какой компании?")
        return

    if st["stage"] == "ask_company":
        st["company"] = txt
        st["stage"] = "need_photo"
        first = st.get("first_name") or "Гость"
        await asyncio.sleep(2)
        await message.answer(f"{first}, приятно познакомиться.")
        await asyncio.sleep(2)
        await message.answer("Теперь пришли свою фотографию (как изображение, НЕ как файл). Можно и документом, если это картинка.")
        return

    if st["stage"] == "need_photo":
        await message.answer("Жду фото/картинку 🙂")
        return

    user_data[uid] = {"stage": "ask_first"}
    await message.answer("Давай начнём заново. Как тебя зовут?")

# Фото как photo
@dp.message(F.photo)
async def on_photo(message: types.Message):
    await handle_image_message(message, source="photo")

# Изображение как документ (mime image/*)
@dp.message(F.document)
async def on_document(message: types.Message):
    doc = message.document
    if not doc or not (doc.mime_type or "").startswith("image/"):
        return
    await handle_image_message(message, source="document")

async def handle_image_message(message: types.Message, source: str):
    uid = message.from_user.id
    st = user_data.get(uid)
    log.info("IMAGE from %s via %s, stage=%s", uid, source, st.get("stage") if st else None)

    if not st or st.get("stage") != "need_photo":
        await message.answer("Сначала введи имя/фамилию/компанию. Напиши /start, если нужна подсказка.")
        return

    try:
        await message.answer("Спасибо! Ещё секунду 😊")

        # Получаем байты изображения
        if source == "photo":
            photo_size = message.photo[-1]
            image_bytes = await download_file_to_memory(photo_size.file_id)
        else:
            image_bytes = await download_file_to_memory(message.document.file_id)

        # Генерация пригласительного
        path = make_invite(
            image_bytes=image_bytes,
            first_name=st.get('first_name', ''),
            last_name=st.get('last_name', ''),
            company=st.get('company', ''),
            uid=uid
        )

        await asyncio.sleep(2)
        await message.answer_photo(photo=FSInputFile(path))

        await asyncio.sleep(2)
        await message.answer(
            "Чтобы участвовать в розыгрыше VIP билета —\n"
            "Опубликуй картинку в сторис TG, FB или IG, прикрепи ссылку на Timepad (https://digitalclub.timepad.ru/event/3457454/)\n"
        )
        await asyncio.sleep(2)
        await message.answer(
            "🎁 Победитель будет выбран случайным образом 12 августа.\n\n"
            "Следи за розыгрышем и его результатами в
