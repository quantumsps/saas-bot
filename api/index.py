import json
import os
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    filters, ContextTypes, ConversationHandler
)
from upstash_redis import Redis

# === تنظیمات اصلی ===
MAIN_BOT_TOKEN = "8831950444:AAFB8jcSo_VjjcuRDMR0dtMSrwEezaVoAoQ"
ADMIN_ID = 8898908182  
REDIS_URL = "https://relieved-fawn-197670.upstash.io"
REDIS_TOKEN = "gQAAAAAAAwQmAAIgcDE2MmFlM2JjYTBmY2M0MzMyYjRiNzRlMTEyOWYyOGY4OA"

# آدرس سایت ورسل شما (بعد از دیپلوی باید اینجا آدرسی که ورسل بهت میده رو جایگذاری کنی)
VERCEL_DOMAIN = "https://your-project-name.vercel.app" 

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)
app_fastapi = FastAPI()

# ==========================================
# مدیریت دیتابیس در فضای ابری (Upstash Redis)
# ==========================================
def load_db():
    data = redis.get("saas_db")
    if data:
        return json.loads(data) if isinstance(data, str) else data
    # ساختار اولیه دیتابیس
    return {'global': {'sales_open': True}}

def save_db(data):
    redis.set("saas_db", json.dumps(data))

def get_uid_by_token(token, db):
    for uid, udata in db.items():
        if uid != 'global' and token in udata.get('bots', {}): return uid
    return None

# ==========================================
# تنظیمات وب‌هوک (FastAPI) برای ورسل
# ==========================================
apps = {}

async def get_app(token: str, is_main: bool, admin_id=None):
    if token not in apps:
        application = Application.builder().token(token).build()
        if is_main:
            # هندلر ربات اصلی رو اضافه می‌کنیم
            application.add_handler(main_conv_handler)
        else:
            # هندلر ربات‌های مشتری رو اضافه می‌کنیم
            application.bot_data['admin_id'] = admin_id
            application.add_handler(child_conv_handler)
        await application.initialize()
        apps[token] = application
    return apps[token]

@app_fastapi.post("/api/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    db = load_db()
    is_main = (token == MAIN_BOT_TOKEN)
    admin_id = None
    
    if not is_main:
        uid = get_uid_by_token(token, db)
        if not uid: return {"ok": False, "error": "Bot Not Found"}
        bdata = db[uid]['bots'][token]
        admin_id = bdata['admin_id']
        
        # بررسی انقضا و مسدودی
        exp_date = datetime.fromisoformat(bdata['expiry_date'])
        if exp_date < datetime.now() or bdata.get('admin_disabled') or bdata.get('user_disabled'):
            return {"ok": False, "error": "Bot Disabled"}

    application = await get_app(token, is_main, admin_id)
    
    # دریافت پیام از تلگرام و پردازش
    update_dict = await request.json()
    update = Update.de_json(update_dict, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app_fastapi.get("/api/setup")
async def setup_webhooks():
    db = load_db()
    results = []
    
    # تنظیم هوک ربات اصلی
    bot = Bot(MAIN_BOT_TOKEN)
    await bot.set_webhook(f"{VERCEL_DOMAIN}/api/webhook/{MAIN_BOT_TOKEN}")
    results.append("Main Bot Setup Success")
    
    # تنظیم هوک ربات‌های زیرمجموعه
    for uid, udata in db.items():
        if uid == 'global': continue
        for token, bdata in udata.get('bots', {}).items():
            if datetime.fromisoformat(bdata['expiry_date']) > datetime.now():
                cbot = Bot(token)
                await cbot.set_webhook(f"{VERCEL_DOMAIN}/api/webhook/{token}")
                results.append(f"Child Bot {token[:10]}... Setup Success")
                
    return {"status": "All Webhooks Connected to Vercel!", "details": results}

# ==========================================
# هندلرها (همان کدهای منوی ربات اصلی و مشتری)
# ==========================================
async def main_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = load_db()
    
    if str(user_id) not in db:
        db[str(user_id)] = {'credits': [], 'bots': {}, 'used_free_trial': False}
        save_db(db)
        
    msg = "🤖 **به پنل مدیریت ربات‌ساز ناشناس خوش آمدید.**\nاز منوی زیر استفاده کنید:"
    keyboard = [
        [InlineKeyboardButton("🛒 خرید اشتراک (پشتیبانی از تست رایگان)", callback_data="buy_new")],
        [InlineKeyboardButton("❓ راهنمای استفاده", callback_data="user_help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else: await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    return ConversationHandler.END

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("این نسخه ابری با موفقیت به Upstash متصل است!", show_alert=True)
    return ConversationHandler.END

# (توجه: برای خلاصه شدن در این مرحله تنظیمات اولیه سرور، هندلرهای پیچیده در اینجا خلاصه شده‌اند. 
# پس از اتصال موفق به ورسل، سورس کامل هندلرها را در آپدیت بعدی یکجا تزریق می‌کنیم).

main_conv_handler = ConversationHandler(
    entry_points=[CommandHandler('start', main_start), CallbackQueryHandler(handle_callbacks, pattern="^.*$")],
    states={}, fallbacks=[]
)
child_conv_handler = ConversationHandler(entry_points=[], states={}, fallbacks=[])
