# main.py — Render + webhook (aiogram v3) + stages + accepts photo & image docs + 2s pauses + Sheets fix

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

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("invite-bot")

# ---------- ENV
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "super_secret_123")
BASE_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("BASE_URL")
PORT = int(os.getenv("PORT", "10000"))

SHEET_ID = os.getenv("SHEET_ID")  # ID таблицы
SHEETS_CREDS_JSON = os.getenv("SHEETS_CREDS_JSON")  # JSON сервисного аккаунта в одну строку

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

user_data: dict[int, dict] = {}
referrals: dict[int, list[int]] = {}

TEMPLATE_PATH = "templates/template.png"
FONT_NAME = "fonts/GothamPro-Black.ttf"
FONT_COMP = "fonts/GothamPro-Medium.ttf"

# ---------- Google Sheets ----------
def get_worksheet():
    if not (SHEET_ID and SHEETS_CREDS_JSON):
        log.info("Sheets: переменные не заданы (SHEET_ID/SHEETS_CREDS_JSON)")
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_dict = json.loads(SHEETS_CREDS_JSON)
        # фиксим переносы строк в ключе
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        return sh.sheet1
    except Exception as e:
        log.exception("Sheets: ошибка при подключении: %s", e)
        return None

def save_guest_to_sheets(user_id: int, first_name: str, last_name: str, company: str):
    try:
        ws = get_worksheet()
        if not ws:
            return
        ws.append_row([first_name, last_name, company, str(user_id)])
        log.info("Sheets: записан гость %s %s (%s), id=%s", first_name, last_name, company, user_id)
    except Exception as e:
        log.exception("Sheets: ошибка при записи: %s", e)

# ---------- Invite generation ----------
def make_invite(image_bytes: BytesIO, first_name: str, last_name: str, company: str, uid: int) -> str:
    template = Image.open(TEMPLATE_PATH).convert("RGBA")
    overlay = Image.new('RGBA', template.size, (255, 255, 255, 0))

    avatar = Image.open(image_bytes).convert("RGBA")
    tw, th = 471, 613
    scale = max(tw / avatar.width, th / avatar.height)
    avatar = avatar.resize((int(avatar.width * scale), int(avatar.height * scale)), Image.LANCZOS)
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

    pos = (template.width - 80 - tw, template.height - 377 - th)
    overlay.paste(border, pos, border)
    final = Image.alpha_composite(template, overlay)

    try:
        name_font = ImageFont.truetype(FONT_NAME, 35)
        comp_font = ImageFont.truetype(FONT_COMP, 30)
    except Exception:
        name_font = ImageFont.truetype("arial.ttf", 35)
        comp_font = ImageFont.truetype("arial.ttf", 30)

    full_name = f"{first_name} {last_name}".strip()
    draw = ImageDraw.Draw(final)
    draw.text((pos[0], pos[1] + th + 50), full_name, font=name_font, fill=(255, 255, 255))
    draw.text((pos[0], pos[1] + th + 100), company, font=comp_font, fill=(255, 255, 255))

    path = f"invite_{uid}.png"
    final.convert("RGB").save(path, format="PNG")
    return path

async def download_file_to_memory(file_id: str) -> BytesIO:
    file = await bot.get_file(file_id)
    bio = BytesIO()
    await bot.download(file, destination=bio)
    bio.seek(0)
    return bio

# ---------- Handlers ----------
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    uid = message.from_user.id
    if os.path.exists("templates/banner.png"):
        await message.answer_photo(FSInputFile("templates/banner.png"))
    await message.answer(
        "Привет, рады тебя видеть!\n\n"
        "Этот бот поможет оформить красивый инвайт и участвовать в розыгрыше VIP билета на PRO PARTY 🎉",
    )
    await asyncio.sleep(2)
    user_data[uid] = {"stage": "ask_first"}
    await message.answer("Как тебя зовут?")

@dp.message(F.text)
async def text_router(message: types.Message):
    uid = message.from_user.id
    st = user_data.get(uid)
    txt = (message.text or "").strip()

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
        await asyncio.sleep(2)
        await message.answer(f"{st['first_name']}, приятно познакомиться.")
        await asyncio.sleep(2)
        await message.answer("Теперь пришли свою фотографию (как изображение, НЕ как файл).")
        return

    if st["stage"] == "need_photo":
        await message.answer("Жду фото как изображение 🙂")
        return

@dp.message(F.photo)
async def on_photo(message: types.Message):
    await handle_image_message(message, source="photo")

@dp.message(F.document)
async def on_document(message: types.Message):
    if (message.document.mime_type or "").startswith("image/"):
        await handle_image_message(message, source="document")

async def handle_image_message(message: types.Message, source: str):
    uid = message.from_user.id
    st = user_data.get(uid)

    if not st or st.get("stage") != "need_photo":
        await message.answer("Сначала введи имя, фамилию и компанию. Напиши /start.")
        return

    try:
        await message.answer("Спасибо! Ещё секунду 😊")
        img_bytes = await download_file_to_memory(
            message.photo[-1].file_id if source == "photo" else message.document.file_id
        )
        path = make_invite(img_bytes, st["first_name"], st["last_name"], st["company"], uid)
        await asyncio.sleep(2)
        await message.answer_photo(photo=FSInputFile(path))
        save_guest_to_sheets(uid, st["first_name"], st["last_name"], st["company"])
        os.remove(path)
        user_data[uid] = {"stage": "ask_first"}
    except Exception as e:
        log.exception("Ошибка обработки: %s", e)
        await message.answer("Не удалось обработать фото. Пришли другое.")

# ---------- Webhook ----------
async def on_startup(app: web.Application):
    assert BASE_URL, "BASE_URL не задан"
    await bot.set_webhook(BASE_URL.rstrip("/") + "/webhook", secret_token=WEBHOOK_SECRET)

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

def build_app():
    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="OK"))  # healthcheck fix
    SimpleRequestHandler(dp, bot, secret_token=WEBHOOK_SECRET).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=PORT)
