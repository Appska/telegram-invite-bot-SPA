# main.py — Render + webhook (aiogram v3) + stages + verbose logging + Google Sheets

import os
import json
import random
import logging
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
SHEETS_CREDS_JSON = os.getenv("SHEETS_CREDS_JSON")  # весь JSON сервисного аккаунта в одну строку

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Память по пользователю
user_data: dict[int, dict] = {}
referrals: dict[int, list[int]] = {}

# ---------- Google Sheets helpers ----------
def get_worksheet():
    if not (SHEET_ID and SHEETS_CREDS_JSON):
        log.info("Sheets: переменные не заданы (SHEET_ID/SHEETS_CREDS_JSON)")
        return None
    import gspread
    from google.oauth2.service_account import Credentials

    creds_dict = json.loads(SHEETS_CREDS_JSON)
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

def save_guest_to_sheets(user_id: int, first_name: str, last_name: str, company: str):
    try:
        ws = get_worksheet()
        if not ws:
            return
        ws.append_row([first_name, last_name, company, str(user_id)])
        log.info("Sheets: записан гость %s %s (%s), id=%s", first_name, last_name, company, user_id)
    except Exception as e:
        log.exception("Sheets: ошибка при записи: %s", e)

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
        log.info("No state → reset to ask_first for %s", uid)
        return

    if st["stage"] == "ask_first":
        st["first_name"] = txt
        st["stage"] = "ask_last"
        await message.answer("Какая у тебя фамилия?")
        log.info("ask_first → ask_last for %s, first_name=%r", uid, txt)
        return

    if st["stage"] == "ask_last":
        st["last_name"] = txt
        st["stage"] = "ask_company"
        await message.answer("Из какой компании?")
        log.info("ask_last → ask_company for %s, last_name=%r", uid, txt)
        return

    if st["stage"] == "ask_company":
        st["company"] = txt
        st["stage"] = "need_photo"
        first = st.get("first_name") or "Гость"
        await message.answer(f"{first}, приятно познакомиться.")
        await message.answer("Теперь пришли свою фотографию (как изображение, НЕ как файл).")
        log.info("ask_company → need_photo for %s, company=%r", uid, txt)
        return

    if st["stage"] == "need_photo":
        await message.answer("Жду фото как изображение 🙂")
        log.info("Received text but need photo (uid=%s)", uid)
        return

    # запасной случай
    user_data[uid] = {"stage": "ask_first"}
    await message.answer("Давай начнём заново. Как тебя зовут?")
    log.info("Fallback reset for %s", uid)

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    uid = message.from_user.id
    st = user_data.get(uid)
    log.info("PHOTO from %s, stage=%s", uid, st.get("stage") if st else None)

    if not st or st.get("stage") != "need_photo":
        await message.answer("Сначала введи имя/фамилию/компанию. Напиши /start, если нужна подсказка.")
        log.info("Photo ignored: wrong stage (uid=%s)", uid)
        return

    try:
        await message.answer("Спасибо! Ещё секунду 😊")

        # Скачиваем фото (aiogram v3)
        photo_size = message.photo[-1]
        file = await bot.get_file(photo_size.file_id)
        bio = BytesIO()
        await bot.download(file, destination=bio)
        bio.seek(0)
        log.info("Downloaded photo for %s, size=%d bytes", uid, bio.getbuffer().nbytes)

        # Шаблон
        tpl_path = "templates/template.png"
        if not os.path.exists(tpl_path):
            await message.answer("Не найден файл templates/template.png (1080×1080). Загрузите шаблон и попробуйте снова.")
            log.error("Template not found at %s", tpl_path)
            return

        template = Image.open(tpl_path).convert("RGBA")
        overlay = Image.new('RGBA', template.size, (255, 255, 255, 0))

        # Аватар (471×613), скругление 40, внутренняя обводка #FD693C 2px
        avatar = Image.open(bio).convert("RGBA")
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

        # Позиционирование
        pos = (template.width - 80 - tw, template.height - 377 - th)
        overlay.paste(border, pos, border)
        final = Image.alpha_composite(template, overlay)

        # Подписи
        draw = ImageDraw.Draw(final)
        try:
            name_font = ImageFont.truetype("fonts/GothamPro-Black.ttf", 35)
            comp_font = ImageFont.truetype("fonts/GothamPro-Medium.ttf", 30)
        except Exception:
            name_font = ImageFont.truetype("arial.ttf", 35)
            comp_font = ImageFont.truetype("arial.ttf", 30)

        full_name = f"{st.get('first_name','')} {st.get('last_name','')}".strip()
        company = st.get('company', '')
        draw.text((pos[0], pos[1] + th + 50), full_name, font=name_font, fill=(255, 255, 255))
        draw.text((pos[0], pos[1] + th + 100), company, font=comp_font, fill=(255, 255, 255))

        path = f"invite_{uid}.png"
        final.convert("RGB").save(path, format="PNG")
        log.info("Invite saved to %s (uid=%s)", path, uid)
        await message.answer_photo(photo=FSInputFile(path))

        # Инструкции
        await message.answer(
            "Чтобы участвовать в розыгрыше VIP билета —\n"
            "Опубликуй картинку в сторис TG, FB или IG, прикрепи ссылку на Timepad (https://digitalclub.timepad.ru/event/3457454/)\n"
        )
        await message.answer(
            "🎁 Победитель будет выбран случайным образом 12 августа.\n\n"
            "Следи за розыгрышем и его результатами в клубе [здесь](https://t.me/+l6rrLeN7Eho3ZjQy)\n\n"
            "Желаем тебе удачи! 🍀",
            parse_mode="Markdown",
        )
        await message.answer("Поделись приглашением с коллегами по рынку: @proparty_invite_bot")

        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔄 Пересоздать картинку", callback_data="retry_photo")]]
        )
        await message.answer("Если хочешь пересоздать — нажми на кнопку", reply_markup=markup)

        # Запись в таблицу
        save_guest_to_sheets(uid, st.get('first_name',''), st.get('last_name',''), company)

        # очистим временный файл и сбросим сценарий
        try:
            os.remove(path)
        except OSError:
            pass
        user_data[uid] = {"stage": "ask_first"}
        log.info("Flow done, reset stage for %s", uid)

    except Exception as e:
        log.exception("Ошибка обработки фото (uid=%s): %s", uid, e)
        await message.answer("Ой! Фото не обработалось. Попробуй ещё раз или пришли другое изображение.")

@dp.callback_query(F.data == "retry_photo")
async def retry_photo_handler(callback: CallbackQuery):
    user_data[callback.from_user.id] = {"stage": "need_photo"}
    await callback.message.answer("Окей! Отправь новое фото, и мы пересоздадим пригласительный ✨")
    log.info("Retry requested by %s → stage need_photo", callback.from_user.id)

@dp.message(Command("whoami"))
async def whoami(message: types.Message):
    await message.answer(f"Твой user_id: {message.from_user.id}")

@dp.message(Command("draw"))
async def draw_winner(message: types.Message):
    admin_ids = [2002200912]
    if message.from_user.id not in admin_ids:
        await message.answer("У тебя нет доступа к розыгрышу.")
        return

    ws = get_worksheet()
    if not ws:
        await message.answer("Google Sheets не настроен.")
        return

    try:
        records = ws.get_all_records()
    except Exception as e:
        await message.answer("Ошибка доступа к таблице.")
        log.exception("Sheets: %s", e)
        return

    if not records:
        await message.answer("Список участников пуст.")
        return

    await message.answer("🎰 Запускаем барабан...")
    suspense_list = random.sample(records, min(6, len(records)))
    for r in suspense_list[:-1]:
        fn = r.get('Имя') or r.get('first_name') or ''
        ln = r.get('Фамилия') or r.get('last_name') or ''
        await message.answer(f"🌀 {fn} {ln}...")
    winner = suspense_list[-1]
    fn = winner.get('Имя') or winner.get('first_name') or ''
    ln = winner.get('Фамилия') or winner.get('last_name') or ''
    company = winner.get('Компания') or winner.get('company') or ''
    win_id = winner.get('ID') or winner.get('id') or ''

    await message.answer(f"🎉 Победитель:\n\n👑 {fn} {ln}, {company}\n\n🔥 Поздравляем!")
    if win_id:
        try:
            await bot.send_message(int(win_id), f"🎉 Поздравляем, {fn} {ln}! Ты выиграл приз от Digital CPA Club 🎁")
        except Exception as e:
            await message.answer("⚠️ Не удалось отправить личное сообщение победителю.")
            log.exception("Ошибка при отправке победителю: %s", e)

# ---------- Webhook bootstrapping ----------
async def on_startup(app: web.Application):
    assert BASE_URL, "BASE_URL/RENDER_EXTERNAL_URL не задан"
    url = BASE_URL.rstrip("/") + "/webhook"
    await bot.set_webhook(url=url, secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    log.info("Webhook set to %s", url)

async def on_shutdown(app: web.Application):
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    finally:
        await bot.session.close()

def build_app():
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=PORT)
