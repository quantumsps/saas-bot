import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    filters, ContextTypes
)
from upstash_redis import Redis

# === تنظیمات اصلی ===
MAIN_BOT_TOKEN = "8831950444:AAFB8jcSo_VjjcuRDMR0dtMSrwEezaVoAoQ"
ADMIN_ID = 8898908182  
REDIS_URL = "https://relieved-fawn-197670.upstash.io"
REDIS_TOKEN = "gQAAAAAAAwQmAAIgcDE2MmFlM2JjYTBmY2M0MzMyYjRiNzRlMTEyOWYyOGY4OA"
VERCEL_DOMAIN = "https://saas-bot-uyu8.vercel.app" 

CARD_NUMBER = "6219861837792000"
CARD_NAME = "امیر ابراهیمی"
MAX_BOTS_PER_USER = 5

PLANS = {
    30: "100000",
    60: "180000",
    90: "270000"
}

# وضعیت‌های ربات اصلی
(WAITING_RECEIPT, WAITING_TOKEN, WAITING_ADMIN_ID, 
 WAITING_MANAGE_USER, WAITING_ADMIN_DAYS, WAITING_NEW_ADMIN_ID,
 WAITING_BOT_NAME, WAITING_CO_ADMIN, WAITING_CO_ADMIN_NAME, 
 WAITING_BROADCAST, WAITING_FORCE_JOIN) = range(1, 12)

# وضعیت‌های ربات‌های مشتری
CHILD_WAITING_MSG, CHILD_ADMIN_REPLY = 1, 2

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)
app = FastAPI()
apps = {}

# ==========================================
# بخش دیتابیس ابری (Upstash Redis)
# ==========================================
def load_db():
    data = redis.get("saas_db")
    if data:
        db = json.loads(data) if isinstance(data, str) else data
    else:
        db = {'global': {'sales_open': True}, '_states': {}}
        
    if 'global' not in db: db['global'] = {'sales_open': True}
    if '_states' not in db: db['_states'] = {}
    
    for uid, udata in list(db.items()):
        if uid in ['global', '_states']: continue
        if not isinstance(udata, dict): continue
        if 'used_free_trial' not in udata: udata['used_free_trial'] = False
        if 'bots' not in udata: db[uid]['bots'] = {}
        if 'credits' not in udata: db[uid]['credits'] = []
        elif isinstance(udata['credits'], int): db[uid]['credits'] = [30] * udata['credits']
        
        for t, bdata in udata.get('bots', {}).items():
            if 'bot_name' not in bdata: bdata['bot_name'] = ""
            if 'force_join' not in bdata: bdata['force_join'] = ""
            if 'co_admins' not in bdata: bdata['co_admins'] = {}
            elif isinstance(bdata['co_admins'], list): 
                bdata['co_admins'] = {str(k): "بدون نام" for k in bdata['co_admins']}
    return db

def save_db(data):
    redis.set("saas_db", json.dumps(data))

# مدیریت وضعیت مکالمات در فضای ابری (جلوگیری از فراموشی سرور)
def get_state(db, bot_key, user_id):
    key = f"{bot_key}_{user_id}"
    return db.get('_states', {}).get(key, {'state': None, 'data': {}})

def set_state(db, bot_key, user_id, state, data=None):
    if '_states' not in db: db['_states'] = {}
    key = f"{bot_key}_{user_id}"
    if state is None:
        db['_states'].pop(key, None)
    else:
        curr_data = db['_states'].get(key, {}).get('data', {})
        if data is not None: curr_data.update(data)
        db['_states'][key] = {'state': state, 'data': curr_data}
    save_db(db)

def get_token_by_id(uid, bot_id, db):
    for t in db.get(str(uid), {}).get('bots', {}).keys():
        if t.split(':')[0] == str(bot_id): return t
    return None

def get_uid_by_token(token, db):
    for uid, udata in db.items():
        if uid not in ['global', '_states'] and isinstance(udata, dict) and token in udata.get('bots', {}): 
            return uid
    return None

def is_bot_running(bdata):
    try:
        exp_ok = datetime.fromisoformat(bdata['expiry_date']) > datetime.now()
    except:
        exp_ok = False
    return exp_ok and not bdata.get('admin_disabled', False) and not bdata.get('user_disabled', False)

def is_user_blocked(token, user_id, db):
    uid = get_uid_by_token(token, db)
    if uid: return user_id in db[uid]['bots'][token].get('blocked_users', [])
    return False

def block_user_in_db(token, user_id):
    db = load_db()
    uid = get_uid_by_token(token, db)
    if uid:
        if 'blocked_users' not in db[uid]['bots'][token]: db[uid]['bots'][token]['blocked_users'] = []
        if user_id not in db[uid]['bots'][token]['blocked_users']:
            db[uid]['bots'][token]['blocked_users'].append(user_id)
        save_db(db)
        return True
    return False

async def sync_bot_state(uid, token, db):
    bdata = db[str(uid)]['bots'][token]
    cbot = Bot(token)
    if is_bot_running(bdata):
        try:
            await cbot.set_webhook(f"{VERCEL_DOMAIN}/?token={token}")
            return "started"
        except Exception as e:
            return f"error: {e}"
    else:
        try:
            await cbot.delete_webhook()
        except:
            pass
        return "stopped"

# ==========================================
# هسته ربات‌های مشتریان (Child Bots)
# ==========================================
async def child_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = context.bot_data['admin_id']
    user_id = update.effective_user.id
    token = context.bot.token
    bot_id = token.split(':')[0]
    db = load_db()
    
    set_state(db, bot_id, user_id, None)
    
    uid = get_uid_by_token(token, db)
    bdata = db.get(uid, {}).get('bots', {}).get(token, {})
    co_admins = bdata.get('co_admins', {})
    
    if user_id == admin_id or str(user_id) in co_admins:
        role = "مدیر اصلی" if user_id == admin_id else f"ادمین فرعی ({co_admins[str(user_id)]})"
        msg = f"👨‍💻 **سلام {role} عزیز! به ربات خودت خوش اومدی.**\n\n✅ ربات فعال است و پیام‌ها برای شما فوروارد خواهد شد."
        if update.message: await update.message.reply_text(msg, parse_mode='Markdown')
        else: await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
        return
        
    if is_user_blocked(token, user_id, db):
        if update.message: await update.message.reply_text("⛔️ شما از طرف مدیریت این ربات مسدود شده‌اید.")
        return

    force_join = bdata.get('force_join')
    if force_join:
        try:
            member = await context.bot.get_chat_member(chat_id=force_join, user_id=user_id)
            if member.status in ['left', 'kicked']:
                link = f"https://t.me/{force_join.replace('@', '')}"
                keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url=link)], [InlineKeyboardButton("✅ عضو شدم", callback_data="start_anon")]]
                msg = "🛑 **عضویت اجباری!**\n\nبرای ارسال پیام ناشناس، ابتدا باید در کانال زیر عضو شوید:"
                if update.message: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
                else: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
                return
        except Exception: pass

    bot_name = bdata.get('bot_name', '')
    display_name = f" «{bot_name}»" if bot_name else ""
    msg = f"سلام! 🎭\nبه ربات پیام ناشناس{display_name} خوش آمدید.\nبرای ارسال پیام روی دکمه زیر کلیک کنید:"
    keyboard = [[InlineKeyboardButton("📨 ارسال پیام ناشناس", callback_data="start_anon")]]
    
    if update.message: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def child_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    token = context.bot.token
    bot_id = token.split(':')[0]
    db = load_db()
    uid = get_uid_by_token(token, db)
    bdata = db.get(uid, {}).get('bots', {}).get(token, {})
    co_admins = bdata.get('co_admins', {})
    admin_id = context.bot_data['admin_id']
    st = get_state(db, bot_id, user_id)

    await query.answer()

    if data == "start_anon":
        if is_user_blocked(token, user_id, db):
            await query.message.reply_text("⛔️ شما مسدود شده‌اید.")
            return
            
        force_join = bdata.get('force_join')
        if force_join and str(user_id) not in co_admins and user_id != admin_id:
            try:
                member = await context.bot.get_chat_member(chat_id=force_join, user_id=user_id)
                if member.status in ['left', 'kicked']:
                    link = f"https://t.me/{force_join.replace('@', '')}"
                    keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url=link)], [InlineKeyboardButton("✅ عضو شدم", callback_data="start_anon")]]
                    await query.edit_message_text("❌ **شما هنوز در کانال عضو نشده‌اید!**\nلطفاً ابتدا عضو شوید سپس روی دکمه زیر کلیک کنید:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
                    return
            except Exception: pass

        try: await query.edit_message_reply_markup(reply_markup=None)
        except: pass
        set_state(db, bot_id, user_id, CHILD_WAITING_MSG)
        await query.message.reply_text("✍️ لطفاً پیام خود را بفرستید (متن، عکس، ویدیو، یا ویس پشتیبانی می‌شود):")

    elif data == "cancel_anon":
        set_state(db, bot_id, user_id, None)
        try: await query.edit_message_text("❌ ارسال پیام لغو شد.\nبرای شروع مجدد /start را بزنید.")
        except: await query.message.reply_text("❌ لغو شد.")

    elif data == "confirm_anon":
        msg_id = st['data'].get('draft_msg_id')
        if not msg_id:
            await query.edit_message_text("❌ خطا! لطفاً مجدداً /start را بزنید.")
            return
        all_admins = list(set([admin_id] + [int(k) for k in co_admins.keys()]))
        admin_keyboard = [[InlineKeyboardButton("💬 پاسخ دادن", callback_data=f"creply_{user_id}")], [InlineKeyboardButton("🚫 مسدودیت", callback_data=f"cblock_{user_id}")]]
        
        success = False
        for ad_id in all_admins:
            try:
                await context.bot.send_message(chat_id=ad_id, text="📥 **پیام ناشناس جدید:**", parse_mode='Markdown')
                await context.bot.copy_message(chat_id=ad_id, from_chat_id=user_id, message_id=msg_id, reply_markup=InlineKeyboardMarkup(admin_keyboard))
                success = True
            except: pass
            
        text = "✅ پیام شما با موفقیت ارسال شد." if success else "❌ ربات متوقف است."
        set_state(db, bot_id, user_id, None)
        try: await query.edit_message_text(text)
        except: await query.message.reply_text(text)

    elif data == "cancel_reply":
        set_state(db, bot_id, user_id, None)
        await query.edit_message_text("❌ پاسخ لغو شد.")

    elif data.startswith("cblock_"):
        target_anon_id = int(data.split('_')[1])
        block_user_in_db(token, target_anon_id)
        try: await query.edit_message_reply_markup(reply_markup=None)
        except: pass
        await query.message.reply_text("✅ کاربر مسدود شد.")

    elif data.startswith("creply_"):
        target_id = int(data.split('_')[1])
        set_state(db, bot_id, user_id, CHILD_ADMIN_REPLY, {'reply_target': target_id})
        await query.message.reply_text("✍️ پیام خود را بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو پاسخ", callback_data="cancel_reply")]]))

async def child_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    token = context.bot.token
    bot_id = token.split(':')[0]
    db = load_db()
    st = get_state(db, bot_id, user_id)
    state = st['state']

    if state == CHILD_WAITING_MSG:
        set_state(db, bot_id, user_id, CHILD_WAITING_MSG, {'draft_msg_id': update.message.message_id})
        keyboard = [[InlineKeyboardButton("✅ بله، بفرست", callback_data="confirm_anon")], [InlineKeyboardButton("❌ لغو", callback_data="cancel_anon")]]
        await update.message.reply_text("❓ آیا از ارسال این پیام مطمئن هستید؟", reply_markup=InlineKeyboardMarkup(keyboard))

    elif state == CHILD_ADMIN_REPLY:
        target_id = st['data'].get('reply_target')
        set_state(db, bot_id, user_id, None)
        if not target_id: return
        try:
            await context.bot.send_message(chat_id=target_id, text="📬 **پیام از طرف مدیریت:**", parse_mode='Markdown')
            await context.bot.copy_message(chat_id=target_id, from_chat_id=user_id, message_id=update.message.message_id, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 ارسال پاسخ به مدیر", callback_data="start_anon")]]))
            await update.message.reply_text("✅ پاسخ ارسال شد.")
        except:
            await update.message.reply_text("❌ خطا! کاربر ربات را بلاک کرده.")

# ==========================================
# ربات اصلی (پنل مرکزی)
# ==========================================
async def main_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = load_db()
    
    if str(user_id) not in db:
        db[str(user_id)] = {'credits': [], 'bots': {}, 'used_free_trial': False}
        save_db(db)
        
    set_state(db, "main", user_id, None)
        
    if user_id == ADMIN_ID:
        sales_status = "🟢 باز" if db['global'].get('sales_open', True) else "🔴 بسته"
        msg = "👨‍💻 **پنل مدیریت ربات‌ساز**\n\nیکی از گزینه‌های زیر را انتخاب کنید:"
        keyboard = [
            [InlineKeyboardButton("👥 لیست و وضعیت کاربران", callback_data="admin_users_list")],
            [InlineKeyboardButton("⚙️ مدیریت کاربران", callback_data="admin_manage_user")],
            [InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data="admin_broadcast")],
            [InlineKeyboardButton(f"🛒 وضعیت فروش: {sales_status}", callback_data="admin_toggle_sales")]
        ]
    else:
        credits_count = len(db.get(str(user_id), {}).get('credits', []))
        msg = f"🤖 **به پلتفرم ربات‌ساز ناشناس خوش آمدید!**\n\nتعداد اشتراک‌های استفاده نشده: {credits_count}\nاز دکمه‌های زیر استفاده کنید:"
        keyboard = [
            [InlineKeyboardButton("🛒 خرید اشتراک جدید", callback_data="buy_new")],
            [InlineKeyboardButton("🔑 راه‌اندازی ربات از اشتراک", callback_data="set_bot")],
            [InlineKeyboardButton("🤖 مدیریت ربات‌های من", callback_data="user_bots")],
            [InlineKeyboardButton("❓ راهنمای استفاده", callback_data="user_help")]
        ]
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = str(update.effective_user.id)
    db = load_db()
    await query.answer()

    if data == "cancel_flow":
        await main_start(update, context)
        return

    elif data == "user_help":
        help_text = (
            "📚 **راهنمای جامع پلتفرم ربات‌ساز پیام ناشناس**\n\n"
            "به سیستم هوشمند ساخت و مدیریت ربات‌های پیام ناشناس خوش آمدید! با این پنل می‌توانید ربات اختصاصی خود را با امکانات پیشرفته مدیریت کنید.\n\n"
            "🛒 **۱. تهیه اشتراک:**\n"
            "برای شروع، از منوی اصلی روی «خرید اشتراک جدید» کلیک کنید. شما می‌توانید از **پلن ۷ روزه رایگان** (فقط برای یک‌بار) استفاده کنید و یا پلن‌های ۱ تا ۳ ماهه را با پرداخت کارت‌به‌کارت خریداری کنید.\n\n"
            "🔑 **۲. راه‌اندازی ربات:**\n"
            "پس از فعال شدن اشتراک، روی «راه‌اندازی ربات از اشتراک» کلیک کنید:\n"
            "🔹 ابتدا توکن ربات خود را (که از @BotFather دریافت کرده‌اید) بفرستید.\n"
            "🔹 سپس شناسه عددی تلگرام خود را وارد کنید تا ربات فوراً استارت بخورد.\n\n"
            "⚙️ **۳. مدیریت و تنظیمات پیشرفته (بخش مدیریت ربات‌های من):**\n"
            "با ورود به این بخش، امکانات حرفه‌ای زیر در اختیار شماست:\n"
            "🔒 **قفل عضویت اجباری:** آیدی کانال خود را (با @) ثبت کنید تا کاربران قبل از ارسال پیام، مجبور به عضویت در کانال شما شوند. *(⚠️ ربات حتماً باید در کانال شما ادمین باشد)*.\n"
            "👥 **ادمین‌های فرعی:** می‌توانید آیدی دوستان یا همکارانتان را ثبت کنید تا پیام‌های ناشناس برای آن‌ها هم ارسال شود و بتوانند پاسخ دهند.\n"
            "✍️ **نام نمایشی:** یک اسم دلخواه برای ربات خود تعیین کنید تا پیام خوش‌آمدگویی جذاب‌تر شود.\n"
            "🔴/🟢 **روشن/خاموش:** هر زمان که خواستید دریافت پیام ناشناس را موقتاً متوقف کنید.\n\n"
            "💬 **۴. چت دوطرفه و بلاک (ضد اسپم):**\n"
            "هر پیامی که در ربات دریافت می‌کنید، دارای دکمه‌های شیشه‌ای است:\n"
            "🔸 با زدن **«💬 پاسخ دادن»**، می‌توانید مستقیماً و به صورت ناشناس جواب شخص را بدهید.\n"
            "🔸 با زدن **«🚫 مسدودیت»**، کاربر مزاحم برای همیشه بلاک شده و دیگر نمی‌تواند به شما پیام بدهد."
        )
        await query.edit_message_text(
            help_text, 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="cancel_flow")]]), 
            parse_mode='Markdown'
        )

    # --- بخش مدیریت جهانی فروش و ادمین کل ---
    elif data == "admin_toggle_sales":
        if int(user_id) != ADMIN_ID: return
        current = db['global'].get('sales_open', True)
        db['global']['sales_open'] = not current
        save_db(db)
        await main_start(update, context)

    elif data == "admin_broadcast":
        if int(user_id) != ADMIN_ID: return
        set_state(db, "main", user_id, WAITING_BROADCAST)
        await query.edit_message_text("📢 لطفاً پیام همگانی خود را بفرستید (متن یا مدیا):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))

    elif data == "admin_users_list":
        if int(user_id) != ADMIN_ID: return
        users = [k for k in db.keys() if k not in ['global', '_states']]
        total_bots = sum(len(db[u].get('bots', {})) for u in users if isinstance(db[u], dict))
        msg = f"👥 **آمار کلی سیستم:**\nتعداد کل کاربران: {len(users)}\nتعداد کل ربات‌ها: {total_bots}\n\n📋 **لیست آخرین کاربران:**\n"
        for u in users[-20:]:
            b_cnt = len(db[u].get('bots', {}))
            c_cnt = len(db[u].get('credits', []))
            msg += f"👤 `{u}` | ربات: {b_cnt} | اشتراک: {c_cnt}\n"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]), parse_mode='Markdown')

    elif data == "admin_manage_user":
        if int(user_id) != ADMIN_ID: return
        set_state(db, "main", user_id, WAITING_MANAGE_USER)
        await query.edit_message_text("👤 لطفاً شناسه عددی (User ID) کاربر مورد نظر را ارسال کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))

    elif data.startswith("admaddcred_"):
        if int(user_id) != ADMIN_ID: return
        target_uid = data.split('_')[1]
        if target_uid not in db: db[target_uid] = {'credits': [], 'bots': {}, 'used_free_trial': False}
        db[target_uid]['credits'].append(30)
        save_db(db)
        await query.edit_message_text(f"✅ یک اشتراک ۳۰ روزه به کاربر `{target_uid}` اضافه شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]), parse_mode='Markdown')

    elif data.startswith("admb_"):
        if int(user_id) != ADMIN_ID: return
        _, target_uid, b_id = data.split('_')
        token = get_token_by_id(target_uid, b_id, db)
        if token:
            curr = db[target_uid]['bots'][token].get('admin_disabled', False)
            db[target_uid]['bots'][token]['admin_disabled'] = not curr
            save_db(db)
            await sync_bot_state(target_uid, token, db)
            status_txt = "مسدود (خاموش)" if not curr else "آزاد (روشن)"
            await query.edit_message_text(f"✅ وضعیت ربات `{b_id}` به **{status_txt}** تغییر یافت.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]), parse_mode='Markdown')

    # --- انتخاب پلن‌ها ---
    elif data == "buy_new":
        if not db['global'].get('sales_open', True):
            await query.answer("❌ فروش ربات موقتاً توسط مدیریت بسته شده است.", show_alert=True)
            return
            
        current_bots, credits = len(db.get(user_id, {}).get('bots', {})), len(db.get(user_id, {}).get('credits', []))
        if current_bots + credits >= MAX_BOTS_PER_USER:
            await query.edit_message_text(f"❌ شما به سقف مجاز ({MAX_BOTS_PER_USER} ربات) رسیده‌اید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
            return
            
        keyboard = [
            [InlineKeyboardButton("1 ماهه - 100 هزار تومان", callback_data="buyplan_new_30")],
            [InlineKeyboardButton("2 ماهه - 180 هزار تومان", callback_data="buyplan_new_60")],
            [InlineKeyboardButton("3 ماهه - 270 هزار تومان", callback_data="buyplan_new_90")],
            [InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")]
        ]
        
        if not db.get(user_id, {}).get('used_free_trial', False):
            keyboard.insert(0, [InlineKeyboardButton("🎁 پلن 7 روزه رایگان (یکبار مصرف)", callback_data="free_trial")])
            
        await query.edit_message_text("🛒 **لطفاً پلن مورد نظر خود را انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "free_trial":
        if not db['global'].get('sales_open', True):
            await query.answer("❌ فروش ربات بسته است.", show_alert=True)
            return
            
        if db.get(user_id, {}).get('used_free_trial', False):
            await query.answer("❌ شما قبلاً از این پلن استفاده کرده‌اید!", show_alert=True)
            return
            
        db[user_id]['used_free_trial'] = True
        db[user_id]['credits'].append(7)
        save_db(db)
        await query.edit_message_text("🎉 **پلن 7 روزه رایگان با موفقیت فعال شد!**\n\nهم‌اکنون از منوی اصلی «راه‌اندازی ربات» را انتخاب کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منو", callback_data="cancel_flow")]]), parse_mode='Markdown')

    elif data.startswith("renew_"):
        b_id = data.split('_')[1]
        keyboard = [
            [InlineKeyboardButton("1 ماهه - 100 هزار تومان", callback_data=f"buyplan_ren_30_{b_id}")],
            [InlineKeyboardButton("2 ماهه - 180 هزار تومان", callback_data=f"buyplan_ren_60_{b_id}")],
            [InlineKeyboardButton("3 ماهه - 270 هزار تومان", callback_data=f"buyplan_ren_90_{b_id}")],
            [InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")]
        ]
        await query.edit_message_text(f"🛒 **پلن تمدید ربات `{b_id}` را انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("buyplan_"):
        parts = data.split('_')
        buy_type, days = parts[1], int(parts[2])
        state_data = {'buy_type': buy_type, 'buy_plan_days': days}
        caption = f"تمدید ربات {parts[3]}" if buy_type == 'ren' else "خرید اشتراک جدید"
        if buy_type == 'ren': state_data['renew_id'] = parts[3]
            
        set_state(db, "main", user_id, WAITING_RECEIPT, state_data)
        msg = f"💳 **{caption} ({days} روزه)**\n\n💵 مبلغ: `{PLANS[days]}` تومان\n💳 کارت: `{CARD_NUMBER}`\n👤 به نام: {CARD_NAME}\n\n📸 **لطفاً عکس فیش واریزی را بفرستید:**"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو پرداخت", callback_data="cancel_flow")]]), parse_mode='Markdown')

    elif data == "set_bot":
        if not db.get(user_id, {}).get('credits', []):
            await query.edit_message_text("❌ شما هیچ اشتراک استفاده نشده‌ای ندارید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
            return
        set_state(db, "main", user_id, WAITING_TOKEN)
        await query.edit_message_text("🔑 **مرحله ۱:** لطفاً توکن ربات جدید را ارسال کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]), parse_mode='Markdown')

    elif data == "user_bots":
        bots = db.get(user_id, {}).get('bots', {})
        if not bots:
            await query.edit_message_text("❌ شما هیچ ربات فعالی ندارید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
            return
            
        keyboard = [[InlineKeyboardButton(f"{'🟢' if is_bot_running(bdata) else '🔴'} ربات {t.split(':')[0]}", callback_data=f"mybot_{t.split(':')[0]}")] for t, bdata in bots.items()]
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")])
        await query.edit_message_text("🤖 **یکی از ربات‌های خود را انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("mybot_") or data.startswith("utoggle_"):
        b_id = data.split('_')[1]
        token = get_token_by_id(user_id, b_id, db)
        if not token: return
        
        set_state(db, "main", user_id, None)
        if data.startswith("utoggle_"):
            db[user_id]['bots'][token]['user_disabled'] = not db[user_id]['bots'][token].get('user_disabled', False)
            save_db(db)
            await sync_bot_state(user_id, token, db)
        
        bdata = db[user_id]['bots'][token]
        days_left = max(0, (datetime.fromisoformat(bdata['expiry_date']) - datetime.now()).days)
        force_text = bdata.get('force_join') or 'ندارد (خاموش)'
        running = is_bot_running(bdata)
        
        msg = (
            f"🤖 **ربات:** `{b_id}`\n"
            f"⏳ **زمان باقی‌مانده:** {days_left} روز\n\n"
            f"📝 **نام نمایشی ربات:** {bdata.get('bot_name') or 'ثبت نشده'}\n"
            f"🔒 **قفل کانال:** {force_text}\n"
            f"👥 **تعداد مدیر فرعی:** {len(bdata.get('co_admins', {}))} نفر\n\n"
            f"وضعیت سرور: {'در حال کار 🚀' if running else 'متوقف 🛑'}\nمدیریت کل: {'⛔️ مسدود' if bdata.get('admin_disabled') else '✅ آزاد'}"
        )
        keyboard = [
            [InlineKeyboardButton("🛒 تمدید زمان ربات", callback_data=f"renew_{b_id}"), InlineKeyboardButton("روشن / خاموش", callback_data=f"utoggle_{b_id}")],
            [InlineKeyboardButton("✍️ تعیین نام", callback_data=f"setname_{b_id}"), InlineKeyboardButton("👥 ادمین فرعی", callback_data=f"coadmin_{b_id}")],
            [InlineKeyboardButton("🔒 قفل عضویت اجباری", callback_data=f"forcejoin_{b_id}")],
            [InlineKeyboardButton("👤 تغییر آیدی مدیر اصلی", callback_data=f"chgadmin_{b_id}")],
            [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="user_bots")]
        ]
        try: await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except: pass

    elif data.startswith("forcejoin_"):
        b_id = data.split('_')[1]
        set_state(db, "main", user_id, WAITING_FORCE_JOIN, {'edit_bid': b_id})
        msg = "🔒 **تنظیم قفل عضویت اجباری:**\n\nبرای تنظیم قفل، آیدی کانال خود را با @ بفرستید (مثال: `@MyChannel`).\n\n⚠️ **نکته بسیار مهم:** حتماً باید ربات خود را در کانالتان ادمین کنید، در غیر این صورت قفل عمل نخواهد کرد.\n\nبرای غیرفعال کردن قفل، کلمه `لغو` را بفرستید."
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]), parse_mode='Markdown')

    elif data.startswith("setname_"):
        b_id = data.split('_')[1]
        set_state(db, "main", user_id, WAITING_BOT_NAME, {'edit_bid': b_id})
        await query.edit_message_text("✍️ لطفاً نام نمایشی ربات خود را بفرستید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"mybot_{b_id}")]]))

    elif data.startswith("coadmin_"):
        b_id = data.split('_')[1]
        set_state(db, "main", user_id, WAITING_CO_ADMIN, {'edit_bid': b_id})
        co_admins = db[user_id]['bots'][get_token_by_id(user_id, b_id, db)].get('co_admins', {})
        msg = f"👥 **مدیریت ادمین‌های فرعی ربات {b_id}:**\n\n" + "".join([f" - `{k}` ({v})\n" for k,v in co_admins.items()])
        msg += "\nبرای **اضافه کردن** یا **حذف** یک ادمین، آیدی عددی او را بفرستید:"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]), parse_mode='Markdown')

    elif data.startswith("chgadmin_"):
        b_id = data.split('_')[1]
        set_state(db, "main", user_id, WAITING_NEW_ADMIN_ID, {'edit_bid': b_id})
        await query.edit_message_text("👤 لطفاً شناسه عددی جدید مدیر اصلی را بفرستید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"mybot_{b_id}")]]))

    # --- تایید یا رد فیش‌های واریزی (خرید جدید و تمدید) ---
    elif data.startswith("approve_new_") or data.startswith("reject_new_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        target_uid = parts[2]
        
        if parts[0] == "approve":
            days = int(parts[3])
            if target_uid not in db: db[target_uid] = {'credits': [], 'bots': {}, 'used_free_trial': False}
            db[target_uid]['credits'].append(days)
            save_db(db)
            try: await context.bot.send_message(chat_id=target_uid, text=f"🎉 **پرداخت تایید شد!**\nشما یک اشتراک {days} روزه دریافت کردید.", parse_mode='Markdown')
            except: pass
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ **تایید شد ({days} روزه).**", parse_mode='Markdown')
        else:
            try: await context.bot.send_message(chat_id=target_uid, text="❌ **پرداخت شما تایید نشد.**")
            except: pass
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ **رد شد.**", parse_mode='Markdown')

    elif data.startswith("approve_ren_") or data.startswith("reject_ren_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        target_uid, b_id = parts[2], parts[3]
        token = get_token_by_id(target_uid, b_id, db)
        
        if parts[0] == "approve" and token:
            days = int(parts[4])
            curr_exp = datetime.fromisoformat(db[target_uid]['bots'][token]['expiry_date'])
            base_time = curr_exp if curr_exp > datetime.now() else datetime.now()
            db[target_uid]['bots'][token]['expiry_date'] = (base_time + timedelta(days=days)).isoformat()
            save_db(db)
            await sync_bot_state(target_uid, token, db)
            try: await context.bot.send_message(chat_id=target_uid, text=f"🎉 **تمدید تایید شد!**\nربات `{b_id}` به مدت {days} روز شارژ شد.", parse_mode='Markdown')
            except: pass
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ **تمدید تایید شد ({days} روزه).**", parse_mode='Markdown')
        else:
            try: await context.bot.send_message(chat_id=target_uid, text=f"❌ **تمدید ربات `{b_id}` تایید نشد.**", parse_mode='Markdown')
            except: pass
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ **رد شد.**", parse_mode='Markdown')

# ==========================================
# هندلر پیام‌های ورودی ربات اصلی
# ==========================================
async def main_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    db = load_db()
    st = get_state(db, "main", user_id)
    state = st['state']
    sdata = st['data']

    if not state:
        return

    if state == WAITING_RECEIPT:
        if not (update.message.photo or update.message.document):
            await update.message.reply_text("❌ لطفاً فقط عکس فیش واریزی را ارسال کنید.")
            return
        photo_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
        days = sdata.get('buy_plan_days', 30)
        
        if sdata.get('buy_type', 'new') == 'new': 
            cb_approve, cb_reject, caption_txt = f"approve_new_{user.id}_{days}", f"reject_new_{user.id}", f"خرید اشتراک جدید ({days} روزه)"
        else: 
            b_id = sdata.get('renew_id')
            cb_approve, cb_reject, caption_txt = f"approve_ren_{user.id}_{b_id}_{days}", f"reject_ren_{user.id}_{b_id}", f"تمدید ربات {b_id} ({days} روزه)"

        set_state(db, "main", user_id, None)
        keyboard = [[InlineKeyboardButton("✅ تایید", callback_data=cb_approve), InlineKeyboardButton("❌ رد", callback_data=cb_reject)]]
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_id, caption=f"🧾 **{caption_txt}**\n\n👤 از طرف: `{user.id}`", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        await update.message.reply_text("✅ فیش شما ارسال شد و پس از بررسی ادمین فعال می‌گردد.")

    elif state == WAITING_TOKEN:
        if not update.message.text or ':' not in update.message.text:
            await update.message.reply_text("❌ فرمت توکن نامعتبر است. لطفاً توکن دقیق دریافتی از @BotFather را بفرستید:")
            return
        set_state(db, "main", user_id, WAITING_ADMIN_ID, {'temp_token': update.message.text.strip()})
        await update.message.reply_text("✅ توکن دریافت شد.\n\n👤 حالا شناسه عددی خودتان (مدیر پیام‌ها) را بفرستید:")

    elif state == WAITING_ADMIN_ID:
        if not update.message.text or not update.message.text.strip().isdigit():
            await update.message.reply_text("❌ شناسه عددی باید فقط شامل اعداد باشد. مجدداً ارسال کنید:")
            return
        admin_id_child = int(update.message.text.strip())
        token = sdata['temp_token']
        
        if not db[user_id].get('credits'):
            set_state(db, "main", user_id, None)
            await update.message.reply_text("❌ شما اشتراک فعالی ندارید.")
            return
            
        days = db[user_id]['credits'].pop(0)
        db[user_id]['bots'][token] = {
            'admin_id': admin_id_child, 'expiry_date': (datetime.now() + timedelta(days=days)).isoformat(),
            'warned': False, 'admin_disabled': False, 'user_disabled': False, 'blocked_users': [],
            'bot_name': "", 'co_admins': {}, 'force_join': ""
        }
        set_state(db, "main", user_id, None)
        await sync_bot_state(user_id, token, db)
        await update.message.reply_text(f"🚀 **ربات شما ساخته و برای {days} روز روشن شد!**\nهم‌اکنون می‌توانید وارد ربات خود شوید و /start را بزنید.", parse_mode='Markdown')

    elif state == WAITING_FORCE_JOIN:
        text = (update.message.text or "").strip()
        b_id = sdata['edit_bid']
        token = get_token_by_id(user_id, b_id, db)
        
        if text == "لغو":
            db[user_id]['bots'][token]['force_join'] = ""
            set_state(db, "main", user_id, None)
            await update.message.reply_text("✅ قفل عضویت اجباری غیرفعال شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data=f"mybot_{b_id}")]]))
            return
            
        if not text.startswith('@') and not text.startswith('-100'):
            await update.message.reply_text("❌ فرمت آیدی اشتباه است. لطفاً حتماً با @ بفرستید:")
            return
            
        db[user_id]['bots'][token]['force_join'] = text
        set_state(db, "main", user_id, None)
        await update.message.reply_text(f"✅ قفل عضویت اجباری برای کانال {text} تنظیم شد!\n⚠️ فراموش نکنید ربات را در کانال ادمین کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data=f"mybot_{b_id}")]]))

    elif state == WAITING_BOT_NAME:
        name = (update.message.text or "").strip()
        b_id = sdata['edit_bid']
        db[user_id]['bots'][get_token_by_id(user_id, b_id, db)]['bot_name'] = name
        set_state(db, "main", user_id, None)
        await update.message.reply_text(f"✅ نام ربات به «{name}» تغییر یافت.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))

    elif state == WAITING_BROADCAST:
        if int(user_id) != ADMIN_ID: return
        set_state(db, "main", user_id, None)
        success = 0
        await update.message.reply_text("⏳ در حال ارسال...")
        for uid in db.keys():
            if uid not in ['global', '_states']:
                try:
                    await context.bot.copy_message(chat_id=uid, from_chat_id=user.id, message_id=update.message.message_id)
                    success += 1
                except: pass
        await update.message.reply_text(f"✅ ارسال به {success} نفر انجام شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))

    elif state == WAITING_CO_ADMIN:
        ad_id_str = (update.message.text or "").strip()
        b_id = sdata['edit_bid']
        token = get_token_by_id(user_id, b_id, db)
        co_admins = db[user_id]['bots'][token].get('co_admins', {})
        
        if ad_id_str in co_admins:
            del co_admins[ad_id_str]
            set_state(db, "main", user_id, None)
            await update.message.reply_text("✅ ادمین فرعی حذف شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))
        else:
            set_state(db, "main", user_id, WAITING_CO_ADMIN_NAME, {'pending_coadmin': ad_id_str})
            await update.message.reply_text(f"👤 لطفاً یک نام نمایشی برای ادمین `{ad_id_str}` بفرستید (مثلاً: شریک دوم):", parse_mode='Markdown')

    elif state == WAITING_CO_ADMIN_NAME:
        name = (update.message.text or "").strip()
        b_id = sdata['edit_bid']
        db[user_id]['bots'][get_token_by_id(user_id, b_id, db)]['co_admins'][sdata['pending_coadmin']] = name
        set_state(db, "main", user_id, None)
        await update.message.reply_text("✅ ادمین فرعی اضافه شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))

    elif state == WAITING_NEW_ADMIN_ID:
        if not update.message.text or not update.message.text.strip().isdigit():
            await update.message.reply_text("❌ لطفاً فقط شناسه عددی بفرستید:")
            return
        new_admin_id = int(update.message.text.strip())
        b_id = sdata['edit_bid']
        token = get_token_by_id(user_id, b_id, db)
        db[user_id]['bots'][token]['admin_id'] = new_admin_id
        set_state(db, "main", user_id, None)
        if token in apps: apps[token].bot_data['admin_id'] = new_admin_id
        await update.message.reply_text("✅ شناسه مدیر تغییر یافت.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))

    elif state == WAITING_MANAGE_USER:
        if int(user_id) != ADMIN_ID: return
        target_uid = (update.message.text or "").strip()
        set_state(db, "main", user_id, None)
        if target_uid not in db or target_uid in ['global', '_states']:
            await update.message.reply_text("❌ کاربر در دیتابیس یافت نشد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
            return
        udata = db[target_uid]
        msg = f"👤 **مدیریت کاربر:** `{target_uid}`\nاعتبارهای استفاده نشده: {len(udata.get('credits', []))}\nتعداد ربات‌ها: {len(udata.get('bots', {}))}"
        keyboard = [[InlineKeyboardButton("➕ افزودن ۱ اشتراک ۳۰ روزه", callback_data=f"admaddcred_{target_uid}")]]
        for t, bdata in udata.get('bots', {}).items():
            bid = t.split(':')[0]
            st_icon = "⛔️" if bdata.get('admin_disabled') else "✅"
            keyboard.append([InlineKeyboardButton(f"{st_icon} تغییر وضعیت ربات {bid}", callback_data=f"admb_{target_uid}_{bid}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")])
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ==========================================
# مدیریت وب‌هوک‌ها در ورسل (FastAPI)
# ==========================================
async def get_app(token: str, is_main: bool, admin_id=None):
    if token not in apps:
        application = Application.builder().token(token).build()
        if is_main:
            application.add_handler(CommandHandler('start', main_start))
            application.add_handler(CallbackQueryHandler(handle_all_callbacks))
            application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_messages))
        else:
            application.bot_data['admin_id'] = admin_id
            application.add_handler(CommandHandler('start', child_start))
            application.add_handler(CallbackQueryHandler(child_callbacks))
            application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, child_messages))
        await application.initialize()
        apps[token] = application
    elif not is_main and admin_id:
        apps[token].bot_data['admin_id'] = admin_id
    return apps[token]

@app.post("/{full_path:path}")
async def telegram_webhook(full_path: str, request: Request):
    token = request.query_params.get("token", MAIN_BOT_TOKEN)
    db = load_db()
    is_main = (token == MAIN_BOT_TOKEN)
    admin_id = None
    
    if not is_main:
        uid = get_uid_by_token(token, db)
        if not uid: return {"ok": False, "error": "Bot Not Found"}
        bdata = db[uid]['bots'][token]
        admin_id = bdata['admin_id']
        if not is_bot_running(bdata):
            return {"ok": False, "error": "Bot Disabled"}

    application = await get_app(token, is_main, admin_id)
    update_dict = await request.json()
    update = Update.de_json(update_dict, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/{full_path:path}")
async def setup_webhooks(full_path: str):
    db = load_db()
    results = []
    bot = Bot(MAIN_BOT_TOKEN)
    await bot.set_webhook(f"{VERCEL_DOMAIN}/?token={MAIN_BOT_TOKEN}")
    results.append("Main Bot Setup Success")
    
    for uid, udata in db.items():
        if uid in ['global', '_states'] or not isinstance(udata, dict): continue
        for token, bdata in udata.get('bots', {}).items():
            if is_bot_running(bdata):
                cbot = Bot(token)
                await cbot.set_webhook(f"{VERCEL_DOMAIN}/?token={token}")
                results.append(f"Child Bot {token[:10]}... Setup Success")
    return {"status": "All Webhooks Connected!", "details": results}
