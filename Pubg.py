import os
import logging
import sqlite3
import datetime
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton, 
                           InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, FSInputFile)

# --- KONFIGURATSIYA ---
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
# DB_NAME
DB_NAME = os.getenv("DB_NAME", "bot_database_pubg_uc.db")

# REBRANDING: UC Cash
CURRENCY_NAME = os.getenv("CURRENCY_NAME", "UC") # 💎
CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "💎")

# Karta ma'lumotlari (Environmentdan yoki default)
CARD_UZS = os.getenv("CARD_UZS", "5614686817322558")
CARD_NAME = os.getenv("CARD_NAME", "Sayfullayev Sherali")
CARD_VISA = os.getenv("CARD_VISA", "4176550026725055")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH ---
def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if commit: conn.commit()
            if fetchone: return cursor.fetchone()
            if fetchall: return cursor.fetchall()
            return None
    except Exception as e:
        logging.error(f"Bazada xatolik: {e}")
        return None

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (id INTEGER PRIMARY KEY, 
                           balance REAL DEFAULT 0.0,
                           status_level INTEGER DEFAULT 0,
                           status_expire TEXT,
                           referrer_id INTEGER,
                           joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS config 
                          (key TEXT PRIMARY KEY, value TEXT)''')
        
        # 'projects' jadvali saqlanadi, lekin funksiyalarda 'Akkountlar' deb yuritiladi
        cursor.execute('''CREATE TABLE IF NOT EXISTS projects 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                           name TEXT, 
                           price REAL, 
                           description TEXT,
                           media_id TEXT,
                           media_type TEXT,
                           file_id TEXT)''')
                           
        cursor.execute('''CREATE TABLE IF NOT EXISTS uc_packages
                          (id INTEGER PRIMARY KEY AUTOINCREMENT,
                           uc_amount INTEGER,
                           uzs_price REAL,
                           usd_price REAL)''')
        conn.commit()
    
    # Migratsiyalar (avvalgidek qoldi)
    columns_users = ["status_level", "referrer_id", "joined_at"]
    for col in columns_users:
        try: db_query(f"ALTER TABLE users ADD COLUMN {col} INTEGER" if col == "status_level" or col == "referrer_id" else f"ALTER TABLE users ADD COLUMN {col} TEXT", commit=True)
        except: pass
    
    columns_projects = ["description", "media_id", "media_type", "file_id"]
    for col in columns_projects:
        try: db_query(f"ALTER TABLE projects ADD COLUMN {col} TEXT", commit=True)
        except: pass

init_db()

# --- SOZLAMALAR ---
def get_config(key, default_value):
    res = db_query("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
    if res: return res[0]
    db_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(default_value)), commit=True)
    return str(default_value)

def set_config(key, value):
    db_query("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)), commit=True)

# Status darajalari
STATUS_DATA = {
    0: {"name": "👤 Start", "limit": 30, "price_month": 0},
    1: {"name": "🥈 Silver", "limit": 100, "desc": f"✅ Clicker (Pul ishlash)\n✅ Limit: 100 {CURRENCY_SYMBOL}"}, 
    # Loyihalar -> Akkountlar
    2: {"name": "🥇 Gold", "limit": 1000, "desc": f"✅ Akkountlar 50% chegirma\n✅ Limit: 1000 {CURRENCY_SYMBOL}"}, 
    3: {"name": "💎 Platinum", "limit": 100000, "desc": f"✅ Hammasi TEKIN (Xizmatlar ham)\n✅ Limit: 100000 {CURRENCY_SYMBOL}"} 
}

def get_dynamic_prices():
    return {
        "ref_reward": float(get_config("ref_reward", 1.0)),
        "click_reward": float(get_config("click_reward", 0.05)),
        # Status narxlari (Oyiga)
        "pro_price": float(get_config("status_price_1", 20.0)),  # Silver
        "prem_price": float(get_config("status_price_2", 50.0)), # Gold
        "king_price": float(get_config("status_price_3", 200.0)) # Platinum
    }

def get_coin_rates():
    return {
        "uzs": float(get_config("rate_uzs", 1000.0)), 
        "usd": float(get_config("rate_usd", 0.1))
    }

def get_text(key, default):
    # Loyiha/Loyihalar so'zlarini Akkount/Akkountlar ga almashtirish
    modified_default = default.replace("UzCoin", CURRENCY_SYMBOL).replace("COIN", CURRENCY_SYMBOL).replace("UZC", CURRENCY_SYMBOL).replace("SultanCoin", CURRENCY_SYMBOL)
    modified_default = modified_default.replace("Loyihalar", "Akkountlar").replace("Loyiha", "Akkount")

    res = get_config(f"text_{key}", modified_default).replace("\\n", "\n")
    res = res.replace("UzCoin", CURRENCY_SYMBOL).replace("COIN", CURRENCY_SYMBOL).replace("UZC", CURRENCY_SYMBOL).replace("SultanCoin", CURRENCY_SYMBOL)
    res = res.replace("Loyihalar", "Akkountlar").replace("Loyiha", "Akkount")
    return res

def get_user_data(user_id):
    res = db_query("SELECT balance, status_level, status_expire FROM users WHERE id = ?", (user_id,), fetchone=True)
    if not res: return None
    
    balance, level, expire = res
    if expire:
        expire_dt = datetime.datetime.strptime(expire, "%Y-%m-%d %H:%M:%S")
        if datetime.datetime.now() > expire_dt:
            db_query("UPDATE users SET status_level = 0, status_expire = NULL WHERE id = ?", (user_id,), commit=True)
            level = 0
            expire = None
    return {"balance": balance, "level": level, "expire": expire}

def format_num(num):
    return f"{float(num):.2f}".rstrip('0').rstrip('.')

# --- STATES ---
class AdminState(StatesGroup):
    edit_balance_id = State()
    edit_balance_amount = State()
    
    # Akkount qo'shish (Loyihalar)
    add_proj_name = State()
    add_proj_price = State()
    add_proj_desc = State()
    add_proj_media = State()
    add_proj_file = State()

    # Yangi: Akkount tahrirlash
    edit_proj_select = State()
    edit_proj_name = State()
    edit_proj_price = State()
    edit_proj_desc = State()
    edit_proj_media = State()
    edit_proj_file = State()

    # Yangi: UC tahrirlash
    edit_uc_select = State()
    edit_uc_amount = State()
    edit_uc_uzs = State()
    edit_uc_usd = State()

    change_config_value = State()
    edit_text_key = State()
    edit_text_val = State()
    broadcast_msg = State() 
    
    # UC to'plamlari uchun
    add_uc_amount = State()
    add_uc_uzs = State()
    add_uc_usd = State()
    
class UcOrder(StatesGroup):
    choosing_uc = State()
    waiting_for_id = State()

class FillBalance(StatesGroup):
    choosing_currency = State()
    waiting_for_amount = State()
    waiting_for_receipt = State()

class MoneyTransfer(StatesGroup):
    waiting_for_recipient = State()
    waiting_for_amount = State()
    confirm = State()

# --- KEYBOARDS ---
def main_menu(user_id):
    kb = [
        [KeyboardButton(text="👤 Kabinet"), KeyboardButton(text="🌟 Statuslar")],
        # Loyihalar -> Akkountlar
        [KeyboardButton(text="💎 UC Sotib olish"), KeyboardButton(text="📂 Akkountlar")], 
        [KeyboardButton(text="💳 Hisobni to'ldirish"), KeyboardButton(text="💸 Pul ishlash")],
        [KeyboardButton(text="🏆 Top Foydalanuvchilar")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚫 Bekor qilish")]], resize_keyboard=True)

def edit_proj_kb(pid):
    kb = [
        [InlineKeyboardButton(text="✏️ Nomini tahrirlash", callback_data=f"ep_name:{pid}"),
         InlineKeyboardButton(text="💰 Narxini tahrirlash", callback_data=f"ep_price:{pid}")],
        [InlineKeyboardButton(text="📝 Tavsifini tahrirlash", callback_data=f"ep_desc:{pid}")],
        [InlineKeyboardButton(text="🖼 Rasmini/Videosini tahrirlash", callback_data=f"ep_media:{pid}")],
        [InlineKeyboardButton(text="📁 Faylini tahrirlash", callback_data=f"ep_file:{pid}")],
        [InlineKeyboardButton(text="❌ Akkountni butunlay o'chirish", callback_data=f"ep_delete:{pid}")],
        [InlineKeyboardButton(text="⬅️ Ortga", callback_data="adm_manage_proj")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def edit_uc_kb(pid):
    kb = [
        [InlineKeyboardButton(text="✏️ UC Miqdorini tahrirlash", callback_data=f"eu_amount:{pid}")],
        [InlineKeyboardButton(text="💰 UZS Narxini tahrirlash", callback_data=f"eu_uzs:{pid}"),
         InlineKeyboardButton(text="💵 USD Narxini tahrirlash", callback_data=f"eu_usd:{pid}")],
        [InlineKeyboardButton(text="❌ To'plamni butunlay o'chirish", callback_data=f"eu_delete:{pid}")],
        [InlineKeyboardButton(text="⬅️ Ortga", callback_data="adm_manage_uc")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# --------------------------------------------------------------------------------
# --- 🔥 MUHIM FIX: BEKOR QILISH HANDLERI (ENG TEPADA) ---
# --------------------------------------------------------------------------------
@dp.message(F.text == "🚫 Bekor qilish", StateFilter("*"))
async def cancel_all_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Bosh menyudasiz.", reply_markup=main_menu(message.from_user.id))
        return

    await state.clear()
    await message.answer("🚫 Jarayon bekor qilindi.", reply_markup=main_menu(message.from_user.id))

# --- START, KABINET, PUL ISHLASH, STATUSLAR, TOP USERLAR --- (O'zgarishsiz)

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    referrer_id = None
    args = command.args
    
    if args and args.isdigit():
        referrer_id = int(args)
        if referrer_id == message.from_user.id: referrer_id = None
    
    if not db_query("SELECT id FROM users WHERE id = ?", (message.from_user.id,), fetchone=True):
        db_query("INSERT INTO users (id, balance, referrer_id) VALUES (?, 0.0, ?)", 
                 (message.from_user.id, referrer_id), commit=True)
        
        if referrer_id:
            reward = get_dynamic_prices()['ref_reward']
            db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, referrer_id), commit=True)
            try:
                await bot.send_message(referrer_id, f"🎉 Sizda yangi referal! +{format_num(reward)} {CURRENCY_SYMBOL}")
            except: pass

    welcome_text = get_text("welcome", 
                            f"👋 **Assalomu alaykum, {message.from_user.full_name}!**\n\n"
                            f"🤖 **SULTANOV Official Bot**ga xush kelibsiz.\n"
                            f"Bu yerda siz xizmatlardan foydalanishingiz va {CURRENCY_NAME} ishlashingiz mumkin.")
    
    await message.answer(welcome_text, reply_markup=main_menu(message.from_user.id), parse_mode="Markdown")

@dp.message(F.text == "👤 Kabinet")
async def kabinet(message: types.Message):
    data = get_user_data(message.from_user.id)
    status_name = STATUS_DATA[data['level']]['name']
    limit = STATUS_DATA[data['level']]['limit']
    
    msg = (f"🆔 Sizning ID: `{message.from_user.id}`\n"
           f"💰 Balans: **{format_num(data['balance'])} {CURRENCY_SYMBOL}**\n"
           f"📊 Status: {status_name}\n"
           f"💳 O'tkazma limiti: {limit} {CURRENCY_SYMBOL}")
    
    if data['expire']:
        msg += f"\n⏳ Tugash vaqti: `{data['expire']}`"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💸 Do'stga o'tkazish", callback_data="transfer_start")]])
    await message.answer(msg, reply_markup=kb, parse_mode="Markdown")

@dp.message(F.text == "💸 Pul ishlash")
async def earn_money(message: types.Message):
    user = get_user_data(message.from_user.id)
    prices = get_dynamic_prices()
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={message.from_user.id}"
    
    msg = (f"🔗 **Referal havolangiz:**\n`{ref_link}`\n\n"
           f"👤 Har bir taklif uchun: **{format_num(prices['ref_reward'])} {CURRENCY_SYMBOL}**\n"
           f"ℹ️ Do'stingiz botga kirib start bossa kifoya.")
    
    kb_rows = []
    if user['level'] >= 1:
        msg += f"\n\n🥈 **Silver Clicker** faol!\nHar bosishda: {format_num(prices['click_reward'])} {CURRENCY_SYMBOL}"
        kb_rows.append([InlineKeyboardButton(text=f"👆 {CURRENCY_NAME} ISHLASH", callback_data="clicker_process")])
    else:
        msg += f"\n\n🔒 **Clicker** yopiq. Kamida Silver status oling!"
        kb_rows.append([InlineKeyboardButton(text="🥈 Status sotib olish", callback_data="open_status_shop")])
        
    await message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="Markdown")

@dp.callback_query(F.data == "clicker_process")
async def process_click(callback: types.CallbackQuery):
    user = get_user_data(callback.from_user.id)
    if user['level'] < 1:
        return await callback.answer("Faqat Silver va yuqori statusdagilar uchun!", show_alert=True)
    
    reward = get_dynamic_prices()['click_reward']
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, callback.from_user.id), commit=True)
    await callback.answer(f"+{format_num(reward)} {CURRENCY_SYMBOL}", cache_time=1)

@dp.message(F.text == "🌟 Statuslar")
async def status_shop(message: types.Message):
    await show_status_menu(message)

@dp.callback_query(F.data == "open_status_shop")
async def cb_status_shop(callback: types.CallbackQuery):
    await show_status_menu(callback.message)

async def show_status_menu(message: types.Message):
    prices = get_dynamic_prices()
    kb = [
        [InlineKeyboardButton(text=f"🥈 Silver ({prices['pro_price']} {CURRENCY_SYMBOL})", callback_data="buy_status_1")], 
        [InlineKeyboardButton(text=f"🥇 Gold ({prices['prem_price']} {CURRENCY_SYMBOL})", callback_data="buy_status_2")], 
        [InlineKeyboardButton(text=f"💎 Platinum ({prices['king_price']} {CURRENCY_SYMBOL})", callback_data="buy_status_3")] 
    ]
    
    info = (f"**🌟 STATUSLAR VA IMKONIYATLAR:**\n\n"
            f"🥈 **SILVER** - {prices['pro_price']} {CURRENCY_SYMBOL}\n{STATUS_DATA[1]['desc']}\n\n"
            f"🥇 **GOLD** - {prices['prem_price']} {CURRENCY_SYMBOL}\n{STATUS_DATA[2]['desc']}\n\n"
            f"💎 **PLATINUM** - {prices['king_price']} {CURRENCY_SYMBOL}\n{STATUS_DATA[3]['desc']}")
    
    if isinstance(message, types.CallbackQuery):
        await message.message.edit_text(info, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    else:
        await message.answer(info, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("buy_status_"))
async def buy_status_handler(callback: types.CallbackQuery):
    lvl = int(callback.data.split("_")[-1])
    prices = get_dynamic_prices()
    price_map = {1: prices['pro_price'], 2: prices['prem_price'], 3: prices['king_price']}
    cost = price_map[lvl]
    
    user = get_user_data(callback.from_user.id)
    
    if user['level'] >= lvl:
        return await callback.answer("Sizda allaqachon bu yoki undan yuqori status bor!", show_alert=True)
    
    if user['balance'] < cost:
        return await callback.answer(f"Hisobingizda mablag' yetarli emas! Kerak: {cost} {CURRENCY_SYMBOL}", show_alert=True)
    
    expire_date = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    
    db_query("UPDATE users SET balance = balance - ?, status_level = ?, status_expire = ? WHERE id = ?", 
             (cost, lvl, expire_date, callback.from_user.id), commit=True)
    
    await callback.message.delete()
    await callback.message.answer(f"🎉 **Tabriklaymiz!**\nSiz **{STATUS_DATA[lvl]['name']}** statusini sotib oldingiz!\nBarcha imkoniyatlar ochildi.")

@dp.message(F.text == "🏆 Top Foydalanuvchilar")
async def top_users(message: types.Message):
    users = db_query("SELECT id, balance, status_level FROM users ORDER BY balance DESC LIMIT 10", fetchall=True)
    msg = f"🏆 **{CURRENCY_NAME} MILLIONERLARI:**\n\n"
    
    for idx, (uid, bal, lvl) in enumerate(users, 1):
        badge = ""
        if lvl == 1: badge = "🥈"
        elif lvl == 2: badge = "🥇"
        elif lvl == 3: badge = "💎"
        
        # ID ni qisman yashirish (Professionalism)
        hidden_id = str(uid)[:4] + "..." + str(uid)[-2:]
        msg += f"{idx}. {badge} ID: `{hidden_id}` — **{format_num(bal)} {CURRENCY_SYMBOL}**\n"
        
    await message.answer(msg, parse_mode="Markdown")

# --- AKKOUNTLAR (LOYIHALAR) ---
@dp.message(F.text == "📂 Akkountlar")
async def show_projects(message: types.Message):
    projs = db_query("SELECT id, name FROM projects", fetchall=True)
    # Loyihalar -> Akkountlar
    if not projs: return await message.answer("📂 Hozircha akkountlar yuklanmagan.") 
    
    kb = []
    for pid, name in projs:
        # Loyiha -> Akkount
        kb.append([InlineKeyboardButton(text=f"📁 {name} Akkounti", callback_data=f"view_proj_{pid}")]) 
    # Loyihani -> Akkountni
    await message.answer("📥 Kerakli akkountni tanlang va yuklab oling:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("view_proj_"))
async def view_project(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    proj = db_query("SELECT name, price, description, media_id, media_type FROM projects WHERE id = ?", (pid,), fetchone=True)
    
    # Loyiha -> Akkount
    if not proj: return await callback.answer("Akkount topilmadi.", show_alert=True) 
    name, price, desc, mid, mtype = proj
    
    user = get_user_data(callback.from_user.id)
    discount = 0
    if user['level'] == 2: discount = 0.5
    elif user['level'] == 3: discount = 1.0
    
    final_price = price * (1 - discount)
    
    price_text = f"{format_num(price)} {CURRENCY_SYMBOL}"
    if discount > 0:
        price_text = f"~{format_num(price)}~ -> **{format_num(final_price)} {CURRENCY_SYMBOL}**"
        if final_price == 0: price_text = "**TEKIN (Status)**"
    
    caption = f"📂 **{name} Akkounti**\n\n📝 {desc}\n\n💰 Narxi: {price_text}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Sotib olish / Yuklash", callback_data=f"buy_proj_{pid}")]])
    
    try:
        if mid:
            if mtype == 'video':
                await bot.send_video(callback.message.chat.id, mid, caption=caption, reply_markup=kb, parse_mode="Markdown")
            elif mtype == 'photo':
                await bot.send_photo(callback.message.chat.id, mid, caption=caption, reply_markup=kb, parse_mode="Markdown")
            else:
                await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
        else:
            await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_proj_"))
async def buy_project_process(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    proj = db_query("SELECT price, file_id, name FROM projects WHERE id = ?", (pid,), fetchone=True)
    if not proj: return
    price, file_id, name = proj
    
    user = get_user_data(callback.from_user.id)
    discount = 0
    if user['level'] == 2: discount = 0.5
    elif user['level'] == 3: discount = 1.0
    
    final_price = price * (1 - discount)
    
    if user['balance'] < final_price:
        return await callback.answer(f"Mablag' yetarli emas! Kerak: {format_num(final_price)} {CURRENCY_SYMBOL}", show_alert=True)
        
    if final_price > 0:
        db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (final_price, callback.from_user.id), commit=True)
        await callback.message.answer(f"✅ Xarid amalga oshdi! Hisobdan {format_num(final_price)} {CURRENCY_SYMBOL} yechildi.")
    
    # Loyiha -> Akkount
    await bot.send_document(callback.message.chat.id, file_id, caption=f"✅ **{name} Akkounti**\n\nFaylni muvaffaqiyatli yuklab oldingiz!")
    await callback.answer()

# --- UC SOTIB OLISH --- (O'zgarishsiz)

@dp.message(F.text == "💎 UC Sotib olish")
async def uc_buy_start(message: types.Message, state: FSMContext):
    packages = db_query("SELECT id, uc_amount, uzs_price, usd_price FROM uc_packages ORDER BY uc_amount ASC", fetchall=True)
    if not packages: return await message.answer("⚠️ Hozircha UC to'plamlari yuklanmagan. Admin panelini tekshiring.")
    
    kb = []
    msg = f"💎 **UC To'plamlarini Tanlang:**\n\n"
    
    for pid, uc_amt, uzs_p, usd_p in packages:
        msg += f"🔥 **{uc_amt} UC**\n💰 Narxi: **{uzs_p:,.0f} UZS** / **{usd_p:.2f} USD**\n\n"
        kb.append([InlineKeyboardButton(text=f"{uc_amt} UC", callback_data=f"uc_buy:{pid}")])
        
    await message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    await state.set_state(UcOrder.choosing_uc)

@dp.callback_query(F.data.startswith("uc_buy:"))
async def uc_buy_select(callback: types.CallbackQuery, state: FSMContext):
    pid = int(callback.data.split(":")[1])
    package = db_query("SELECT uc_amount, uzs_price, usd_price FROM uc_packages WHERE id = ?", (pid,), fetchone=True)
    if not package: return await callback.answer("To'plam topilmadi.", show_alert=True)
    
    uc_amount, uzs_price, usd_price = package
    
    await state.update_data(uc_pid=pid, uc_amount=uc_amount, uzs_price=uzs_price, usd_price=usd_price)
    
    await callback.message.answer(f"✅ **{uc_amount} UC** tanlandi!\n\n"
                                  f"🎮 Iltimos, **PUBG ID raqamingizni** kiriting:", reply_markup=cancel_kb())
    await state.set_state(UcOrder.waiting_for_id)
    await callback.answer()

@dp.message(UcOrder.waiting_for_id)
async def uc_buy_confirm(message: types.Message, state: FSMContext):
    player_id = message.text.strip()
    if not player_id.isdigit(): 
        return await message.answer("⚠️ Iltimos, faqat raqamlardan iborat to'g'ri PUBG ID kiriting!")

    data = await state.get_data()
    
    admin_message = (f"🎮 **YANGI UC BUYURTMA!**\n"
                     f"👤 User: ID `{message.from_user.id}` (@{message.from_user.username or 'yoq'})\n"
                     f"💰 UC Miqdori: **{data['uc_amount']} UC**\n"
                     f"💳 Narxi: **{data['uzs_price']:,.0f} UZS** / **{data['usd_price']:.2f} USD**\n"
                     f"🎯 PUBG ID: `{player_id}`")
                     
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ UC ni Jo'natdim", callback_data=f"uc_sent:{message.from_user.id}:{data['uc_amount']}"),
         InlineKeyboardButton(text="❌ Rad etish", callback_data=f"uc_reject:{message.from_user.id}")]
    ])

    await bot.send_message(ADMIN_ID, admin_message, reply_markup=kb, parse_mode="Markdown")
    
    await message.answer("✅ Buyurtmangiz qabul qilindi. Tez orada admin UC ni hisobingizga yuklaydi!", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.callback_query(F.data.startswith("uc_sent:"))
async def uc_sent_approve(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    uid, uc_amt = int(parts[1]), int(parts[2])
    
    try:
        await bot.send_message(uid, f"✅ **UC Muvaffaqiyatli Yuklandi!**\nHisobingizga {uc_amt} UC qo'shildi.")
    except: pass
    await callback.message.edit_text(callback.message.text + "\n\n✅ UC YUKLANDI. TASDIQLANDI.")

@dp.callback_query(F.data.startswith("uc_reject:"))
async def uc_sent_reject(callback: types.CallbackQuery):
    uid = int(callback.data.split(":")[1])
    try:
        await bot.send_message(uid, "❌ UC buyurtmangiz rad etildi. Iltimos, admin bilan bog'laning (ID xato bo'lishi mumkin).")
    except: pass
    await callback.message.edit_text(callback.message.text + "\n\n❌ RAD ETILDI.")

# --- PUL O'TKAZISH --- (O'zgarishsiz)

@dp.callback_query(F.data == "transfer_start")
async def transfer_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🆔 Qabul qiluvchining ID raqamini kiriting:", reply_markup=cancel_kb())
    await state.set_state(MoneyTransfer.waiting_for_recipient)

@dp.message(MoneyTransfer.waiting_for_recipient)
async def transfer_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): 
        return await message.answer("⚠️ Iltimos, faqat raqamlardan iborat ID kiriting!")
    
    rid = int(message.text)
    if rid == message.from_user.id:
        return await message.answer("⚠️ O'zingizga pul o'tkaza olmaysiz!")

    if not db_query("SELECT id FROM users WHERE id = ?", (rid,), fetchone=True):
        return await message.answer("⚠️ Bunday ID ga ega foydalanuvchi topilmadi!")
        
    await state.update_data(rid=rid)
    user = get_user_data(message.from_user.id)
    limit = STATUS_DATA[user['level']]['limit']
    
    await message.answer(f"💰 Qancha **{CURRENCY_NAME}** o'tkazmoqchisiz?\n"
                         f"Sizning balansingiz: {format_num(user['balance'])}\n"
                         f"O'tkazma limiti: {limit} {CURRENCY_SYMBOL}", reply_markup=cancel_kb())
    await state.set_state(MoneyTransfer.waiting_for_amount)

@dp.message(MoneyTransfer.waiting_for_amount)
async def transfer_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
    except ValueError:
        return await message.answer("⚠️ Iltimos, to'g'ri raqam kiriting (masalan: 10 yoki 5.5)!")
        
    if amount <= 0: return await message.answer("⚠️ Miqdor musbat bo'lishi kerak!")
    
    user = get_user_data(message.from_user.id)
    limit = STATUS_DATA[user['level']]['limit']
    
    if amount > limit:
        return await message.answer(f"⚠️ Limitdan oshdingiz! Sizning limit: {limit} {CURRENCY_SYMBOL}.\nLimitni oshirish uchun status sotib oling.")
        
    if user['balance'] < amount:
        return await message.answer("⚠️ Hisobingizda yetarli mablag' yo'q!")
        
    data = await state.get_data()
    rid = data['rid']
    
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, message.from_user.id), commit=True)
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, rid), commit=True)
    
    await message.answer(f"✅ **Muvaffaqiyatli!**\n`{rid}` ID ga {format_num(amount)} {CURRENCY_SYMBOL} o'tkazildi.", reply_markup=main_menu(message.from_user.id))
    try: await bot.send_message(rid, f"📥 **Sizga pul kelib tushdi!**\n+{format_num(amount)} {CURRENCY_SYMBOL}\nKimdan: ID `{message.from_user.id}`")
    except: pass
    await state.clear()

# --- ADMIN PANEL ---

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    kb = [
        # Loyiha Qo'shish -> Akkount Qo'shish
        [InlineKeyboardButton(text="➕ Akkount Qo'shish", callback_data="adm_add_proj"),
         # Loyiha Tahrirlash -> Akkount Tahrirlash (YANGI)
         InlineKeyboardButton(text="✏️ Akkount Tahrirlash", callback_data="adm_manage_proj")],
        [InlineKeyboardButton(text="💵 Narxlar va Sozlamalar", callback_data="adm_prices"),
         InlineKeyboardButton(text="✏️ User Balansi", callback_data="adm_edit_bal")],
        [InlineKeyboardButton(text="📢 Broadcast (Xabar)", callback_data="adm_broadcast"),
         # UC Tahrirlash (YANGI)
         InlineKeyboardButton(text="💎 UC To'plamlarini Boshqarish/Tahrir", callback_data="adm_manage_uc")]
    ]
    await message.answer("🔐 **Admin Panel v3.1 (UC Servis)**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "adm_back_main")
async def adm_back_main(callback: types.CallbackQuery):
    await admin_panel(callback.message)

# --- AKKOUNT QO'SHISH (LOYIHA QO'SHISH) ---

@dp.callback_query(F.data == "adm_add_proj")
async def adm_add_proj_start(callback: types.CallbackQuery, state: FSMContext):
    # Loyiha -> Akkount
    await callback.message.edit_text("📝 Akkount nomini yozing:", reply_markup=cancel_kb())
    await state.set_state(AdminState.add_proj_name)

@dp.message(AdminState.add_proj_name)
async def adm_p_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(f"💰 Narxini kiriting ({CURRENCY_SYMBOL}):")
    await state.set_state(AdminState.add_proj_price)

@dp.message(AdminState.add_proj_price)
async def adm_p_price(message: types.Message, state: FSMContext):
    try:
        val = float(message.text)
    except: return await message.answer("⚠️ Raqam yozing!")
    await state.update_data(price=val)
    # Loyiha -> Akkount
    await message.answer("📝 Akkount haqida batafsil ma'lumot (Description):")
    await state.set_state(AdminState.add_proj_desc)

@dp.message(AdminState.add_proj_desc)
async def adm_p_desc(message: types.Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("🖼 Akkount Rasmi yoki Videosini yuboring (Yoki 'skip' deb yozing):")
    await state.set_state(AdminState.add_proj_media)

@dp.message(AdminState.add_proj_media)
async def adm_p_media(message: types.Message, state: FSMContext):
    mid, mtype = None, None
    if message.photo:
        mid, mtype = message.photo[-1].file_id, "photo"
    elif message.video:
        mid, mtype = message.video.file_id, "video"
    elif message.text and message.text.lower() == 'skip':
        pass
    else:
        return await message.answer("⚠️ Rasm, video yoki 'skip' yozing.")
        
    await state.update_data(mid=mid, mtype=mtype)
    await message.answer("📁 Endi asosiy faylni (Masalan, login/parol saqlangan TXT/JSON/PDF) yuboring:")
    await state.set_state(AdminState.add_proj_file)

@dp.message(AdminState.add_proj_file)
async def adm_p_file(message: types.Message, state: FSMContext):
    if not message.document: return await message.answer("⚠️ Fayl yuborishingiz shart!")
    data = await state.get_data()
    
    db_query("INSERT INTO projects (name, price, description, media_id, media_type, file_id) VALUES (?,?,?,?,?,?)",
             (data['name'], data['price'], data['desc'], data['mid'], data['mtype'], message.document.file_id), commit=True)
    
    # Loyiha -> Akkount
    await message.answer("✅ Akkount bazaga qo'shildi!", reply_markup=main_menu(message.from_user.id))
    await state.clear()


# --- YANGI: AKKOUNT TARNIRLASH (LOYIHA TARNIRLASH) ---

@dp.callback_query(F.data == "adm_manage_proj")
async def adm_manage_proj(callback: types.CallbackQuery):
    projs = db_query("SELECT id, name FROM projects", fetchall=True)
    if not projs: return await callback.message.edit_text("📂 Hozircha akkountlar mavjud emas.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Ortga", callback_data="adm_back_main")]]))
    
    kb = []
    msg = "✏️ **Tahrirlash uchun Akkountni tanlang:**\n\n"
    
    for pid, name in projs:
        kb.append([InlineKeyboardButton(text=f"[{pid}] {name} Akkounti", callback_data=f"edit_proj:{pid}")])
        
    kb.append([InlineKeyboardButton(text="⬅️ Ortga", callback_data="adm_back_main")])
    await callback.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("edit_proj:"))
async def adm_edit_proj_select(callback: types.CallbackQuery, state: FSMContext):
    pid = int(callback.data.split(":")[1])
    proj = db_query("SELECT name, price FROM projects WHERE id = ?", (pid,), fetchone=True)
    if not proj: return await callback.answer("Akkount topilmadi.", show_alert=True)
    
    name, price = proj
    
    await state.update_data(edit_pid=pid)
    
    msg = f"**Akkount ID:** `{pid}`\n**Nomi:** {name}\n**Narxi:** {format_num(price)} {CURRENCY_SYMBOL}\n\nQaysi maydonni tahrirlamoqchisiz?"
    await callback.message.edit_text(msg, reply_markup=edit_proj_kb(pid), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("ep_"))
async def adm_edit_proj_fields(callback: types.CallbackQuery, state: FSMContext):
    action, pid = callback.data.split(":")
    pid = int(pid)
    await state.update_data(edit_pid=pid, edit_field=action)
    
    if action == "ep_delete":
        db_query("DELETE FROM projects WHERE id = ?", (pid,), commit=True)
        await callback.answer(f"Akkount (ID: {pid}) o'chirildi.", show_alert=True)
        await adm_manage_proj(callback) 
        return

    proj = db_query("SELECT name, price, description, media_id, file_id FROM projects WHERE id = ?", (pid,), fetchone=True)
    if not proj: return await callback.answer("Akkount topilmadi.", show_alert=True)
    name, price, desc, mid, fid = proj

    if action == "ep_name":
        await callback.message.edit_text(f"Yangi **Akkount nomini** kiriting (Hozirgi: {name}):", reply_markup=cancel_kb())
        await state.set_state(AdminState.edit_proj_name)
    elif action == "ep_price":
        await callback.message.edit_text(f"Yangi **Narxini** kiriting ({CURRENCY_SYMBOL}) (Hozirgi: {price}):", reply_markup=cancel_kb())
        await state.set_state(AdminState.edit_proj_price)
    elif action == "ep_desc":
        await callback.message.edit_text(f"Yangi **Tavsifini** kiriting (Hozirgi: {desc[:50]}...):", reply_markup=cancel_kb())
        await state.set_state(AdminState.edit_proj_desc)
    elif action == "ep_media":
        await callback.message.edit_text("🖼 Yangi **Rasm yoki Video** yuboring (Yoki 'skip' deb yozing):", reply_markup=cancel_kb())
        await state.set_state(AdminState.edit_proj_media)
    elif action == "ep_file":
        await callback.message.edit_text("📁 Yangi **Asosiy faylni** yuboring (TXT/JSON/PDF/RAR):", reply_markup=cancel_kb())
        await state.set_state(AdminState.edit_proj_file)

# Akkount tahrirlash handlerlari
@dp.message(AdminState.edit_proj_name)
async def adm_save_proj_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db_query("UPDATE projects SET name = ? WHERE id = ?", (message.text, data['edit_pid']), commit=True)
    await message.answer("✅ Akkount nomi tahrirlandi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.message(AdminState.edit_proj_price)
async def adm_save_proj_price(message: types.Message, state: FSMContext):
    try: val = float(message.text)
    except: return await message.answer("⚠️ Iltimos, to'g'ri raqam kiriting.")
    data = await state.get_data()
    db_query("UPDATE projects SET price = ? WHERE id = ?", (val, data['edit_pid']), commit=True)
    await message.answer("✅ Akkount narxi tahrirlandi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.message(AdminState.edit_proj_desc)
async def adm_save_proj_desc(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db_query("UPDATE projects SET description = ? WHERE id = ?", (message.text, data['edit_pid']), commit=True)
    await message.answer("✅ Akkount tavsifi tahrirlandi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.message(AdminState.edit_proj_media)
async def adm_save_proj_media(message: types.Message, state: FSMContext):
    mid, mtype = None, None
    if message.photo:
        mid, mtype = message.photo[-1].file_id, "photo"
    elif message.video:
        mid, mtype = message.video.file_id, "video"
    elif message.text and message.text.lower() == 'skip':
        mid, mtype = None, None
    else:
        return await message.answer("⚠️ Iltimos, rasm, video yoki 'skip' yozing.")

    data = await state.get_data()
    db_query("UPDATE projects SET media_id = ?, media_type = ? WHERE id = ?", (mid, mtype, data['edit_pid']), commit=True)
    await message.answer("✅ Akkount rasmi/videosi tahrirlandi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.message(AdminState.edit_proj_file)
async def adm_save_proj_file(message: types.Message, state: FSMContext):
    if not message.document: return await message.answer("⚠️ Iltimos, fayl yuboring.")
    data = await state.get_data()
    db_query("UPDATE projects SET file_id = ? WHERE id = ?", (message.document.file_id, data['edit_pid']), commit=True)
    await message.answer("✅ Akkount fayli tahrirlandi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()


# --- UC TO'PLAMLARINI BOSHQARISH / TAHRIRLASH ---

@dp.callback_query(F.data == "adm_manage_uc")
async def adm_manage_uc(callback: types.CallbackQuery):
    packages = db_query("SELECT id, uc_amount, uzs_price, usd_price FROM uc_packages ORDER BY uc_amount ASC", fetchall=True)
    
    msg = "💎 **UC To'plamlari (Qo'shish / Tahrirlash):**\n\n"
    kb_rows = []
    
    if packages:
        for pid, uc_amt, uzs_p, usd_p in packages:
            msg += f"🆔 `{pid}`: **{uc_amt} UC** - {uzs_p:,.0f} UZS / {usd_p:.2f} USD\n"
            kb_rows.append([InlineKeyboardButton(text=f"✏️ Tahrirlash: {uc_amt} UC", callback_data=f"edit_uc:{pid}")])
    else:
        msg += "⚠️ Hozircha UC to'plamlari mavjud emas."
        
    kb_rows.append([InlineKeyboardButton(text="➕ Yangi UC To'plam Qo'shish", callback_data="adm_add_uc")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Ortga", callback_data="adm_back_main")])
    
    await callback.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="Markdown")

@dp.callback_query(F.data == "adm_add_uc")
async def adm_add_uc_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("💎 Qo'shiladigan UC miqdorini kiriting (faqat son):", reply_markup=cancel_kb())
    await state.set_state(AdminState.add_uc_amount)

@dp.message(AdminState.add_uc_amount)
async def adm_add_uc_amount(message: types.Message, state: FSMContext):
    try:
        uc_amt = int(message.text)
        if uc_amt <= 0: raise ValueError
    except: return await message.answer("⚠️ Iltimos, musbat butun son kiriting.")
    
    await state.update_data(uc_amount=uc_amt)
    await message.answer(f"💰 **{uc_amt} UC** uchun UZS narxini kiriting (masalan, 15000):")
    await state.set_state(AdminState.add_uc_uzs)

@dp.message(AdminState.add_uc_uzs)
async def adm_add_uc_uzs(message: types.Message, state: FSMContext):
    try:
        uzs_p = float(message.text)
        if uzs_p <= 0: raise ValueError
    except: return await message.answer("⚠️ Iltimos, musbat raqam kiriting.")
    
    await state.update_data(uzs_price=uzs_p)
    await message.answer(f"💰 **{message.text} UZS** narx uchun USD narxini kiriting (masalan, 1.5):")
    await state.set_state(AdminState.add_uc_usd)

@dp.message(AdminState.add_uc_usd)
async def adm_add_uc_usd(message: types.Message, state: FSMContext):
    try:
        usd_p = float(message.text)
        if usd_p <= 0: raise ValueError
    except: return await message.answer("⚠️ Iltimos, musbat raqam kiriting.")
    
    data = await state.get_data()
    
    db_query("INSERT INTO uc_packages (uc_amount, uzs_price, usd_price) VALUES (?, ?, ?)",
             (data['uc_amount'], data['uzs_price'], usd_p), commit=True)
             
    await message.answer(f"✅ **{data['uc_amount']} UC** to'plami bazaga qo'shildi!", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# UC Tahrirlash logikasi
@dp.callback_query(F.data.startswith("edit_uc:"))
async def adm_edit_uc_select(callback: types.CallbackQuery, state: FSMContext):
    pid = int(callback.data.split(":")[1])
    pkg = db_query("SELECT uc_amount, uzs_price, usd_price FROM uc_packages WHERE id = ?", (pid,), fetchone=True)
    if not pkg: return await callback.answer("To'plam topilmadi.", show_alert=True)
    
    uc_amount, uzs_price, usd_price = pkg
    
    await state.update_data(edit_pid=pid)
    
    msg = f"**To'plam ID:** `{pid}`\n**UC Miqdori:** {uc_amount}\n**UZS Narxi:** {uzs_price:,.0f} UZS\n**USD Narxi:** {usd_price:.2f} USD\n\nQaysi maydonni tahrirlamoqchisiz?"
    await callback.message.edit_text(msg, reply_markup=edit_uc_kb(pid), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("eu_"))
async def adm_edit_uc_fields(callback: types.CallbackQuery, state: FSMContext):
    action, pid = callback.data.split(":")
    pid = int(pid)
    await state.update_data(edit_pid=pid, edit_field=action)
    
    pkg = db_query("SELECT uc_amount, uzs_price, usd_price FROM uc_packages WHERE id = ?", (pid,), fetchone=True)
    if not pkg: return await callback.answer("To'plam topilmadi.", show_alert=True)
    uc_amount, uzs_price, usd_price = pkg

    if action == "eu_delete":
        db_query("DELETE FROM uc_packages WHERE id = ?", (pid,), commit=True)
        await callback.answer(f"UC To'plami (ID: {pid}) o'chirildi.", show_alert=True)
        await adm_manage_uc(callback) 
        return

    if action == "eu_amount":
        await callback.message.edit_text(f"Yangi **UC miqdorini** kiriting (Hozirgi: {uc_amount}):", reply_markup=cancel_kb())
        await state.set_state(AdminState.edit_uc_amount)
    elif action == "eu_uzs":
        await callback.message.edit_text(f"Yangi **UZS narxini** kiriting (Hozirgi: {uzs_price:,.0f} UZS):", reply_markup=cancel_kb())
        await state.set_state(AdminState.edit_uc_uzs)
    elif action == "eu_usd":
        await callback.message.edit_text(f"Yangi **USD narxini** kiriting (Hozirgi: {usd_price:.2f} USD):", reply_markup=cancel_kb())
        await state.set_state(AdminState.edit_uc_usd)

# UC tahrirlash handlerlari
@dp.message(AdminState.edit_uc_amount)
async def adm_save_uc_amount(message: types.Message, state: FSMContext):
    try: val = int(message.text)
    except: return await message.answer("⚠️ Iltimos, butun son kiriting.")
    data = await state.get_data()
    db_query("UPDATE uc_packages SET uc_amount = ? WHERE id = ?", (val, data['edit_pid']), commit=True)
    await message.answer("✅ UC miqdori tahrirlandi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.message(AdminState.edit_uc_uzs)
async def adm_save_uc_uzs(message: types.Message, state: FSMContext):
    try: val = float(message.text)
    except: return await message.answer("⚠️ Iltimos, raqam kiriting.")
    data = await state.get_data()
    db_query("UPDATE uc_packages SET uzs_price = ? WHERE id = ?", (val, data['edit_pid']), commit=True)
    await message.answer("✅ UZS narxi tahrirlandi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.message(AdminState.edit_uc_usd)
async def adm_save_uc_usd(message: types.Message, state: FSMContext):
    try: val = float(message.text)
    except: return await message.answer("⚠️ Iltimos, raqam kiriting.")
    data = await state.get_data()
    db_query("UPDATE uc_packages SET usd_price = ? WHERE id = ?", (val, data['edit_pid']), commit=True)
    await message.answer("✅ USD narxi tahrirlandi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()


# --- NARXLAR VA KONFIGURATSIYALAR --- (O'zgarishsiz)

@dp.callback_query(F.data == "adm_prices")
async def adm_prices_list(callback: types.CallbackQuery):
    p = get_dynamic_prices()
    kb = [
        [InlineKeyboardButton(text=f"Ref Bonus ({p['ref_reward']})", callback_data="set_ref_reward"),
         InlineKeyboardButton(text=f"Click ({p['click_reward']})", callback_data="set_click_reward")],
        [InlineKeyboardButton(text=f"Silver ({p['pro_price']})", callback_data="set_status_price_1"),
         InlineKeyboardButton(text=f"Gold ({p['prem_price']})", callback_data="set_status_price_2")],
        [InlineKeyboardButton(text=f"Platinum ({p['king_price']})", callback_data="set_status_price_3")],
        [InlineKeyboardButton(text="⬅️ Ortga", callback_data="adm_back_main")]
    ]
    await callback.message.edit_text("⚙️ **Narxlarni sozlash:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("set_"))
async def adm_set_val(callback: types.CallbackQuery, state: FSMContext):
    key = callback.data.replace("set_", "")
    await state.update_data(conf_key=key)
    await callback.message.edit_text(f"Yangi qiymatni yozing (Hozirgi: {key}):", reply_markup=cancel_kb())
    await state.set_state(AdminState.change_config_value)

@dp.message(AdminState.change_config_value)
async def adm_save_val(message: types.Message, state: FSMContext):
    try:
        val = float(message.text)
        data = await state.get_data()
        set_config(data['conf_key'], val)
        await message.answer("✅ Saqlandi!", reply_markup=main_menu(message.from_user.id))
        await state.clear()
    except:
        await message.answer("⚠️ Iltimos, raqam yozing.")

# --- BROADCAST --- (O'zgarishsiz)

@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📢 Barcha foydalanuvchilarga yuboriladigan xabarni (rasm/video/matn) yuboring:", reply_markup=cancel_kb())
    await state.set_state(AdminState.broadcast_msg)

@dp.message(AdminState.broadcast_msg)
async def adm_broadcast_send(message: types.Message, state: FSMContext):
    users = db_query("SELECT id FROM users", fetchall=True)
    count = 0
    await message.answer(f"⏳ Xabar {len(users)} ta foydalanuvchiga yuborilmoqda...")
    
    for user_row in users:
        try:
            await message.copy_to(chat_id=user_row[0])
            count += 1
            await asyncio.sleep(0.05) 
        except: pass
        
    await message.answer(f"✅ Xabar {count} ta foydalanuvchiga yetib bordi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# --- HISOB TO'LDIRISH --- (O'zgarishsiz)

@dp.message(F.text == "💳 Hisobni to'ldirish")
async def topup_start(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🇺🇿 UZS (Humo/Uzcard)"), KeyboardButton(text="🇺🇸 USD (Visa)")],
        [KeyboardButton(text="🚫 Bekor qilish")]
    ], resize_keyboard=True)
    await message.answer("To'lov valyutasini tanlang:", reply_markup=kb)
    await state.set_state(FillBalance.choosing_currency)

@dp.message(FillBalance.choosing_currency)
async def topup_curr(message: types.Message, state: FSMContext):
    rates = get_coin_rates()
    
    if "UZS" in message.text:
        curr, rate, card, holder = "UZS", rates['uzs'], CARD_UZS, CARD_NAME
    elif "USD" in message.text:
        curr, rate, card, holder = "USD", rates['usd'], CARD_VISA, "Visa Holder"
    else: 
        return await message.answer("⚠️ Iltimos, tugmalardan birini tanlang!")
    
    await state.update_data(curr=curr, rate=rate)
    msg = (f"💳 **To'lov ma'lumotlari:**\n\n"
           f"Karta: `{card}`\n"
           f"Ega: **{holder}**\n\n"
           f"📈 Kurs: 1 {CURRENCY_SYMBOL} = {rate} {curr}\n"
           f"👇 Qancha **{CURRENCY_NAME}** sotib olmoqchisiz? (Raqam yozing)")
    
    await message.answer(msg, reply_markup=cancel_kb(), parse_mode="Markdown")
    await state.set_state(FillBalance.waiting_for_amount)

@dp.message(FillBalance.waiting_for_amount)
async def topup_amt(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
    except: return await message.answer("⚠️ Iltimos, raqam yozing!")
    
    if amt <= 0: return await message.answer("⚠️ Musbat son yozing!")

    data = await state.get_data()
    total = amt * data['rate']
    txt = f"{total:,.0f} so'm" if data['curr'] == "UZS" else f"{total:.2f} $"
    
    await state.update_data(amt=amt, txt=txt)
    await message.answer(f"💵 To'lov miqdori: **{txt}**\n\nTo'lovni amalga oshirib, chekni (skrinshot) shu yerga yuboring:", parse_mode="Markdown")
    await state.set_state(FillBalance.waiting_for_receipt)

@dp.message(FillBalance.waiting_for_receipt, F.photo)
async def topup_rec(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"p_ok:{message.from_user.id}:{data['amt']}"),
         InlineKeyboardButton(text="❌ Rad etish", callback_data=f"p_no:{message.from_user.id}")]
    ])
    
    # Adminga yuborish
    caption = (f"📥 **YANGI TO'LOV!**\n\n"
               f"👤 User: `{message.from_user.id}`\n"
               f"💎 So'raldi: {data['amt']} {CURRENCY_SYMBOL}\n"
               f"💵 To'lov: {data['txt']}")
    
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption, reply_markup=kb, parse_mode="Markdown")
    
    await message.answer("✅ Chek qabul qilindi! Admin tasdiqlagach hisobingiz to'ldiriladi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.callback_query(F.data.startswith("p_ok:"))
async def approve_pay(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    uid, amt = int(parts[1]), float(parts[2])
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid), commit=True)
    try:
        await bot.send_message(uid, f"✅ **To'lov tasdiqlandi!**\nHisobingizga +{format_num(amt)} {CURRENCY_SYMBOL} qo'shildi.")
    except: pass
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ TASDIQLANDI")

@dp.callback_query(F.data.startswith("p_no:"))
async def reject_pay(callback: types.CallbackQuery):
    uid = int(callback.data.split(":")[1])
    try:
        await bot.send_message(uid, "❌ To'lovingiz rad etildi. Iltimos, admin bilan bog'laning.")
    except: pass
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ RAD ETILDI")

# --- BOTNI ISHGA TUSHIRISH ---

async def main():
    print(f"Bot ishga tushdi... {CURRENCY_NAME}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
