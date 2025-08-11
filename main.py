# main.py

import logging
import os
import asyncio
import json
import random
from io import BytesIO

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from PIL import Image, ImageDraw, ImageFont

import gspread
from google.oauth2.service_account import Credentials

from keep_alive import keep_alive
keep_alive()

API_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

user_data = {}
referrals = {}

# ✅ Подключение к Google Sheets
SHEET_ID = "1392i1U93gV5FzipUXQ8RN9oP6xcr5i-Obbr4DdWCh84"
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPE)
gs_client = gspread.authorize(creds)
sheet = gs_client.open_by_key(SHEET_ID).sheet1

def save_guest_to_sheets(user_id, first_name, last_name, company):
    try:
        sheet.append_row([first_name, last_name, company, str(user_id)])
    except Exception as e:
        print("Ошибка при записи в таблицу:", e)

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

    banner = FSInputFile("templates/banner.png")
    await message.answer_photo(photo=banner)
    await asyncio.sleep(2)
    await message.answer(
        "Привет, рады тебя видеть!\n\n"
        "Этот бот поможет тебе оформить красивый инвайт, а также даёт право на участие в розыгрыше VIP билета на PRO PARTY от Digital CPA Club. "
        "Вечеринка пройдёт 14 августа в Москве в noorbar.com, с кейс-программой, "
        "танцами, нетворкингом и коктейлями.\n\n"
        "Подробная информация и регистрация на мероприятие "
        "[здесь](https://digitalclub.timepad.ru/event/3457454/)", parse_mode="Markdown"
    )
    await asyncio.sleep(2)
    await message.answer("Как тебя зовут?")
    user_data[user_id] = {}

@dp.message(lambda m: m.from_user.id in user_data and 'first_name' not in user_data[m.from_user.id])
async def get_first_name(message: types.Message):
    user_data[message.from_user.id]['first_name'] = message.text.strip()
    await asyncio.sleep(2)
    await message.answer("Какая у тебя фамилия?")

@dp.message(lambda m: m.from_user.id in user_data and 'last_name' not in user_data[m.from_user.id])
async def get_last_name(message: types.Message):
    user_data[message.from_user.id]['last_name'] = message.text.strip()
    await asyncio.sleep(2)
    await message.answer("Из какой компании?")

@dp.message(lambda m: m.from_user.id in user_data and 'company' not in user_data[m.from_user.id])
async def get_company(message: types.Message):
    user_data[message.from_user.id]['company'] = message.text.strip()
    first = user_data[message.from_user.id]['first_name']
    await asyncio.sleep(2)
    await message.answer(f"{first}, приятно познакомиться.")
    await asyncio.sleep(2)
    await message.answer("Теперь пришли свою фотографию:")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await message.answer("Спасибо! Ещё секунду 😊")

    photo_size = message.photo[-1]
    file = await bot.get_file(photo_size.file_id)
    bio = BytesIO()
    await bot.download_file(file.file_path, bio)
    bio.seek(0)

    template = Image.open("templates/template.png").convert("RGBA")
    overlay = Image.new('RGBA', template.size, (255, 255, 255, 0))

    avatar = Image.open(bio).convert("RGBA")
    w, h = avatar.size
    tw, th = 471, 613

    scale = max(tw / w, th / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    avatar = avatar.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - tw) // 2
    top = (new_h - th) // 2
    avatar = avatar.crop((left, top, left + tw, top + th))

    mask = Image.new('L', (tw, th), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, tw, th), radius=40, fill=255)
    avatar.putalpha(mask)

    border = Image.new('RGBA', (tw + 4, th + 4), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border)
    bd.rounded_rectangle((0, 0, tw + 2, th + 2), radius=40, outline='#FD693C', width=2)
    border.paste(avatar, (2, 2), avatar)

    pos = (template.width - 80 - tw, template.height - 377 - th)
    overlay.paste(border, pos, border)
    final = Image.alpha_composite(template, overlay)

    draw = ImageDraw.Draw(final)
    name_font = ImageFont.truetype("fonts/GothamPro-Black.ttf", 35)
    comp_font = ImageFont.truetype("fonts/GothamPro-Medium.ttf", 30)

    uid = message.from_user.id
    full_name = f"{user_data[uid]['first_name']} {user_data[uid]['last_name']}"
    company = user_data[uid]['company']

    draw.text((pos[0], pos[1] + th + 50), full_name, font=name_font, fill=(255, 255, 255))
    draw.text((pos[0], pos[1] + th + 100), company, font=comp_font, fill=(255, 255, 255))

    path = f"invite_{uid}.png"
    final.convert("RGB").save(path, format="PNG")
    await message.answer_photo(photo=FSInputFile(path))

    await asyncio.sleep(2)
    await message.answer(
        "Чтобы участвовать в розыгрыше VIP билета —\n"
        "Опубликуй картинку в сторис TG, FB или IG, прикрепи ссылку на Timepad (https://digitalclub.timepad.ru/event/3457454/)\n"
    )

    await asyncio.sleep(2)
    await message.answer(
        "🎁 Победитель будет выбран случайным образом 12 августа.\n\n"
        "Следи за розыгрышем и его результатами в клубе [здесь](https://t.me/+l6rrLeN7Eho3ZjQy)\n\n"
        "Желаем тебе удачи! 🍀",
        parse_mode="Markdown"
    )

    await asyncio.sleep(2)
    await message.answer("Поделись приглашением с коллегами по рынку: @proparty_invite_bot")

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Пересоздать картинку", callback_data="retry_photo")]
        ]
    )
    await message.answer("Если хочешь пересоздать — нажми на кнопку", reply_markup=markup)

    save_guest_to_sheets(uid, user_data[uid]['first_name'], user_data[uid]['last_name'], user_data[uid]['company'])

    try:
        os.remove(path)
    except OSError:
        pass

@dp.callback_query(F.data == "retry_photo")
async def retry_photo_handler(callback: CallbackQuery):
    await callback.message.answer("Окей! Отправь новое фото, и мы пересоздадим пригласительный ✨")

@dp.message(Command("whoami"))
async def whoami(message: types.Message):
    await message.answer(f"Твой user_id: {message.from_user.id}")

@dp.message(Command("draw"))
async def draw_winner(message: types.Message):
    admin_ids = [2002200912]
    if message.from_user.id not in admin_ids:
        await message.answer("У тебя нет доступа к розыгрышу.")
        return

    try:
        records = sheet.get_all_records()
    except Exception as e:
        await message.answer("Ошибка доступа к таблице.")
        print("Ошибка Google Sheets:", e)
        return

    if not records:
        await message.answer("Список участников пуст.")
        return

    await message.answer("🎰 Запускаем барабан...")
    await asyncio.sleep(1)

    suspense_list = random.sample(records, min(6, len(records)))
    for r in suspense_list[:-1]:
        await message.answer(f"🌀 {r['Имя']} {r['Фамилия']}...")
        await asyncio.sleep(0.8)

    winner = suspense_list[-1]
    await asyncio.sleep(1.5)
    await message.answer("🥁🥁🥁")
    await asyncio.sleep(1)

    winner_name = f"{winner['Имя']} {winner['Фамилия']}"
    winner_company = winner['Компания']
    winner_id = winner['ID']

    await message.answer(
        f"🎉 Победитель розыгрыша:\n\n"
        f"👑 {winner_name}, {winner_company}\n\n"
        f"🔥 Поздравляем!"
    )

    try:
        await bot.send_message(
            int(winner_id),
            f"🎉 Поздравляем, {winner_name}!\n\n"
            f"Ты выиграл приз от Digital CPA Club 🎁\n"
            f"Скоро с тобой свяжется организатор. До встречи на Pro Party!"
        )
    except Exception as e:
        await message.answer("⚠️ Не удалось отправить личное сообщение победителю.")
        print("Ошибка при отправке сообщения победителю:", e)

@dp.message(Command("mystats"))
async def mystats_handler(message: types.Message):
    uid = message.from_user.id
    invited = referrals.get(uid, [])
    await message.answer(f"Ты пригласил {len(invited)} человек(а).")

async def main():
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
