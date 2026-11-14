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
MAIN_BOT_TOKEN = "9018084227:AAFacpqEejBGK71LOO_2BiiX6PHiWJaMed4"
ADMIN_ID = 8898908182  
REDIS_URL = "https://relieved-fawn-197670.upstash.io"
REDIS_TOKEN = "gQAAAAAAAwQmAAIgcDE2MmFlM2JjYTBmY2M0MzMyYjRiNzRlMTEyOWYyOGY4OA"
VERCEL_DOMAIN = "https://saas-bot-uyu8.vercel.app" 

CARD_NUMBER = "6219861837792000"
CARD_NAME = "امیر ابراهیمی"
MAX_BOTS_PER_USER = 5

PLANS = {
    30: "50000",
    60: "80000",
    90: "120000"
}

# وضعیت‌های ربات اصلی
(WAITING_RECEIPT, WAITING_TOKEN, WAITING_ADMIN_ID, 
 WAITING_MANAGE_USER, WAITING_ADMIN_DAYS, WAITING_NEW_ADMIN_ID,
 WAITING_BOT_NAME, WAITING_CO_ADMIN, WAITING_CO_ADMIN_NAME, 
 WAITING_BROADCAST, WAITING_FORCE_JOIN,
 WAITING_SINGLE_USER_ID, WAITING_SINGLE_USER_TEXT) = range(1, 14)

# وضعیت‌های ربات‌های مشتری
(CHILD_WAITING_MSG, CHILD_ADMIN_REPLY, CHILD_WAITING_BROADCAST, 
 CHILD_WAITING_BAD_WORD, CHILD_WAITING_WARN_REASON) = range(1, 6)

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
        if 'username' not in udata: udata['username'] = "ندارد"
        elif isinstance(udata['credits'], int): db[uid]['credits'] = [30] * udata['credits']
        
        for t, bdata in udata.get('bots', {}).items():
            if 'bot_name' not in bdata: bdata['bot_name'] = ""
            if 'force_join' not in bdata: bdata['force_join'] = ""
            if 'co_admins' not in bdata: bdata['co_admins'] = {}
            if 'blocked_users' not in bdata: bdata['blocked_users'] = []
            if 'bad_words' not in bdata: bdata['bad_words'] = []
            if 'warns' not in bdata: bdata['warns'] = {}
            if 'known_users' not in bdata: bdata['known_users'] = []
            elif isinstance(bdata['co_admins'], list): 
                bdata['co_admins'] = {str(k): "بدون نام" for k in bdata['co_admins']}
    return db

def save_db(data):
    redis.set("saas_db", json.dumps(data))

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

def track_user(token, user_id):
    db = load_db()
    uid = get_uid_by_token(token, db)
    if uid:
        bdata = db[uid]['bots'][token]
        if 'known_users' not in bdata: bdata['known_users'] = []
        if user_id not in bdata['known_users']:
            bdata['known_users'].append(user_id)
            save_db(db)

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
        msg = (
            f"<tg-emoji emoji-id=\"5818813162815753343\">👨‍💻</tg-emoji> <b>سلام {role} عزیز! به ربات خودت خوش اومدی.</b>\n\n"
            f"<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> ربات فعال است و پیام‌ها برای شما فوروارد خواهد شد.\n\n"
            f"<tg-emoji emoji-id=\"5462921117423384478\">🛠</tg-emoji> <b>ابزارهای مدیریتی شما:</b>\n"
            f"• ارسال پیام همگانی به کاربران ربات\n"
            f"• سیستم فیلتر کلمات رکیک\n"
            f"• سیستم اخطار و مسدودسازی هوشمند"
        )
        keyboard = [
            [InlineKeyboardButton("📢 ارسال پیام همگانی به کاربران", callback_data="cadmin_broadcast")],
            [InlineKeyboardButton("🚫 مدیریت کلمات رکیک", callback_data="cadmin_badwords"),
             InlineKeyboardButton("⚙️ تنظیمات ربات", callback_data="cadmin_panel")]
        ]
        if update.message: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        else: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
        
    if is_user_blocked(token, user_id, db):
        if update.message: await update.message.reply_text("⛔️ شما از طرف مدیریت این ربات مسدود شده‌اید.")
        return

    track_user(token, user_id)

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

    is_admin = (user_id == admin_id or str(user_id) in co_admins)

    if data == "start_anon":
        if is_user_blocked(token, user_id, db):
            await query.message.reply_text("⛔️ شما مسدود شده‌اید.")
            return
            
        track_user(token, user_id)
        force_join = bdata.get('force_join')
        if force_join and not is_admin:
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
        warns_count = bdata.get('warns', {}).get(str(user_id), 0)
        
        admin_keyboard = [
            [InlineKeyboardButton("💬 پاسخ دادن", callback_data=f"creply_{user_id}")],
            [InlineKeyboardButton(f"⚠️ اخطار ({warns_count})", callback_data=f"cwarn_{user_id}"),
             InlineKeyboardButton("🚫 مسدودیت", callback_data=f"cblock_{user_id}")]
        ]
        
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
        await query.edit_message_text("❌ عملیات لغو شد.")

    elif data.startswith("cblock_"):
        if not is_admin: return
        target_anon_id = int(data.split('_')[1])
        block_user_in_db(token, target_anon_id)
        try: await query.edit_message_reply_markup(reply_markup=None)
        except: pass
        await query.message.reply_text("✅ کاربر با موفقیت مسدود شد.")

    elif data.startswith("cwarn_"):
        if not is_admin: return
        target_anon_id = int(data.split('_')[1])
        if 'warns' not in bdata: bdata['warns'] = {}
        curr_warns = bdata['warns'].get(str(target_anon_id), 0) + 1
        bdata['warns'][str(target_anon_id)] = curr_warns
        save_db(db)
        
        try:
            await context.bot.send_message(
                chat_id=target_anon_id, 
                text=f"⚠️ **اخطار از طرف مدیریت!**\nتعداد اخطارهای شما: {curr_warns} از ۳\nلطفاً در ارسال پیام‌ها دقت کنید، در صورت تکرار مسدود خواهید شد.",
                parse_mode='Markdown'
            )
        except: pass
        
        if curr_warns >= 3:
            block_user_in_db(token, target_anon_id)
            await query.message.reply_text(f"⚠️ کاربر به دلیل رسیدن به ۳ اخطار، به صورت خودکار **مسدود** شد.")
        else:
            await query.message.reply_text(f"✅ اخطار شماره {curr_warns} به کاربر ارسال شد.")

    elif data.startswith("creply_"):
        if not is_admin: return
        target_id = int(data.split('_')[1])
        set_state(db, bot_id, user_id, CHILD_ADMIN_REPLY, {'reply_target': target_id})
        await query.message.reply_text("✍️ پاسخ خود را به این کاربر بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو پاسخ", callback_data="cancel_reply")]]))

    elif data == "cadmin_broadcast":
        if not is_admin: return
        set_state(db, bot_id, user_id, CHILD_WAITING_BROADCAST)
        await query.edit_message_text("📢 پیام همگانی خود را برای ارسال به تمام کاربران این ربات بفرستید (متن، عکس، ویس و...):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="cancel_reply")]]))

    elif data == "cadmin_badwords":
        if not is_admin: return
        words = bdata.get('bad_words', [])
        words_str = ", ".join([f"`{w}`" for w in words]) if words else "هیچ کلمه‌ای ثبت نشده است."
        msg = f"🚫 **مدیریت کلمات رکیک و فیلتر اسپم:**\n\nکلمات فعلی:\n{words_str}\n\nبرای **اضافه کردن** یک کلمه جدید، آن را ارسال کنید.\nبرای **پاکسازی کامل لیست** کلمه `پاکسازی` را بفرستید."
        set_state(db, bot_id, user_id, CHILD_WAITING_BAD_WORD)
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منو", callback_data="cancel_reply")]]), parse_mode='Markdown')

    elif data == "cadmin_panel":
        if not is_admin: return
        await child_start(update, context)

async def child_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    token = context.bot.token
    bot_id = token.split(':')[0]
    db = load_db()
    uid = get_uid_by_token(token, db)
    bdata = db.get(uid, {}).get('bots', {}).get(token, {})
    co_admins = bdata.get('co_admins', {})
    admin_id = context.bot_data['admin_id']
    is_admin = (user_id == admin_id or str(user_id) in co_admins)
    
    if not is_admin:
        track_user(token, user_id)

    st = get_state(db, bot_id, user_id)
    state = st['state']

    if is_admin and state == CHILD_WAITING_BROADCAST:
        set_state(db, bot_id, user_id, None)
        success = 0
        await update.message.reply_text("⏳ در حال ارسال پیام همگانی...")
        
        all_chats = set(bdata.get('known_users', []))
        for w_uid in bdata.get('warns', {}).keys():
            all_chats.add(int(w_uid))
        for b_uid in bdata.get('blocked_users', []):
            all_chats.add(b_uid)
            
        for target_u in all_chats:
            try:
                await update.message.copy(chat_id=target_u)
                success += 1
            except: pass
        await update.message.reply_text(f"✅ پیام همگانی با موفقیت به {success} کاربر ارسال شد.")
        return

    if is_admin and state == CHILD_WAITING_BAD_WORD:
        text = (update.message.text or "").strip()
        set_state(db, bot_id, user_id, None)
        if text == "پاکسازی":
            bdata['bad_words'] = []
            save_db(db)
            await update.message.reply_text("✅ لیست کلمات مسدود پاکسازی شد.")
            return
        if text:
            if 'bad_words' not in bdata: bdata['bad_words'] = []
            if text not in bdata['bad_words']:
                bdata['bad_words'].append(text)
                save_db(db)
            await update.message.reply_text(f"✅ کلمه `{text}` به لیست فیلتر اضافه شد.", parse_mode='Markdown')
        return

    if not is_admin:
        bad_words = bdata.get('bad_words', [])
        if bad_words and update.message.text:
            msg_text = update.message.text.lower()
            if any(bw.lower() in msg_text for bw in bad_words):
                await update.message.reply_text("⛔️ پیام شما به دلیل استفاده از کلمات نامعتبر یا رکیک توسط ربات مسدود شد!")
                return

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
    user = update.effective_user
    user_id = user.id
    db = load_db()
    
    if str(user_id) not in db:
        db[str(user_id)] = {'credits': [], 'bots': {}, 'used_free_trial': False, 'username': user.username or "ندارد"}
        save_db(db)
    else:
        if user.username:
            db[str(user_id)]['username'] = user.username
            save_db(db)
        
    set_state(db, "main", user_id, None)
        
    if user_id == ADMIN_ID:
        sales_status = "🟢 باز" if db['global'].get('sales_open', True) else "🔴 بسته"
        msg = "👨‍💻 **پنل مدیریت ربات‌ساز (مدیر کل)**\n\nیکی از گزینه‌های زیر را انتخاب کنید:"
        keyboard = [
            [InlineKeyboardButton("👥 لیست و وضعیت کاربران", callback_data="admin_users_list")],
            [InlineKeyboardButton("⚙️ مدیریت کاربران", callback_data="admin_manage_user")],
            [InlineKeyboardButton("📨 ارسال پیام تکی به کاربر", callback_data="admin_single_msg")],
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
            "<tg-emoji emoji-id=\"5373098009640836781\">📚</tg-emoji> <b>راهنمای جامع پلتفرم ربات‌ساز پیام ناشناس</b>\n\n"
            "به سیستم هوشمند ساخت و مدیریت ربات‌های پیام ناشناس خوش آمدید! با این پنل می‌توانید ربات اختصاصی خود را با امکانات پیشرفته مدیریت کنید.\n\n"
            "<tg-emoji emoji-id=\"5318985656548999952\">🛒</tg-emoji> <b>۱. تهیه اشتراک:</b>\n"
            "برای شروع، از منوی اصلی روی «خرید اشتراک جدید» کلیک کنید. شما می‌توانید از <b>پلن ۷ روزه رایگان</b> (فقط برای یک‌بار) استفاده کنید و یا پلن‌های ۱ تا ۳ ماهه را با پرداخت کارت‌به‌کارت خریداری کنید.\n\n"
            "<tg-emoji emoji-id=\"5330115548900501467\">🔑</tg-emoji> <b>۲. راه‌اندازی ربات:</b>\n"
            "پس از فعال شدن اشتراک، روی «راه‌اندازی ربات از اشتراک» کلیک کنید:\n"
            "<tg-emoji emoji-id=\"5951779294396028785\">🔹</tg-emoji> ابتدا توکن ربات خود را (که از @BotFather دریافت کرده‌اید) بفرستید.\n"
            "<tg-emoji emoji-id=\"5972265777296838427\">🔹</tg-emoji> سپس شناسه عددی تلگرام خود را وارد کنید تا ربات فوراً استارت بخورد.\n\n"
            "<tg-emoji emoji-id=\"5341715473882955310\">⚙️</tg-emoji> <b>۳. مدیریت و تنظیمات پیشرفته (بخش مدیریت ربات‌های من):</b>\n"
            "با ورود به این بخش، امکانات حرفه‌ای زیر در اختیار شماست:\n"
            "<tg-emoji emoji-id=\"5318995457664369219\">🔒</tg-emoji> <b>قفل عضویت اجباری:</b> آیدی کانال خود را (با @) ثبت کنید تا کاربران قبل از ارسال پیام، مجبور به عضویت در کانال شما شوند. (<i><tg-emoji emoji-id=\"4956611513369494230\">⚠️</tg-emoji> ربات حتماً باید در کانال شما ادمین باشد</i>).\n"
            "<tg-emoji emoji-id=\"5318803171978526909\">👥</tg-emoji> <b>ادمین‌های فرعی:</b> می‌توانید آیدی دوستان یا همکارانتان را ثبت کنید تا پیام‌های ناشناس برای آن‌ها هم ارسال شود و بتوانند پاسخ دهند.\n"
            "<tg-emoji emoji-id=\"5470060791883374114\">✍️</tg-emoji> <b>نام نمایشی:</b> یک اسم دلخواه برای ربات خود تعیین کنید تا پیام خوش‌آمدگویی جذاب‌تر شود.\n"
            "<tg-emoji emoji-id=\"5882090035519429111\">🔴</tg-emoji>/<tg-emoji emoji-id=\"5188234920639632382\">🟢</tg-emoji> <b>روشن/خاموش:</b> هر زمان که خواستید دریافت پیام ناشناس را موقتاً متوقف کنید.\n"
            "<tg-emoji emoji-id=\"5938302546014638461\">❌</tg-emoji> <b>فیلتر کلمات رکیک:</b> از داخل ربات خودتان کلمات بد را فیلتر کنید.\n"
            "<tg-emoji emoji-id=\"4956611513369494230\">⚠️</tg-emoji> <b>سیستم اخطار (Warn):</b> کاربران مزاحم را اخطار دهید یا مسدود کنید.\n\n"
            "<tg-emoji emoji-id=\"5318867613667835128\">💬</tg-emoji> <b>۴. چت دوطرفه و بلاک (ضد اسپم):</b>\n"
            "هر پیامی که در ربات دریافت می‌کنید، دارای دکمه‌های شیشه‌ای است:\n"
            "<tg-emoji emoji-id=\"5951940647727403709\">🔹</tg-emoji> با زدن <b>«<tg-emoji emoji-id=\"5938359183748370657\">💬</tg-emoji> پاسخ دادن»</b>، می‌توانید مستقیماً و به صورت ناشناس جواب شخص را بدهید.\n"
            "<tg-emoji emoji-id=\"5951940647727403709\">🔹</tg-emoji> با زدن <b>«<tg-emoji emoji-id=\"5938290000415167172\">❌</tg-emoji> مسدودیت»</b>، کاربر مزاحم برای همیشه بلاک شده و دیگر نمی‌تواند به شما پیام بدهد."
        )
        await query.edit_message_text(
            help_text, 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="cancel_flow")]]), 
            parse_mode='HTML'
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

    elif data == "admin_single_msg":
        if int(user_id) != ADMIN_ID: return
        set_state(db, "main", user_id, WAITING_SINGLE_USER_ID)
        await query.edit_message_text("👤 لطفاً **شناسه عددی (User ID)** کاربری که می‌خواهید به او پیام بفرستید را وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))

    elif data == "admin_send_single_confirm":
        if int(user_id) != ADMIN_ID: return
        st = get_state(db, "main", user_id)
        sdata = st['data']
        t_id = sdata.get('target_user_id')
        msg_text = sdata.get('msg_text')
        set_state(db, "main", user_id, None)
        try:
            await context.bot.send_message(chat_id=t_id, text=f"📬 **پیام از طرف مدیریت:**\n\n{msg_text}", parse_mode='Markdown')
            await query.edit_message_text("✅ پیام با موفقیت به کاربر ارسال شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="cancel_flow")]]))
        except Exception as e:
            await query.edit_message_text(f"❌ خطا در ارسال پیام به کاربر:\n{e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="cancel_flow")]]))

    elif data == "admin_change_single_text":
        if int(user_id) != ADMIN_ID: return
        set_state(db, "main", user_id, WAITING_SINGLE_USER_TEXT, get_state(db, "main", user_id)['data'])
        await query.edit_message_text("✍️ لطفاً **متن جدید** پیام خود را بنویسید و بفرستید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))

    elif data == "admin_users_list":
        if int(user_id) != ADMIN_ID: return
        real_buyers = []
        for u_key, u_val in db.items():
            if u_key in ['global', '_states'] or not isinstance(u_val, dict): continue
            has_bought = len(u_val.get('credits', [])) > 0 or len(u_val.get('bots', {})) > 0
            if has_bought or int(u_key) == ADMIN_ID:
                real_buyers.append(u_key)
                
        msg = f"👥 **آمار خریداران و مشتریان واقعی:** {len(real_buyers)} نفر\n\n📋 **لیست آخرین خریداران:**\n"
        for u in real_buyers[-20:]:
            u_info = db[u]
            uname = f"@{u_info.get('username', 'ندارد')}" if u_info.get('username') != "ندارد" else "بدون آیدی"
            b_cnt = len(u_info.get('bots', {}))
            c_cnt = len(u_info.get('credits', []))
            msg += f"👤 `{u}` | {uname} | ربات فعال: {b_cnt} | اعتبار: {c_cnt}\n"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]), parse_mode='Markdown')

    elif data == "admin_manage_user":
        if int(user_id) != ADMIN_ID: return
        set_state(db, "main", user_id, WAITING_MANAGE_USER)
        await query.edit_message_text("👤 لطفاً **شناسه عددی (User ID)** کاربر مورد نظر را ارسال کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))

    elif data.startswith("adm_bot_"):
        if int(user_id) != ADMIN_ID: return
        _, _, target_uid, b_id = data.split('_')
        token = get_token_by_id(target_uid, b_id, db)
        if not token: return
        bdata = db[target_uid]['bots'][token]
        days_left = max(0, (datetime.fromisoformat(bdata['expiry_date']) - datetime.now()).days)
        running = is_bot_running(bdata)
        
        msg = (
            f"🤖 **مدیریت ربات `{b_id}` متعلق به کاربر `{target_uid}`**\n\n"
            f"⏳ زمان باقی‌مانده: {days_left} روز\n"
            f"وضعیت سرور: {'فعال 🟢' if running else 'غیرفعال 🔴'}\n"
            f"مسدودیت ادمین کل: {'⛔️ مسدود' if bdata.get('admin_disabled') else '✅ آزاد'}"
        )
        keyboard = [
            [InlineKeyboardButton("➕/➖ کاهش یا افزایش اشتراک", callback_data=f"admaddcred_{target_uid}_{b_id}")],
            [InlineKeyboardButton(f"{'🔴 غیرفعال کردن ربات' if running else '🟢 فعال کردن ربات'}", callback_data=f"admtoggle_{target_uid}_{b_id}")],
            [InlineKeyboardButton("🔑 دریافت توکن ربات", callback_data=f"admtoken_{target_uid}_{b_id}")],
            [InlineKeyboardButton("🔙 بازگشت به لیست ربات‌ها", callback_data=f"admbackbots_{target_uid}")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("admtoken_"):
        if int(user_id) != ADMIN_ID: return
        _, target_uid, b_id = data.split('_')
        token = get_token_by_id(target_uid, b_id, db)
        if token:
            await query.message.reply_text(f"🔑 توکن ربات `{b_id}`:\n\n`{token}`\n\n*(برای کپی کردن روی توکن ضربه بزنید)*", parse_mode='Markdown')
            await query.answer()

    elif data.startswith("admbackbots_"):
        if int(user_id) != ADMIN_ID: return
        target_uid = data.split('_')[1]
        udata = db.get(target_uid, {})
        bots = udata.get('bots', {})
        if not bots:
            await query.edit_message_text("❌ این کاربر هیچ رباتی ندارد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
            return
        keyboard = [[InlineKeyboardButton(f"🤖 ربات {t.split(':')[0]}", callback_data=f"adm_bot_{target_uid}_{t.split(':')[0]}")] for t in bots.keys()]
        keyboard.append([InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="cancel_flow")])
        await query.edit_message_text(f"🤖 **ربات‌های کاربر `{target_uid}` را انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("admaddcred_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        target_uid = parts[1]
        b_id = parts[2] if len(parts) > 2 else ""
        set_state(db, "main", user_id, WAITING_ADMIN_DAYS, {'target_uid': target_uid, 'target_bid': b_id})
        await query.edit_message_text(f"➕/➖ **تغییر اشتراک ربات `{b_id}` برای کاربر `{target_uid}`**\n\nتعداد روزهای مورد نظر را وارد کنید (برای افزایش عدد مثبت مثلاً `30` و برای کاهش عدد منفی مثلاً `-10` بفرستید):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data=f"adm_bot_{target_uid}_{b_id}")]]), parse_mode='Markdown')

    elif data.startswith("admtoggle_"):
        if int(user_id) != ADMIN_ID: return
        _, target_uid, b_id = data.split('_')
        token = get_token_by_id(target_uid, b_id, db)
        if token:
            bdata = db[target_uid]['bots'][token]
            curr = bdata.get('admin_disabled', False)
            bdata['admin_disabled'] = not curr
            save_db(db)
            await sync_bot_state(target_uid, token, db)
            
            running = is_bot_running(bdata)
            days_left = max(0, (datetime.fromisoformat(bdata['expiry_date']) - datetime.now()).days)
            
            msg = (
                f"🤖 **مدیریت ربات `{b_id}` متعلق به کاربر `{target_uid}`**\n\n"
                f"⏳ زمان باقی‌مانده: {days_left} روز\n"
                f"وضعیت سرور: {'فعال 🟢' if running else 'غیرفعال 🔴'}\n"
                f"مسدودیت ادمین کل: {'⛔️ مسدود' if bdata.get('admin_disabled') else '✅ آزاد'}"
            )
            keyboard = [
                [InlineKeyboardButton("➕/➖ کاهش یا افزایش اشتراک", callback_data=f"admaddcred_{target_uid}_{b_id}")],
                [InlineKeyboardButton(f"{'🔴 غیرفعال کردن ربات' if running else '🟢 فعال کردن ربات'}", callback_data=f"admtoggle_{target_uid}_{b_id}")],
                [InlineKeyboardButton("🔑 دریافت توکن ربات", callback_data=f"admtoken_{target_uid}_{b_id}")],
                [InlineKeyboardButton("🔙 بازگشت به لیست ربات‌ها", callback_data=f"admbackbots_{target_uid}")]
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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
            [InlineKeyboardButton("1 ماهه - 50 هزار تومان", callback_data="buyplan_new_30")],
            [InlineKeyboardButton("2 ماهه - 80 هزار تومان", callback_data="buyplan_new_60")],
            [InlineKeyboardButton("3 ماهه - 120 هزار تومان", callback_data="buyplan_new_90")],
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
            [InlineKeyboardButton("1 ماهه - 50 هزار تومان", callback_data=f"buyplan_ren_30_{b_id}")],
            [InlineKeyboardButton("2 ماهه - 80 هزار تومان", callback_data=f"buyplan_ren_60_{b_id}")],
            [InlineKeyboardButton("3 ماهه - 120 هزار تومان", callback_data=f"buyplan_ren_90_{b_id}")],
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
            f"🔑 **توکن ربات:** `{token}`\n"
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

    # --- تایید یا رد فیش‌های واریزی ---
    elif data.startswith("approve_new_") or data.startswith("reject_new_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        target_uid = parts[2]
        
        if parts[0] == "approve":
            days = int(parts[3])
            if target_uid not in db: db[target_uid] = {'credits': [], 'bots': {}, 'used_free_trial': False, 'username': 'ندارد'}
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
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_id, caption=f"🧾 **{caption_txt}**\n\n👤 از طرف: `{user.id}` (@{user.username or 'ندارد'})", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
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
            'bot_name': "", 'co_admins': {}, 'force_join': "", 'bad_words': [], 'warns': {}, 'known_users': []
        }
        set_state(db, "main", user_id, None)
        await sync_bot_state(user_id, token, db)
        await update.message.reply_text(f"🚀 **ربات شما ساخته و برای {days} روز روشن شد!**\nهم‌اکنون می‌توانید وارد ربات خود شوید و /start را بزنید.", parse_mode='Markdown')

    elif state == WAITING_SINGLE_USER_ID:
        if int(user_id) != ADMIN_ID: return
        t_id_text = (update.message.text or "").strip()
        if not t_id_text.isdigit():
            await update.message.reply_text("❌ شناسه عددی باید فقط شامل عدد باشد. مجدداً بفرستید:")
            return
        set_state(db, "main", user_id, WAITING_SINGLE_USER_TEXT, {'target_user_id': int(t_id_text)})
        await update.message.reply_text("✍️ حالا **متن پیامی** که می‌خواهید به این کاربر بفرستید را بنویسید و ارسال کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))

    elif state == WAITING_SINGLE_USER_TEXT:
        if int(user_id) != ADMIN_ID: return
        msg_text = update.message.text
        set_state(db, "main", user_id, WAITING_SINGLE_USER_TEXT, {'msg_text': msg_text})
        
        preview_msg = (
            f"🔍 **پیش‌نمایش پیام تکی:**\n\n"
            f"👤 گیرنده (User ID): `{sdata.get('target_user_id')}`\n\n"
            f"📄 **متن پیام:**\n{msg_text}\n\n"
            f"آیا می‌خواهید این پیام را ارسال کنید؟"
        )
        keyboard = [
            [InlineKeyboardButton("✅ بله، ارسال شود", callback_data="admin_send_single_confirm")],
            [InlineKeyboardButton("✏️ تغییر متن", callback_data="admin_change_single_text")],
            [InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")]
        ]
        await update.message.reply_text(preview_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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
        bots = udata.get('bots', {})
        if not bots:
            await update.message.reply_text(f"❌ کاربر `{target_uid}` هیچ رباتی نساخته است.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="cancel_flow")]]), parse_mode='Markdown')
            return
            
        keyboard = [[InlineKeyboardButton(f"🤖 ربات {t.split(':')[0]}", callback_data=f"adm_bot_{target_uid}_{t.split(':')[0]}")] for t in bots.keys()]
        keyboard.append([InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="cancel_flow")])
        await update.message.reply_text(f"👤 کاربر مورد نظر یافت شد. لطفاً **ربات مورد نظر** را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif state == WAITING_ADMIN_DAYS:
        if int(user_id) != ADMIN_ID: return
        txt_days = (update.message.text or "").strip()
        try:
            days_change = int(txt_days)
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید (مثلا 30 یا -10):")
            return
            
        target_uid = sdata.get('target_uid')
        target_bid = sdata.get('target_bid')
        set_state(db, "main", user_id, None)
        
        token = get_token_by_id(target_uid, target_bid, db)
        if token and target_uid in db and token in db[target_uid]['bots']:
            curr_exp = datetime.fromisoformat(db[target_uid]['bots'][token]['expiry_date'])
            base_time = curr_exp if curr_exp > datetime.now() else datetime.now()
            new_exp = base_time + timedelta(days=days_change)
            db[target_uid]['bots'][token]['expiry_date'] = new_exp.isoformat()
            save_db(db)
            await sync_bot_state(target_uid, token, db)
            
            await update.message.reply_text(f"✅ تاریخ انقضای ربات `{target_bid}` با موفقیت به میزان {days_change} روز تغییر یافت.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به تنظیمات ربات", callback_data=f"adm_bot_{target_uid}_{target_bid}")]]), parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ خطا در پیدا کردن ربات کاربر.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="cancel_flow")]]))

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
