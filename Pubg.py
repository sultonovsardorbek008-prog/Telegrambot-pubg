import logging
import sqlite3
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# --- SOZLAMALAR ---
API_TOKEN = 'BU_YERGA_BOT_TOKENINI_QOYING'
ADMIN_ID = 123456789  # O'zingizning Telegram ID raqamingizni yozing
REFERRAL_BONUS = 500  # Referal uchun beriladigan summa (so'mda)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- MA'LUMOTLAR BAZASI (SQLite) ---
conn = sqlite3.connect('pubg_store.db')
cursor = conn.cursor()

# Jadvallarni yaratish
cursor.execute('''CREATE TABLE IF NOT EXISTS users
                  (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, balance INTEGER DEFAULT 0, referrer_id INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS products
                  (id INTEGER PRIMARY KEY, category TEXT, name TEXT, price INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS accounts
                  (id INTEGER PRIMARY KEY, name TEXT, description TEXT, price INTEGER, media_id TEXT, media_type TEXT, status TEXT DEFAULT 'active')''')
cursor.execute('''CREATE TABLE IF NOT EXISTS history
                  (id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT, amount INTEGER, date TEXT DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

# Boshlang'ich mahsulotlarni qo'shish (Agar yo'q bo'lsa)
def init_products():
    defaults = [
        ('uc', '60 UC', 12000), ('uc', '325 UC', 60000), ('uc', '660 UC', 115000),
        ('pop', 'Motosikl (Pop)', 5000), ('pop', 'Mashina (Pop)', 15000)
    ]
    current = cursor.execute("SELECT * FROM products").fetchall()
    if not current:
        cursor.executemany("INSERT INTO products (category, name, price) VALUES (?, ?, ?)", defaults)
        conn.commit()
init_products()

# --- HOLATLAR (STATES) ---
class BuyState(StatesGroup):
    waiting_for_id = State() # O'yinchi ID sini kutish

class AdminState(StatesGroup):
    add_acc_media = State()
    add_acc_name = State()
    add_acc_desc = State()
    add_acc_price = State()
    change_price_select = State()
    change_price_input = State()

# --- YORDAMCHI FUKNSIYALAR ---
def get_user(user_id):
    return cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def add_user(user_id, referrer_id=None):
    if not get_user(user_id):
        cursor.execute("INSERT INTO users (user_id, balance, referrer_id) VALUES (?, 0, ?)", (user_id, referrer_id))
        if referrer_id:
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (REFERRAL_BONUS, referrer_id))
            bot.send_message(referrer_id, f"🎉 Sizning havolangiz orqali do'stingiz qo'shildi! +{REFERRAL_BONUS} so'm.")
        conn.commit()

# --- TUGMALAR ---
menu_kb = ReplyKeyboardMarkup(resize_keyboard=True)
menu_kb.add("💎 UC olish", "🔥 Mashhurlik")
menu_kb.add("🎮 Akkountlar", "👤 Kabinet")

cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Bekor qilish")

# --- HANDLERLAR: START VA MENU ---
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    args = message.get_args()
    referrer_id = int(args) if args and args.isdigit() and int(args) != message.from_user.id else None
    add_user(message.from_user.id, referrer_id)
    await message.answer(f"Assalomu alaykum, {message.from_user.first_name}! PUBG savdo botiga xush kelibsiz.", reply_markup=menu_kb)

@dp.message_handler(text="❌ Bekor qilish", state="*")
async def cancel_action(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Amaliyot bekor qilindi.", reply_markup=menu_kb)

# --- KABINET ---
@dp.message_handler(text="👤 Kabinet")
async def show_cabinet(message: types.Message):
    user = get_user(message.from_user.id)
    history = cursor.execute("SELECT action, amount, date FROM history WHERE user_id=? ORDER BY id DESC LIMIT 5", (message.from_user.id,)).fetchall()
    
    hist_text = "\n".join([f"▫️ {h[2][:16]} | {h[0]} | {h[1]} so'm" for h in history])
    
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={message.from_user.id}"
    
    text = (f"👤 **Foydalanuvchi ID:** `{message.from_user.id}`\n"
            f"💰 **Hisobingiz:** {user[2]} so'm\n\n"
            f"🔗 **Referal havola:**\n`{ref_link}`\n"
            f"(Har bir taklif uchun {REFERRAL_BONUS} so'm)\n\n"
            f"📜 **Oxirgi harakatlar:**\n{hist_text if hist_text else 'Hozircha tarix yoq.'}")
    
    await message.answer(text, parse_mode="Markdown")

# --- UC VA MASHHURLIK OLISH ---
@dp.message_handler(text=["💎 UC olish", "🔥 Mashhurlik"])
async def shop_category(message: types.Message):
    category = 'uc' if "UC" in message.text else 'pop'
    items = cursor.execute("SELECT id, name, price FROM products WHERE category=?", (category,)).fetchall()
    
    markup = InlineKeyboardMarkup(row_width=2)
    for item in items:
        markup.insert(InlineKeyboardButton(text=f"{item[1]} - {item[2]} so'm", callback_data=f"buy_{item[0]}"))
    
    await message.answer(f"Quyidagilardan birini tanlang:", reply_markup=markup)

@dp.callback_query_handler(text_contains="buy_")
async def buy_item_start(call: types.CallbackQuery, state: FSMContext):
    item_id = int(call.data.split("_")[1])
    item = cursor.execute("SELECT * FROM products WHERE id=?", (item_id,)).fetchone()
    
    user = get_user(call.from_user.id)
    if user[2] < item[3]:
        await call.answer("Hisobingizda mablag' yetarli emas!", show_alert=True)
        return

    await state.update_data(item_id=item_id, price=item[3], name=item[2])
    await BuyState.waiting_for_id.set()
    await call.message.answer("Iltimos, PUBG ID raqamingizni kiriting:", reply_markup=cancel_kb)
    await call.answer()

@dp.message_handler(state=BuyState.waiting_for_id)
async def process_purchase(message: types.Message, state: FSMContext):
    pubg_id = message.text
    if not pubg_id.isdigit():
        await message.answer("Iltimos, faqat raqamli ID kiriting.")
        return

    data = await state.get_data()
    user_id = message.from_user.id
    price = data['price']
    
    # Pulni yechish
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))
    cursor.execute("INSERT INTO history (user_id, action, amount) VALUES (?, ?, ?)", (user_id, f"Xarid: {data['name']}", -price))
    conn.commit()
    
    # Adminga xabar
    admin_kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"approve_{user_id}_{price}"),
        InlineKeyboardButton("❌ Bekor qilish", callback_data=f"decline_{user_id}_{price}")
    )
    
    await bot.send_message(ADMIN_ID, 
                           f"🛒 **Yangi buyurtma!**\n"
                           f"👤 User: {message.from_user.full_name} (ID: {user_id})\n"
                           f"🏷 Mahsulot: {data['name']}\n"
                           f"🆔 PUBG ID: `{pubg_id}`\n"
                           f"💰 Narx: {price} so'm", parse_mode="Markdown", reply_markup=admin_kb)
    
    await message.answer("✅ Buyurtma qabul qilindi! Admin tasdiqlashini kuting.", reply_markup=menu_kb)
    await state.finish()

# --- ADMIN BUYURTMANI BOSHQARISHI ---
@dp.callback_query_handler(text_contains="approve_")
async def approve_order(call: types.CallbackQuery):
    _, user_id, _ = call.data.split("_")
    await bot.send_message(user_id, "✅ Sizning buyurtmangiz muvaffaqiyatli bajarildi!")
    await call.message.edit_text(f"{call.message.text}\n\n✅ **Bajarildi**")

@dp.callback_query_handler(text_contains="decline_")
async def decline_order(call: types.CallbackQuery):
    _, user_id, price = call.data.split("_")
    price = int(price)
    
    # Pulni qaytarish
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price, user_id))
    cursor.execute("INSERT INTO history (user_id, action, amount) VALUES (?, ?, ?)", (user_id, "Bekor qilindi (Qaytarildi)", price))
    conn.commit()
    
    await bot.send_message(user_id, "❌ Sizning buyurtmangiz bekor qilindi va pul hisobingizga qaytarildi.")
    await call.message.edit_text(f"{call.message.text}\n\n❌ **Bekor qilindi**")

# --- AKKOUNT SOTIB OLISH ---
@dp.message_handler(text="🎮 Akkountlar")
async def list_accounts(message: types.Message):
    accounts = cursor.execute("SELECT * FROM accounts WHERE status='active'").fetchall()
    if not accounts:
        await message.answer("Hozircha sotuvda akkountlar yo'q.")
        return

    for acc in accounts:
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton(f"🛒 Sotib olish ({acc[3]} so'm)", callback_data=f"buyacc_{acc[0]}"))
        caption = f"🎮 **{acc[1]}**\n📝 {acc[2]}\n💰 Narxi: {acc[3]} so'm"
        
        if acc[5] == 'photo':
            await bot.send_photo(message.chat.id, acc[4], caption=caption, parse_mode="Markdown", reply_markup=kb)
        elif acc[5] == 'video':
            await bot.send_video(message.chat.id, acc[4], caption=caption, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query_handler(text_contains="buyacc_")
async def buy_account(call: types.CallbackQuery):
    acc_id = int(call.data.split("_")[1])
    acc = cursor.execute("SELECT * FROM accounts WHERE id=?", (acc_id,)).fetchone()
    
    if not acc or acc[6] != 'active':
        await call.answer("Bu akkount allaqachon sotilgan.", show_alert=True)
        return

    user = get_user(call.from_user.id)
    if user[2] < acc[3]:
        await call.answer("Mablag' yetarli emas!", show_alert=True)
        return

    # Sotib olish jarayoni
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (acc[3], call.from_user.id))
    cursor.execute("UPDATE accounts SET status = 'sold' WHERE id = ?", (acc_id,))
    cursor.execute("INSERT INTO history (user_id, action, amount) VALUES (?, ?, ?)", (call.from_user.id, f"Akkount: {acc[1]}", -acc[3]))
    conn.commit()

    await bot.send_message(ADMIN_ID, f"✅ Akkount sotildi!\nUser: {call.from_user.full_name}\nAkkount: {acc[1]}")
    await call.message.delete()
    await call.message.answer(f"✅ Tabriklaymiz! {acc[1]} akkountini sotib oldingiz. Admin siz bilan bog'lanadi.")

# --- ADMIN PANEL ---
@dp.message_handler(commands=['admin'])
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("➕ Akkount qo'shish", "💵 Narxlarni o'zgartirish")
        kb.add("⬅️ Asosiy menyu")
        await message.answer("Admin panelga xush kelibsiz.", reply_markup=kb)
    else:
        await message.answer("Siz admin emassiz.")

@dp.message_handler(text="⬅️ Asosiy menyu", state="*")
async def back_main(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Asosiy menyu", reply_markup=menu_kb)

# -- Akkount qo'shish logikasi --
@dp.message_handler(text="➕ Akkount qo'shish")
async def add_acc_start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await AdminState.add_acc_media.set()
        await message.answer("Akkount rasmi yoki videosini yuboring:", reply_markup=cancel_kb)

@dp.message_handler(content_types=['photo', 'video'], state=AdminState.add_acc_media)
async def add_acc_media(message: types.Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = 'photo'
    else:
        file_id = message.video.file_id
        file_type = 'video'
    
    await state.update_data(file_id=file_id, file_type=file_type)
    await AdminState.add_acc_name.set()
    await message.answer("Akkount nomini kiriting:")

@dp.message_handler(state=AdminState.add_acc_name)
async def add_acc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await AdminState.add_acc_desc.set()
    await message.answer("Akkount tavsifini kiriting:")

@dp.message_handler(state=AdminState.add_acc_desc)
async def add_acc_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await AdminState.add_acc_price.set()
    await message.answer("Akkount narxini kiriting (faqat raqam):")

@dp.message_handler(state=AdminState.add_acc_price)
async def add_acc_finish(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Raqam kiriting!")
        return
    
    data = await state.get_data()
    cursor.execute("INSERT INTO accounts (name, description, price, media_id, media_type) VALUES (?, ?, ?, ?, ?)",
                   (data['name'], data['description'], int(message.text), data['file_id'], data['file_type']))
    conn.commit()
    await message.answer("✅ Akkount sotuvga qo'shildi!", reply_markup=menu_kb)
    await state.finish()

# -- Narxlarni o'zgartirish --
@dp.message_handler(text="💵 Narxlarni o'zgartirish")
async def change_price_start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        items = cursor.execute("SELECT id, name, price FROM products").fetchall()
        text = "Qaysi mahsulot narxini o'zgartirasiz ID sini kiriting:\n\n"
        for i in items:
            text += f"🆔 {i[0]} | {i[1]} | {i[2]} so'm\n"
        await AdminState.change_price_select.set()
        await message.answer(text, reply_markup=cancel_kb)

@dp.message_handler(state=AdminState.change_price_select)
async def change_price_select(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return
    item_id = int(message.text)
    await state.update_data(item_id=item_id)
    await AdminState.change_price_input.set()
    await message.answer("Yangi narxni kiriting:")

@dp.message_handler(state=AdminState.change_price_input)
async def change_price_finish(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return
    data = await state.get_data()
    cursor.execute("UPDATE products SET price=? WHERE id=?", (int(message.text), data['item_id']))
    conn.commit()
    await message.answer("✅ Narx yangilandi!", reply_markup=menu_kb)
    await state.finish()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)

