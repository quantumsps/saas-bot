import json
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, ConversationHandler
)
from upstash_redis import Redis

# === تنظیمات اصلی ===
MAIN_BOT_TOKEN = "8831950444:AAFB8jcSo_VjjcuRDMR0dtMSrwEezaVoAoQ"
ADMIN_ID = 8898908182  
REDIS_URL = "https://relieved-fawn-197670.upstash.io"
REDIS_TOKEN = "gQAAAAAAAwQmAAIgcDE2MmFlM2JjYTBmY2M0MzMyYjRiNzRlMTEyOWYyOGY4OA"
VERCEL_DOMAIN = "https://saas-bot-uyu8.vercel.app" 

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)
app = FastAPI()

def load_db():
    data = redis.get("saas_db")
    if data: return json.loads(data) if isinstance(data, str) else data
    return {'global': {'sales_open': True}}

def save_db(data):
    redis.set("saas_db", json.dumps(data))

def get_uid_by_token(token, db):
    for uid, udata in db.items():
        if uid != 'global' and token in udata.get('bots', {}): return uid
    return None

apps = {}

async def get_app(token: str, is_main: bool, admin_id=None):
    if token not in apps:
        application = Application.builder().token(token).build()
        if is_main: application.add_handler(main_conv_handler)
        else:
            application.bot_data['admin_id'] = admin_id
            application.add_handler(child_conv_handler)
        await application.initialize()
        apps[token] = application
    return apps[token]

# دریافت تمام پیام‌های تلگرام در هر مسیری (بدون خطای 404)
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
        exp_date = datetime.fromisoformat(bdata['expiry_date'])
        if exp_date < datetime.now() or bdata.get('admin_disabled') or bdata.get('user_disabled'):
            return {"ok": False, "error": "Bot Disabled"}

    application = await get_app(token, is_main, admin_id)
    update_dict = await request.json()
    update = Update.de_json(update_dict, application.bot)
    await application.process_update(update)
    return {"ok": True}

# فعال‌سازی خودکار با باز کردن هر آدرسی از سایت در مرورگر
@app.get("/{full_path:path}")
async def setup_webhooks(full_path: str):
    db = load_db()
    results = []
    bot = Bot(MAIN_BOT_TOKEN)
    await bot.set_webhook(f"{VERCEL_DOMAIN}/?token={MAIN_BOT_TOKEN}")
    results.append("Main Bot Setup Success")
    
    for uid, udata in db.items():
        if uid == 'global': continue
        for token, bdata in udata.get('bots', {}).items():
            if datetime.fromisoformat(bdata['expiry_date']) > datetime.now():
                cbot = Bot(token)
                await cbot.set_webhook(f"{VERCEL_DOMAIN}/?token={token}")
                results.append(f"Child Bot {token[:10]}... Setup Success")
    return {"status": "All Webhooks Connected!", "details": results}

async def main_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = load_db()
    if str(user_id) not in db:
        db[str(user_id)] = {'credits': [], 'bots': {}, 'used_free_trial': False}
        save_db(db)
    msg = "🤖 **به پنل مدیریت ربات‌ساز ناشناس خوش آمدید.**\nاز منوی زیر استفاده کنید:"
    keyboard = [[InlineKeyboardButton("🛒 خرید اشتراک", callback_data="buy_new")]]
    if update.message: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("سیستم با موفقیت به سرور ابری متصل است!", show_alert=True)
    return ConversationHandler.END

main_conv_handler = ConversationHandler(
    entry_points=[CommandHandler('start', main_start), CallbackQueryHandler(handle_callbacks, pattern="^.*$")],
    states={}, fallbacks=[]
)
child_conv_handler = ConversationHandler(entry_points=[], states={}, fallbacks=[])
