import os
import json
import asyncio
import random
import requests
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    filters, ContextTypes, ConversationHandler
)
from telegram.error import BadRequest

# ==========================================
# ⚙️ تنظیمات اصلی و سراسری
# ==========================================
MAIN_BOT_TOKEN = "8842980077:AAGZ2bQ_1OC3pESktNy5FI_6M_49UNvNJWE" # توکن ربات ساز
ADMIN_ID = 8898908182  # آیدی شما (مالک ربات ساز)

# ⚠️ در ورسل فقط پوشه tmp قابل نوشتن است
DB_FILE = "/tmp/saas_database.json" 
VERCEL_DOMAIN = "saas-bot-uyu8.vercel.app" 

CARD_NUMBER = "6219861837792000"
CARD_NAME = "امیر ابراهیمی"
MAX_BOTS_PER_USER = 5

CHANNELS = [{"id": "@quantumvaulte", "link": "https://t.me/quantumvaulte"}]
WINNER_STICKER_ID = "CAACAgIAAxkBAAE... (آیدی استیکر جایزه)"

(WAITING_RECEIPT, WAITING_TOKEN, WAITING_BOT_USERNAME, WAITING_ADMIN_ID, WAITING_FORCE_JOIN, 
 WAITING_BOT_NAME, WAITING_CO_ADMIN, WAITING_CO_ADMIN_NAME, WAITING_NEW_ADMIN_ID, WAITING_BROADCAST, WAITING_MANAGE_USER,
 WAITING_EDIT_BOT_USERNAME, WAITING_DISCOUNT, WAITING_GLOBAL_PRICE, WAITING_GLOBAL_FORCE_JOIN, WAITING_GLOBAL_LOG_CH,
 WAITING_DISCOUNT_NAME, WAITING_DISCOUNT_PERCENT, WAITING_DISCOUNT_USES, WAITING_MOD_DAYS, WAITING_ADMIN_SEND_MSG) = range(1, 22)

CHILD_WAITING_MSG, CHILD_ADMIN_REPLY = 1, 2

# ==========================================
# 🗄 دیتابیس پنل مرکزی (SaaS)
# ==========================================
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            db = json.load(f)
            if 'global' not in db: db['global'] = {'sales_open': True}
            if 'prices' not in db['global']:
                db['global']['prices'] = {'anon': {'30': "100000", '60': "180000", '90': "270000"}, 'lottery': {'30': "150000", '60': "250000", '90': "350000"}}
            if 'discounts' not in db['global']: db['global']['discounts'] = {}
            if 'force_join' not in db['global']: db['global']['force_join'] = ""
            if 'log_channel' not in db['global']: db['global']['log_channel'] = ""
            
            for uid, data in db.items():
                if uid == 'global': continue
                if 'used_free_trial' not in data: data['used_free_trial'] = {'anon': False, 'lottery': False}
                elif isinstance(data['used_free_trial'], bool):
                    val = data['used_free_trial']
                    data['used_free_trial'] = {'anon': val, 'lottery': val}
                if 'bots' not in data: db[uid]['bots'] = {}
                if 'credits' not in data: db[uid]['credits'] = []
                elif len(data['credits']) > 0 and isinstance(data['credits'][0], int): 
                    db[uid]['credits'] = [{'type': 'anon', 'days': d} for d in data['credits']]
                
                for t, bdata in data.get('bots', {}).items():
                    if 'bot_type' not in bdata: bdata['bot_type'] = 'anon'
                    if 'bot_username' not in bdata: bdata['bot_username'] = "نامشخص"
                    if 'bot_name' not in bdata: bdata['bot_name'] = ""
                    if 'force_join' not in bdata: bdata['force_join'] = ""
                    if 'co_admins' not in bdata: bdata['co_admins'] = {}
            return db
    return {
        'global': {
            'sales_open': True,
            'prices': {'anon': {'30': "100000", '60': "180000", '90': "270000"}, 'lottery': {'30': "150000", '60': "250000", '90': "350000"}},
            'discounts': {}, 'force_join': "", 'log_channel': ""
        }
    }

def save_db(data):
    try:
        with open(DB_FILE, 'w') as f: json.dump(data, f, indent=4)
    except: pass

def get_token_by_id(uid, bot_id, db):
    for t in db.get(uid, {}).get('bots', {}).keys():
        if t.split(':')[0] == str(bot_id): return t
    return None

def get_uid_by_token(token, db):
    for uid, udata in db.items():
        if uid != 'global' and token in udata.get('bots', {}): return uid
    return None

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

# ==========================================
# 🎁 هسته ربات قرعه‌کشی 
# ==========================================
def l_load_data(db_file, bot_data):
    if os.path.exists(db_file):
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                bot_data['all_users'] = set(data.get("all_users", []))
                
                l_data = data.get("lottery", {})
                bot_data['lottery'] = {
                    "status": l_data.get("status", "INACTIVE"),
                    "topic": l_data.get("topic", "تعیین نشده"),
                    "winners_count": l_data.get("winners_count", 1),
                    "prizes": l_data.get("prizes", [])
                }
                
                s_dt = l_data.get("start_dt")
                e_dt = l_data.get("end_dt")
                bot_data['lottery']["start_dt"] = datetime.fromisoformat(s_dt) if s_dt else None
                bot_data['lottery']["end_dt"] = datetime.fromisoformat(e_dt) if e_dt else None
                bot_data['participants'] = {int(k): v for k, v in data.get("participants", {}).items()}
        except: pass
    else:
        bot_data['all_users'] = set()
        bot_data['lottery'] = {"status": "INACTIVE", "topic": "تعیین نشده", "winners_count": 1, "prizes": [], "start_dt": None, "end_dt": None}
        bot_data['participants'] = {}
        
    bot_data.setdefault('admin_states', {})
    bot_data.setdefault('admin_temp_data', {})

def l_save_data(db_file, bot_data):
    try:
        data = {
            "all_users": list(bot_data.get('all_users', set())),
            "lottery": {
                "status": bot_data['lottery']["status"],
                "topic": bot_data['lottery']["topic"],
                "winners_count": bot_data['lottery']["winners_count"],
                "prizes": bot_data['lottery']["prizes"],
                "start_dt": bot_data['lottery']["start_dt"].isoformat() if bot_data['lottery'].get("start_dt") else None,
                "end_dt": bot_data['lottery']["end_dt"].isoformat() if bot_data['lottery'].get("end_dt") else None
            },
            "participants": bot_data.get('participants', {})
        }
        with open(db_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except: pass

def get_admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 ایجاد قرعه‌کشی جدید", callback_data="admin_new"), InlineKeyboardButton("⏳ ویرایش زمان", callback_data="admin_edit_time")],
        [InlineKeyboardButton("📢 پیام همگانی", callback_data="admin_broadcast"), InlineKeyboardButton("👥 لیست شرکت‌کنندگان", callback_data="admin_list")],
        [InlineKeyboardButton("📊 وضعیت", callback_data="admin_status"), InlineKeyboardButton("📝 راهنمای جامع ربات", callback_data="admin_help")]
    ])

def get_user_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 ثبت نام در قرعه‌کشی", callback_data="user_register")],
        [InlineKeyboardButton("🔗 دریافت لینک دعوت من (شانس بیشتر)", callback_data="user_invite")],
        [InlineKeyboardButton("👤 وضعیت و بلیط من", callback_data="user_status")]
    ])

def get_join_menu():
    buttons = []
    for i, ch in enumerate(CHANNELS): buttons.append([InlineKeyboardButton(f"📣 عضویت در کانال اسپانسر {i+1}", url=ch["link"])])
    buttons.append([InlineKeyboardButton("✅ عضو شدم، ثبت‌نام کن", callback_data="user_register")])
    return InlineKeyboardMarkup(buttons)

def get_time_left(target_date):
    if not target_date: return "نامشخص"
    now = datetime.now()
    if now >= target_date: return "پایان یافته"
    diff = target_date - now
    days = diff.days
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days} روز و {hours} ساعت و {minutes} دقیقه"

def get_weighted_winners(weights_dict, count):
    winners = []
    pool = list(weights_dict.keys())
    chances = [weights_dict[uid] for uid in pool]
    while len(winners) < count and pool:
        chosen = random.choices(pool, weights=chances, k=1)[0]
        winners.append(chosen)
        idx = pool.index(chosen)
        pool.pop(idx)
        chances.pop(idx)
    return winners

async def l_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, admin_id = update.effective_user, context.bot_data['admin_id']
    bd = context.bot_data
    if user.id not in bd['all_users']:
        bd['all_users'].add(user.id)
        l_save_data(bd['db_file'], bd)
    
    args = context.args
    if args and args[0].startswith("ref_") and user.id != admin_id:
        try:
            ref_id = int(args[0].split("_")[1])
            if ref_id != user.id: context.user_data['referrer'] = ref_id
        except: pass

    if user.id == admin_id:
        bd['admin_states'][user.id] = "IDLE"
        msg = f"<tg-emoji emoji-id=\"5819154994967874788\">🧑‍💻</tg-emoji> <b>پنل مدیریت پیشرفته ربات شما:</b>"
        await update.message.reply_text(msg, reply_markup=get_admin_menu(), parse_mode='HTML')
    else:
        topic_text = bd['lottery']["topic"] if bd['lottery']["status"] != "INACTIVE" else "در حال حاضر قرعه‌کشی نداریم"
        msg = (f"<tg-emoji emoji-id=\"5852639281561996828\">🟥</tg-emoji> به ربات قرعه کشی خوش اومدید.\nموضوع قرعه کشی: {topic_text}\n\n"
               f"<tg-emoji emoji-id=\"5470177992950946662\">👇</tg-emoji> برای ثبت نام گزینه زیر را بزنید.")
        await update.message.reply_text(msg, reply_markup=get_user_menu(), parse_mode='HTML')

async def l_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    bd = context.bot_data
    admin_id = bd['admin_id']
    await query.answer()

    if data == "user_register":
        if bd['lottery']["status"] == "INACTIVE":
            await query.edit_message_text("❌ قرعه‌کشی فعالی وجود ندارد.", reply_markup=get_user_menu())
            return
        elif bd['lottery']["status"] == "PENDING":
            await query.edit_message_text(f"⏳ در انتظار شروع...\nزمان تا شروع: {get_time_left(bd['lottery']['start_dt'])}", reply_markup=get_user_menu())
            return
        elif user.id in bd['participants']:
            await query.edit_message_text("⚠️ شما قبلا ثبت‌نام کرده‌اید! برای دیدن بلیط خود به بخش «وضعیت» بروید.", reply_markup=get_user_menu())
            return

        all_joined = True
        for ch in CHANNELS:
            try:
                member = await context.bot.get_chat_member(chat_id=ch["id"], user_id=user.id)
                if member.status not in ['member', 'creator', 'administrator']: all_joined = False; break
            except BadRequest: pass

        if not all_joined:
            await query.edit_message_text("⚠️ برای شرکت در قرعه‌کشی باید در <b>تمامی کانال‌های زیر</b> عضو شوید:", parse_mode='HTML', reply_markup=get_join_menu())
            return

        num1, num2 = random.randint(1, 10), random.randint(1, 10)
        context.user_data['captcha'] = num1 + num2
        options = [num1 + num2, num1 + num2 + random.randint(1, 5), num1 + num2 - random.randint(1, 4), num1 + num2 + random.randint(6, 10)]
        random.shuffle(options)
        captcha_kb = [[InlineKeyboardButton(str(opt), callback_data=f"cap_{opt}") for opt in options]]
        await query.edit_message_text(f"🤖 لطفا حاصل جمع زیر را انتخاب کنید:\n\n{num1} + {num2} = ?", reply_markup=InlineKeyboardMarkup(captcha_kb))

    elif data.startswith("cap_"):
        try:
            chosen_ans = int(data.split("_")[1])
            correct_ans = context.user_data.get('captcha')
            if chosen_ans == correct_ans:
                is_prem = getattr(user, 'is_premium', False)
                ticket_id = f"#Q-{random.randint(100000, 999999)}"
                bd['participants'][user.id] = {"name": user.first_name, "username": user.username or "ندارد", "referrals": [], "ticket": ticket_id, "is_premium": is_prem}
                l_save_data(bd['db_file'], bd) 
                
                success_msg = f"<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> <b>ثبت‌نام شما با موفقیت انجام شد!</b>\n\n<tg-emoji emoji-id=\"5418010521309815154\">🎫</tg-emoji> شماره بلیط اختصاصی شما: <code>{ticket_id}</code>\n"
                if is_prem: success_msg += "\n💎 <i>کاربر ویژه: به دلیل داشتن اکانت تلگرام پرمیوم، شما با 2 شانس در قرعه‌کشی ثبت‌نام شدید!</i>"
                await query.edit_message_text(success_msg, parse_mode='HTML', reply_markup=get_user_menu())
                
                ref_id = context.user_data.get('referrer')
                if ref_id and ref_id in bd['participants']:
                    if user.id not in bd['participants'][ref_id]["referrals"]:
                        bd['participants'][ref_id]["referrals"].append(user.id)
                        l_save_data(bd['db_file'], bd)
                        context.user_data['referrer'] = None
                        try: await context.bot.send_message(chat_id=ref_id, text="🎉 یک نفر با لینک شما ثبت‌نام کرد!")
                        except: pass
                
                un_txt = f"@{user.username}" if user.username else "ندارد"
                safe_name = user.first_name.replace('<', '').replace('>', '')
                await context.bot.send_message(chat_id=admin_id, text=f"🔔 ثبت‌نام جدید:\nنام: {safe_name}\nآیدی: {un_txt}\nپرمیوم: {'بله 💎' if is_prem else 'خیر'}")
            else:
                await query.edit_message_text("❌ پاسخ اشتباه بود! لطفا دوباره روی دکمه ثبت نام کلیک کنید.", reply_markup=get_user_menu())
        except: pass

    elif data == "user_invite":
        link = f"https://t.me/{context.bot.username}?start=ref_{user.id}"
        msg = f"🔗 لینک دعوت اختصاصی شما:\n\n{link}\n\nبا دعوت هر نفر، 2 شانس بیشتر در قرعه‌کشی می‌گیرید!"
        await query.edit_message_text(msg, reply_markup=get_user_menu())

    elif data == "user_status":
        if user.id in bd['participants']:
            info = bd['participants'][user.id]
            invited = len(info['referrals'])
            time_left = get_time_left(bd['lottery']['end_dt']) if bd['lottery']['status'] == "ACTIVE" else "قرعه‌کشی در جریان نیست"
            
            msg = (f"✅ وضعیت شما: <b>ثبت‌نام شده</b>\n🎫 شماره بلیط: <code>{info['ticket']}</code>\n"
                   f"💎 اکانت پرمیوم: {'بله (+۲ شانس)' if info['is_premium'] else 'خیر'}\n👥 افراد دعوت شده: {invited} نفر\n"
                   f"🎯 <i>(مجموع شانس پس از بررسی فیک‌ها محاسبه می‌شود)</i>\n\n⏳ زمان تا پایان:\n{time_left}")
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_user_menu())
        else:
            await query.edit_message_text("<tg-emoji emoji-id=\"5852812849780362931\">❌</tg-emoji> شما ثبت‌نام نکرده‌اید.", parse_mode='HTML', reply_markup=get_user_menu())

    elif user.id == admin_id:
        if data == "admin_new":
            bd['admin_states'][user.id] = "WAIT_TOPIC"
            await query.edit_message_text("<tg-emoji emoji-id=\"5334882760735598374\">📝</tg-emoji> موضوع قرعه‌کشی چیست؟", parse_mode='HTML')
            
        elif data == "admin_edit_time":
            if bd['lottery']["status"] == "INACTIVE":
                await query.edit_message_text("❌ قرعه‌کشی فعالی وجود ندارد که زمانش را تغییر دهید.", reply_markup=get_admin_menu())
            else:
                bd['admin_states'][user.id] = "WAIT_EDIT_START"
                await query.edit_message_text("<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> تاریخ <b>شروع جدید</b> را وارد کنید (مثال: 2024/05/12 18:30):", parse_mode='HTML')

        elif data == "admin_broadcast":
            bd['admin_states'][user.id] = "WAIT_BROADCAST"
            await query.edit_message_text("📢 لطفا پیام خود را بفرستید:")
            
        elif data == "admin_list":
            if not bd['participants']:
                await query.edit_message_text("هنوز کسی ثبت‌نام نکرده.", reply_markup=get_admin_menu())
                return
            text = "<tg-emoji emoji-id=\"5318803171978526909\">👥</tg-emoji> <b>لیست شرکت‌کنندگان:</b>\n\n"
            for uid, info in bd['participants'].items():
                un = f"@{info['username']}" if info['username'] != "ندارد" else "بدون آیدی"
                safe_name = info['name'].replace('<', '').replace('>', '')
                prem_icon = "💎" if info['is_premium'] else "👤"
                text += f"{prem_icon} {safe_name} | {un} | دعوت: {len(info['referrals'])} | <code>{uid}</code>\n"
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=get_admin_menu())
        
        elif data == "admin_status":
            st_map = {"INACTIVE": "<tg-emoji emoji-id=\"5972265777296838427\">🔹</tg-emoji> غیرفعال", "PENDING": "⏳ در انتظار شروع", "ACTIVE": "<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> در حال ثبت‌نام (فعال)"}
            msg = (f"وضعیت: {st_map[bd['lottery']['status']]}\nموضوع: {bd['lottery']['topic']}\n"
                   f"برندگان: {bd['lottery']['winners_count']}\nشرکت‌کنندگان: {len(bd['participants'])} نفر")
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_admin_menu())
            
        elif data == "admin_help":
            help_text = "📚 <b>راهنمای جامع سیستم قرعه‌کشی VIP</b>\n\n(امکانات و دستورات ربات برای ادمین...)"
            await query.edit_message_text(help_text, parse_mode='HTML', reply_markup=get_admin_menu())

async def l_handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == context.bot_data['admin_id']:
        await update.message.reply_text(f"آیدی استیکر:\n\n<code>{update.message.sticker.file_id}</code>", parse_mode='HTML')

async def l_handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bd = context.bot_data
    admin_id = bd['admin_id']
    if user.id != admin_id: return
    
    if update.message.entities and any(ent.type == "custom_emoji" for ent in update.message.entities):
        msg = "✨ <b>کد ایموجی‌های پرمیوم شما ساخته شد:</b>\n\n"
        for ent in update.message.entities:
            if ent.type == "custom_emoji":
                emoji_char = update.message.text[ent.offset : ent.offset + ent.length]
                msg += f"ایموجی: {emoji_char}\nکد برای کپی کردن:\n<code>&lt;tg-emoji emoji-id=\"{ent.custom_emoji_id}\"&gt;{emoji_char}&lt;/tg-emoji&gt;</code>\n\n"
        await update.message.reply_text(msg, parse_mode='HTML')
        return

    state = bd['admin_states'].get(user.id)
    if state == "WAIT_BROADCAST":
        bd['admin_states'][user.id] = "IDLE"
        success = 0
        await update.message.reply_text("⏳ در حال ارسال...")
        for uid in list(bd['all_users']):
            try:
                await update.message.copy(chat_id=uid)
                success += 1
            except: pass
        await update.message.reply_text(f"✅ برای {success} نفر ارسال شد!", reply_markup=get_admin_menu())
        return

    text = update.message.text
    if not text: return

    if state == "WAIT_TOPIC":
        bd['admin_temp_data']["topic"] = text
        bd['admin_states'][user.id] = "WAIT_WINNERS"
        await update.message.reply_text("<tg-emoji emoji-id=\"5318803171978526909\">👥</tg-emoji> تعداد برنده‌ها را وارد کنید:", parse_mode='HTML')
        
    elif state == "WAIT_WINNERS":
        try:
            bd['admin_temp_data']["winners"] = int(text)
            bd['admin_states'][user.id] = "WAIT_PRIZES"
            await update.message.reply_text("<tg-emoji emoji-id=\"5280615440928758599\">🎁</tg-emoji> جوایز را در خطوط مجزا بفرستید (یا 0 بفرستید):", parse_mode='HTML')
        except ValueError: await update.message.reply_text("❌ فقط عدد بفرستید.")
        
    elif state == "WAIT_PRIZES":
        if text == "0": bd['admin_temp_data']["prizes"] = []
        else:
            prizes = text.split('\n')
            if len(prizes) != bd['admin_temp_data']["winners"]:
                await update.message.reply_text(f"❌ تعداد جوایز باید {bd['admin_temp_data']['winners']} باشد.")
                return
            bd['admin_temp_data']["prizes"] = prizes
        bd['admin_states'][user.id] = "WAIT_START_DATE"
        await update.message.reply_text("<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> تاریخ <b>شروع</b> (مثال: 2024/05/12 18:30):", parse_mode='HTML')
        
    elif state == "WAIT_START_DATE":
        try:
            bd['admin_temp_data']["start_date"] = datetime.strptime(text, "%Y/%m/%d %H:%M")
            bd['admin_states'][user.id] = "WAIT_END_DATE"
            await update.message.reply_text("<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> تاریخ <b>پایان</b> (مثال: 2024/05/12 18:30):", parse_mode='HTML')
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است.")
        
    elif state == "WAIT_END_DATE":
        try:
            end_dt = datetime.strptime(text, "%Y/%m/%d %H:%M")
            start_dt = bd['admin_temp_data']["start_date"]
            if end_dt <= start_dt or end_dt <= datetime.now():
                await update.message.reply_text("❌ تاریخ پایان نامعتبر است!")
                return
            bd['lottery']["topic"] = bd['admin_temp_data']["topic"]
            bd['lottery']["winners_count"] = bd['admin_temp_data']["winners"]
            bd['lottery']["prizes"] = bd['admin_temp_data']["prizes"]
            bd['lottery']["start_dt"] = start_dt
            bd['lottery']["end_dt"] = end_dt
            bd['participants'].clear()
            bd['admin_states'][user.id] = "IDLE"
            # ⚠️ در ورسل تایمر اتوماتیک دقیق کار نمی‌کند
            bd['lottery']["status"] = "ACTIVE"
            l_save_data(bd['db_file'], bd) 
            await update.message.reply_text(f"✅ فعال شد!\nپایان: {end_dt}\n(توجه: در سرورلس زمان‌بندی اتوماتیک دقیق نیست)", reply_markup=get_admin_menu())
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است.")

    elif state == "WAIT_EDIT_START":
        try:
            bd['admin_temp_data']["edit_start_date"] = datetime.strptime(text, "%Y/%m/%d %H:%M")
            bd['admin_states'][user.id] = "WAIT_EDIT_END"
            await update.message.reply_text("<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> تاریخ <b>پایان جدید</b> را وارد کنید:", parse_mode='HTML')
        except: await update.message.reply_text("❌ فرمت اشتباه است.")
        
    elif state == "WAIT_EDIT_END":
        try:
            end_dt = datetime.strptime(text, "%Y/%m/%d %H:%M")
            start_dt = bd['admin_temp_data']["edit_start_date"]
            if end_dt <= start_dt or end_dt <= datetime.now():
                await update.message.reply_text("❌ تاریخ پایان نامعتبر است!")
                return
            bd['lottery']["start_dt"] = start_dt
            bd['lottery']["end_dt"] = end_dt
            bd['admin_states'][user.id] = "IDLE"
            bd['lottery']["status"] = "ACTIVE"
            l_save_data(bd['db_file'], bd)
            await update.message.reply_text(f"✅ زمان ویرایش شد!\nوضعیت: فعال\nپایان جدید: {end_dt}", reply_markup=get_admin_menu())
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است.")

async def l_send_prize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != context.bot_data['admin_id']: return
    try:
        user_id = int(context.args[0])
        prize_text = " ".join(context.args[1:])
        await context.bot.send_message(chat_id=user_id, text=f"🎁 پیام مدیریت:\n\n{prize_text}")
        await update.message.reply_text("✅ ارسال شد.")
    except: await update.message.reply_text("❌ خطا.")

async def l_clear_old_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ کیبوردهای قدیمی پاک شدند! حالا روی /start کلیک کنید.", reply_markup=ReplyKeyboardRemove())

# ==========================================
# 🎭 هسته ربات چت ناشناس
# ==========================================
async def child_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id, user_id, token, db = context.bot_data['admin_id'], update.effective_user.id, context.bot.token, load_db()
    uid = get_uid_by_token(token, db)
    bdata = db.get(uid, {}).get('bots', {}).get(token, {})
    co_admins = bdata.get('co_admins', {})
    
    if user_id == admin_id or str(user_id) in co_admins:
        role = "مدیر اصلی" if user_id == admin_id else f"ادمین فرعی ({co_admins[str(user_id)]})"
        msg = f"<tg-emoji emoji-id=\"5819154994967874788\">🧑‍💻</tg-emoji> <b>سلام {role} عزیز! به ربات خودت خوش اومدی.</b>\n\n<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> ربات فعال است و پیام‌ها برای شما فوروارد خواهد شد."
        if update.message: await update.message.reply_text(msg, parse_mode='HTML')
        else: await update.callback_query.edit_message_text(msg, parse_mode='HTML')
        return ConversationHandler.END
        
    if is_user_blocked(token, user_id, db):
        if update.message: await update.message.reply_text("<tg-emoji emoji-id=\"5938302546014638461\">❌</tg-emoji> شما از طرف مدیریت مسدود شده‌اید.", parse_mode='HTML')
        return ConversationHandler.END

    force_join = bdata.get('force_join')
    if force_join:
        try:
            member = await context.bot.get_chat_member(chat_id=force_join, user_id=user_id)
            if member.status in ['left', 'kicked']:
                link = f"https://t.me/{force_join.replace('@', '')}"
                keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url=link)], [InlineKeyboardButton("✅ عضو شدم", callback_data="start_anon")]]
                msg = "<tg-emoji emoji-id=\"5938290532991111041\">❌</tg-emoji> <b>عضویت اجباری!</b>\n\nبرای ارسال پیام، ابتدا باید در کانال زیر عضو شوید:"
                if update.message: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
                else: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
                return ConversationHandler.END
        except: pass

    bot_name = bdata.get('bot_name', '')
    display_name = f" «{bot_name}»" if bot_name else ""
    msg = f"سلام! <tg-emoji emoji-id=\"5359441070201513074\">🎭</tg-emoji>\nبه ربات پیام ناشناس{display_name} خوش آمدید.\nبرای ارسال پیام روی دکمه زیر کلیک کنید:"
    keyboard = [[InlineKeyboardButton("📨 ارسال پیام ناشناس", callback_data="start_anon")]]
    
    try:
        if update.message: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        else: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except BadRequest:
        msg_clean = msg.replace('<', '').replace('>', '')
        if update.message: await update.message.reply_text(msg_clean, reply_markup=InlineKeyboardMarkup(keyboard))
        else: await update.callback_query.edit_message_text(msg_clean, reply_markup=InlineKeyboardMarkup(keyboard))
        
    return ConversationHandler.END

async def child_start_anon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db, token, user_id = load_db(), context.bot.token, update.effective_user.id
    uid = get_uid_by_token(token, db)
    bdata = db.get(uid, {}).get('bots', {}).get(token, {})
    co_admins, admin_id = bdata.get('co_admins', {}), context.bot_data['admin_id']

    if is_user_blocked(token, user_id, db):
        await query.message.reply_text("<tg-emoji emoji-id=\"5938302546014638461\">❌</tg-emoji> شما مسدود شده‌اید.", parse_mode='HTML')
        return ConversationHandler.END
        
    force_join = bdata.get('force_join')
    if force_join and str(user_id) not in co_admins and user_id != admin_id:
        try:
            member = await context.bot.get_chat_member(chat_id=force_join, user_id=user_id)
            if member.status in ['left', 'kicked']:
                link = f"https://t.me/{force_join.replace('@', '')}"
                keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url=link)], [InlineKeyboardButton("✅ عضو شدم", callback_data="start_anon")]]
                await query.edit_message_text("❌ **شما هنوز عضو نشده‌اید!**\nلطفاً ابتدا عضو شوید:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
                return ConversationHandler.END
        except: pass

    try: await query.edit_message_reply_markup(reply_markup=None)
    except: pass
    await query.message.reply_text("<tg-emoji emoji-id=\"5470060791883374114\">✍️</tg-emoji> لطفاً پیام خود را بفرستید (متن، عکس، ویدیو، یا ویس):", parse_mode='HTML')
    return CHILD_WAITING_MSG

async def child_receive_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['draft_msg_id'] = update.message.message_id
    keyboard = [[InlineKeyboardButton("✅ بله، بفرست", callback_data="confirm_anon")], [InlineKeyboardButton("❌ لغو", callback_data="cancel_anon")]]
    await update.message.reply_text("<tg-emoji emoji-id=\"5436113877181941026\">❓</tg-emoji> آیا از ارسال این پیام مطمئن هستید؟", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return CHILD_WAITING_MSG

async def child_confirm_anon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_anon":
        try: await query.edit_message_text("<tg-emoji emoji-id=\"5852812849780362931\">❌</tg-emoji> ارسال لغو شد.\nبرای شروع مجدد /start را بزنید.", parse_mode='HTML')
        except: await query.message.reply_text("<tg-emoji emoji-id=\"5852812849780362931\">❌</tg-emoji> لغو شد.", parse_mode='HTML')
        context.user_data.clear()
        return ConversationHandler.END
        
    elif query.data == "confirm_anon":
        admin_id, msg_id = context.bot_data['admin_id'], context.user_data.get('draft_msg_id')
        user_id, token, db = update.effective_user.id, context.bot.token, load_db()
        uid = get_uid_by_token(token, db)
        all_admins = list(set([admin_id] + [int(k) for k in db[uid]['bots'][token].get('co_admins', {}).keys()]))
        
        admin_keyboard = [[InlineKeyboardButton("💬 پاسخ دادن", callback_data=f"creply_{user_id}")], [InlineKeyboardButton("🚫 مسدودیت", callback_data=f"cblock_{user_id}")]]
        success = False
        for ad_id in all_admins:
            try:
                await context.bot.send_message(chat_id=ad_id, text="<tg-emoji emoji-id=\"5433811242135331842\">📥</tg-emoji> <b>پیام ناشناس جدید:</b>", parse_mode='HTML')
                await context.bot.copy_message(chat_id=ad_id, from_chat_id=user_id, message_id=msg_id, reply_markup=InlineKeyboardMarkup(admin_keyboard))
                success = True
            except: pass
            
        text = "<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> پیام شما با موفقیت ارسال شد." if success else "<tg-emoji emoji-id=\"5852812849780362931\">❌</tg-emoji> ربات متوقف است."
        try: await query.edit_message_text(text, parse_mode='HTML')
        except: await query.message.reply_text(text, parse_mode='HTML')
        context.user_data.clear()
        return ConversationHandler.END

async def child_admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data = update.callback_query, update.callback_query.data
    await query.answer()
    if data == "cancel_reply":
        await query.edit_message_text("<tg-emoji emoji-id=\"4958526153955476488\">❌</tg-emoji> پاسخ لغو شد.", parse_mode='HTML')
        context.user_data.clear()
        return ConversationHandler.END
    if data.startswith("cblock_"):
        target_anon_id = int(data.split('_')[1])
        block_user_in_db(context.bot.token, target_anon_id)
        try: await query.edit_message_reply_markup(reply_markup=None)
        except: pass
        await query.message.reply_text("<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> کاربر مسدود شد.", parse_mode='HTML')
        return ConversationHandler.END
    elif data.startswith("creply_"):
        context.user_data['reply_target'] = int(data.split('_')[1])
        await query.message.reply_text("<tg-emoji emoji-id=\"5470060791883374114\">✍️</tg-emoji> پیام خود را بنویسید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو پاسخ", callback_data="cancel_reply")]]), parse_mode='HTML')
        return CHILD_ADMIN_REPLY

async def child_do_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_id = context.user_data.get('reply_target')
    if not target_id: return ConversationHandler.END
    try:
        await context.bot.send_message(chat_id=target_id, text="<tg-emoji emoji-id=\"5938311423712039050\">📬</tg-emoji> <b>پیام از طرف مدیریت:</b>", parse_mode='HTML')
        await context.bot.copy_message(chat_id=target_id, from_chat_id=update.effective_user.id, message_id=update.message.message_id, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 ارسال پاسخ به مدیر", callback_data="start_anon")]]))
        await update.message.reply_text("<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> پاسخ ارسال شد.", parse_mode='HTML')
    except: await update.message.reply_text("<tg-emoji emoji-id=\"5882120504017426280\">❌</tg-emoji> خطا! کاربر ربات را بلاک کرده.", parse_mode='HTML')
    context.user_data.clear()
    return ConversationHandler.END

# ==========================================
# 💎 پنل مرکزی (ربات‌ساز شما)
# ==========================================
async def main_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, db = update.effective_user.id, load_db()
    if str(user_id) not in db:
        db[str(user_id)] = {'credits': [], 'bots': {}, 'used_free_trial': {'anon': False, 'lottery': False}}
        save_db(db)

    force_join_global = db['global'].get('force_join', "")
    if force_join_global and user_id != ADMIN_ID:
        try:
            member = await context.bot.get_chat_member(chat_id=force_join_global, user_id=user_id)
            if member.status in ['left', 'kicked']:
                link = f"https://t.me/{force_join_global.replace('@', '')}"
                keyboard = [[InlineKeyboardButton("📢 عضویت در کانال", url=link)], [InlineKeyboardButton("✅ عضو شدم", callback_data="cancel_flow")]]
                msg = "<tg-emoji emoji-id=\"5938290532991111041\">❌</tg-emoji> <b>عضویت اجباری!</b>\n\nبرای استفاده از ربات‌ساز، ابتدا باید در کانال زیر عضو شوید:"
                if update.message: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
                elif update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
                return ConversationHandler.END
        except: pass
        
    if user_id == ADMIN_ID:
        sales_status = "🟢 باز" if db['global'].get('sales_open', True) else "🔴 بسته"
        msg = "👨‍💻 <b>پنل مدیریت ربات‌ساز</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:"
        keyboard = [
            [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_users_list"), InlineKeyboardButton("⚙️ مدیریت دستی کاربر", callback_data="admin_manage_user")],
            [InlineKeyboardButton("📢 پیام همگانی", callback_data="admin_broadcast"), InlineKeyboardButton(f"🛒 فروش: {sales_status}", callback_data="admin_toggle_sales")],
            [InlineKeyboardButton("🛍 بخش فروشگاهی", callback_data="admin_store")]
        ]
    else:
        credits_count = len(db.get(str(user_id), {}).get('credits', []))
        msg = f"<tg-emoji emoji-id=\"6318936317451313567\">🙂</tg-emoji> <b>به پلتفرم ربات‌ساز خوش آمدید!</b>\n\nتعداد اشتراک‌های استفاده نشده: {credits_count}\nاز دکمه‌های زیر استفاده کنید:"
        keyboard = [
            [InlineKeyboardButton("🛒 خرید اشتراک جدید", callback_data="buy_new")],
            [InlineKeyboardButton("🔑 راه‌اندازی ربات از اشتراک", callback_data="set_bot")],
            [InlineKeyboardButton("🤖 مدیریت ربات‌های من", callback_data="user_bots")],
            [InlineKeyboardButton("❓ راهنمای استفاده", callback_data="user_help")]
        ]
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='HTML')
    elif update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='HTML')
    return ConversationHandler.END

async def handle_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data, user_id, db = update.callback_query, update.callback_query.data, str(update.effective_user.id), load_db()
    await query.answer()

    if data == "cancel_flow":
        await main_start(update, context)
        return ConversationHandler.END
    elif data == "user_help":
        help_text = "📚 **راهنمای جامع پلتفرم**\nشما در اینجا می‌توانید هم ربات ناشناس و هم ربات قرعه‌کشی اختصاصی خود را بسازید.\nاز دکمه خرید اشتراک اقدام کنید و سپس با وارد کردن توکن، ربات خود را در کمتر از ۱۰ ثانیه راه‌اندازی کنید."
        await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]), parse_mode='Markdown')
        return ConversationHandler.END

    # فروشگاه و ادمین
    elif data == "admin_store":
        if int(user_id) != ADMIN_ID: return
        msg = "🛍 **بخش فروشگاهی:**\n\nبرای اعمال تغییرات یکی از بخش‌های زیر را انتخاب کنید:"
        keyboard = [
            [InlineKeyboardButton("💲 تنظیم قیمت‌ها", callback_data="store_prices")],
            [InlineKeyboardButton("📢 تنظیم کانال‌ها", callback_data="store_channels")],
            [InlineKeyboardButton("🎁 مدیریت کدهای تخفیف", callback_data="store_discounts")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="cancel_flow")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END
        
    elif data == "store_prices":
        if int(user_id) != ADMIN_ID: return
        keyboard = [
            [InlineKeyboardButton("🎭 قیمت‌های ربات ناشناس", callback_data="st_price_anon")],
            [InlineKeyboardButton("🎁 قیمت‌های ربات قرعه‌کشی", callback_data="st_price_lottery")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_store")]
        ]
        await query.edit_message_text("💲 **کدام ربات را می‌خواهید قیمت‌گذاری کنید؟**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END
        
    elif data.startswith("st_price_"):
        if int(user_id) != ADMIN_ID: return
        bot_type = data.split('_')[2]
        prices = db['global']['prices'].get(bot_type, {})
        keyboard = [
            [InlineKeyboardButton(f"1 ماهه (فعلی: {prices.get('30','-')} ت)", callback_data=f"setprc_{bot_type}_30")],
            [InlineKeyboardButton(f"2 ماهه (فعلی: {prices.get('60','-')} ت)", callback_data=f"setprc_{bot_type}_60")],
            [InlineKeyboardButton(f"3 ماهه (فعلی: {prices.get('90','-')} ت)", callback_data=f"setprc_{bot_type}_90")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="store_prices")]
        ]
        await query.edit_message_text("💲 **پلن مورد نظر برای تغییر قیمت را انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END

    elif data.startswith("setprc_"):
        if int(user_id) != ADMIN_ID: return
        context.user_data['edit_price_type'] = data.split('_')[1]
        context.user_data['edit_price_days'] = data.split('_')[2]
        await query.edit_message_text("💵 لطفاً مبلغ جدید را به تومان (فقط عدد) وارد کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="admin_store")]]))
        return WAITING_GLOBAL_PRICE

    elif data == "store_channels":
        if int(user_id) != ADMIN_ID: return
        cur_fj = db['global'].get('force_join', 'ندارد')
        cur_log = db['global'].get('log_channel', 'ندارد')
        msg = f"📢 **تنظیمات کانال‌های ربات‌ساز:**\n\n🔒 کانال عضویت اجباری (فعلی): {cur_fj}\n📝 کانال گزارشات (فعلی): {cur_log}"
        keyboard = [
            [InlineKeyboardButton("🔒 تنظیم کانال عضویت (اجباری)", callback_data="st_ch_join")],
            [InlineKeyboardButton("📝 تنظیم کانال گزارشات", callback_data="st_ch_log")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_store")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END

    elif data == "st_ch_join":
        if int(user_id) != ADMIN_ID: return
        await query.edit_message_text("🔒 لطفاً آیدی کانال عضویت اجباری را با @ بفرستید (یا کلمه `لغو`):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="store_channels")]]))
        return WAITING_GLOBAL_FORCE_JOIN
    elif data == "st_ch_log":
        if int(user_id) != ADMIN_ID: return
        await query.edit_message_text("📝 لطفاً آیدی کانال گزارشات را با @ بفرستید (یا کلمه `لغو`):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="store_channels")]]))
        return WAITING_GLOBAL_LOG_CH
    elif data == "store_discounts":
        if int(user_id) != ADMIN_ID: return
        discounts = db['global'].get('discounts', {})
        msg = "🎁 **لیست کدهای تخفیف فعال:**\n\n"
        for code, info in discounts.items():
            msg += f"🎫 `{code}` | {info['percent']}% تخفیف | استفاده: {info.get('used', 0)}/{info['limit']}\n"
        if not discounts: msg += "کد تخفیفی وجود ندارد."
        keyboard = [
            [InlineKeyboardButton("➕ ایجاد کد تخفیف جدید", callback_data="st_disc_new")],
            [InlineKeyboardButton("🗑 حذف یک کد تخفیف", callback_data="st_disc_del")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_store")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END
    elif data == "st_disc_new":
        if int(user_id) != ADMIN_ID: return
        await query.edit_message_text("🎫 نام/متن کد تخفیف جدید را وارد کنید (مثلاً YALDA):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="store_discounts")]]))
        return WAITING_DISCOUNT_NAME
    elif data == "st_disc_del":
        if int(user_id) != ADMIN_ID: return
        discounts = db['global'].get('discounts', {})
        keyboard = []
        for code in discounts.keys():
            keyboard.append([InlineKeyboardButton(f"🗑 حذف {code}", callback_data=f"deldisc_{code}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="store_discounts")])
        await query.edit_message_text("کدام کد تخفیف را می‌خواهید حذف کنید؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END
    elif data.startswith("deldisc_"):
        if int(user_id) != ADMIN_ID: return
        code = data.split('_')[1]
        if code in db['global']['discounts']:
            del db['global']['discounts'][code]
            save_db(db)
            await query.answer("✅ کد تخفیف حذف شد.", show_alert=True)
        keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="store_discounts")]]
        await query.edit_message_text("حذف با موفقیت انجام شد.", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    # مدیریت کاربران
    elif data == "admin_toggle_sales":
        if int(user_id) != ADMIN_ID: return
        db['global']['sales_open'] = not db['global'].get('sales_open', True)
        save_db(db)
        await main_start(update, context)
        return ConversationHandler.END
    elif data == "admin_broadcast":
        if int(user_id) != ADMIN_ID: return
        await query.edit_message_text("📢 پیام همگانی را بفرستید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))
        return WAITING_BROADCAST
    elif data == "admin_users_list":
        if int(user_id) != ADMIN_ID: return
        msg = "👥 **لیست کاربران:**\n\n"
        for uid, udata in db.items():
            if uid == 'global': continue
            anon_c = sum(1 for c in udata.get('credits', []) if c['type'] == 'anon')
            lottery_c = sum(1 for c in udata.get('credits', []) if c['type'] == 'lottery')
            bots = len(udata.get('bots', {}))
            msg += f"👤 `{uid}` | اشتراک ناشناس: {anon_c} | قرعه‌کشی: {lottery_c} | ربات‌ها: {bots}\n"
        if len(db) <= 1: msg += "کاربری یافت نشد."
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]), parse_mode='Markdown')
        return ConversationHandler.END
    elif data == "admin_manage_user":
        if int(user_id) != ADMIN_ID: return
        await query.edit_message_text("⚙️ **مدیریت دستی کاربر**\n\nلطفاً آیدی عددی کاربر مورد نظر را بفرستید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]), parse_mode='Markdown')
        return WAITING_MANAGE_USER
        
    elif data.startswith("admadd_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        b_type, target_uid = parts[1], parts[2]
        if target_uid not in db: db[target_uid] = {'credits': [], 'bots': {}, 'used_free_trial': {'anon': False, 'lottery': False}}
        db[target_uid]['credits'].append({'type': b_type, 'days': 30})
        save_db(db)
        await query.answer(f"✅ یک اشتراک 30 روزه ({'ناشناس' if b_type=='anon' else 'قرعه‌کشی'}) اضافه شد.", show_alert=True)
        await main_start(update, context)
        return ConversationHandler.END
        
    elif data.startswith("admmngbot_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        target_uid, b_id = parts[1], parts[2]
        token = get_token_by_id(target_uid, b_id, db)
        if not token: return
        bdata = db[target_uid]['bots'][token]

        days_left = (datetime.fromisoformat(bdata['expiry_date']) - datetime.now()).days
        status = '⛔️ غیرفعال' if bdata.get('admin_disabled') else '✅ فعال'

        msg = (f"⚙️ **مشخصات کامل ربات:**\n\n🔗 آیدی ربات: {bdata.get('bot_username', 'نامشخص')}\n🔑 توکن: `{token}`\n"
               f"👤 آیدی خریدار (مدیر): `{bdata.get('admin_id', 'نامشخص')}`\n⏳ اعتبار باقی‌مانده: {days_left} روز\nوضعیت فعلی: {status}")

        keyboard = [
            [InlineKeyboardButton("➕ افزایش روز", callback_data=f"admmoddays_add_{target_uid}_{b_id}"),
             InlineKeyboardButton("➖ کاهش روز", callback_data=f"admmoddays_sub_{target_uid}_{b_id}")],
            [InlineKeyboardButton("تغییر وضعیت (فعال/غیرفعال)", callback_data=f"admtogglebot_{target_uid}_{b_id}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END

    elif data.startswith("admmoddays_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        action, target_uid, b_id = parts[1], parts[2], parts[3]
        context.user_data['mod_bot_uid'] = target_uid
        context.user_data['mod_bot_id'] = b_id
        context.user_data['mod_action'] = action
        act_text = "افزایش" if action == "add" else "کاهش"
        await query.edit_message_text(f"تعداد روزی که می‌خواهید {act_text} دهید را وارد کنید (فقط عدد):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))
        return WAITING_MOD_DAYS

    elif data.startswith("admtogglebot_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        target_uid, b_id = parts[1], parts[2]
        token = get_token_by_id(target_uid, b_id, db)
        db[target_uid]['bots'][token]['admin_disabled'] = not db[target_uid]['bots'][token].get('admin_disabled', False)
        save_db(db)
        await query.answer("✅ وضعیت با موفقیت تغییر کرد.", show_alert=True)
        await main_start(update, context)
        return ConversationHandler.END

    elif data.startswith("admsendmsg_"):
        if int(user_id) != ADMIN_ID: return
        target_uid = data.split('_')[1]
        context.user_data['send_msg_uid'] = target_uid
        await query.edit_message_text("💬 لطفاً پیام خود را برای این کاربر بفرستید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))
        return WAITING_ADMIN_SEND_MSG

    # خرید کاربر
    elif data == "buy_new":
        if not db['global'].get('sales_open', True):
            await query.answer("❌ فروش موقتاً بسته شده است.", show_alert=True)
            return ConversationHandler.END
            
        keyboard = [
            [InlineKeyboardButton("🎭 ربات پیام ناشناس", callback_data="buytype_anon")],
            [InlineKeyboardButton("🎁 ربات قرعه‌کشی", callback_data="buytype_lottery")]
        ]
        used_trials = db.get(user_id, {}).get('used_free_trial', {'anon': False, 'lottery': False})
        if not used_trials.get('anon', False): keyboard.insert(1, [InlineKeyboardButton("🎁 تست 7 روزه رایگان (ناشناس)", callback_data="freetrial_anon")])
        if not used_trials.get('lottery', False): keyboard.append([InlineKeyboardButton("🎁 تست 7 روزه رایگان (قرعه‌کشی)", callback_data="freetrial_lottery")])
        keyboard.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")])
        
        await query.edit_message_text("<tg-emoji emoji-id=\"5318985656548999952\">🛒</tg-emoji> <b>نوع رباتی که می‌خواهید خریداری کنید را انتخاب کنید:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return ConversationHandler.END

    elif data.startswith("buytype_"):
        bot_type = data.split('_')[1]
        type_name = "پیام ناشناس" if bot_type == 'anon' else "قرعه‌کشی"
        prices_db = db['global']['prices'].get(bot_type, {})
        keyboard = [
            [InlineKeyboardButton(f"1 ماهه - {int(prices_db.get('30', 0)):,} تومان", callback_data=f"buydisc_new_{bot_type}_30")],
            [InlineKeyboardButton(f"2 ماهه - {int(prices_db.get('60', 0)):,} تومان", callback_data=f"buydisc_new_{bot_type}_60")],
            [InlineKeyboardButton(f"3 ماهه - {int(prices_db.get('90', 0)):,} تومان", callback_data=f"buydisc_new_{bot_type}_90")],
            [InlineKeyboardButton("🔙 بازگشت به انتخاب ربات", callback_data="buy_new")]
        ]
        await query.edit_message_text(f"<tg-emoji emoji-id=\"5472401690793614752\">🛍️</tg-emoji> <b>پلن‌های {type_name} را انتخاب کنید:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return ConversationHandler.END

    elif data.startswith("freetrial_"):
        bot_type = data.split('_')[1]
        used_trials = db.get(user_id, {}).get('used_free_trial', {'anon': False, 'lottery': False})
        if used_trials.get(bot_type, False):
            await query.answer("❌ شما قبلاً از تست رایگان این نوع ربات استفاده کرده‌اید!", show_alert=True)
            return ConversationHandler.END
        db[user_id]['used_free_trial'][bot_type] = True
        db[user_id]['credits'].append({'type': bot_type, 'days': 7})
        save_db(db)
        await query.edit_message_text("🎉 **پلن رایگان فعال شد!**\nهم‌اکنون از منو «راه‌اندازی ربات» را بزنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]), parse_mode='Markdown')
        return ConversationHandler.END

    elif data.startswith("buydisc_"):
        parts = data.split('_')
        buy_action = parts[1]
        context.user_data['buy_action'] = buy_action
        if buy_action == 'new':
            context.user_data['buy_plan_type'] = parts[2]
            context.user_data['buy_plan_days'] = parts[3]
        else:
            context.user_data['renew_id'] = parts[2]
            context.user_data['buy_plan_days'] = parts[3]
            token = get_token_by_id(user_id, parts[2], db)
            context.user_data['buy_plan_type'] = db[user_id]['bots'][token].get('bot_type', 'anon')
            
        keyboard = [
            [InlineKeyboardButton("🎟 کد تخفیف دارم", callback_data="enter_discount")],
            [InlineKeyboardButton("⏩ ادامه پرداخت (بدون تخفیف)", callback_data="skip_discount")],
            [InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")]
        ]
        await query.edit_message_text("آیا کد تخفیف دارید؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END
    elif data == "enter_discount":
        await query.edit_message_text("🎟 لطفاً کد تخفیف خود را ارسال کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏩ انصراف از کد", callback_data="skip_discount")]]))
        return WAITING_DISCOUNT
    elif data == "skip_discount":
        context.user_data['discount_percent'] = 0
        await show_receipt_request(query, context, db)
        return WAITING_RECEIPT

    # مدیریت ربات‌های کاربر
    elif data == "set_bot":
        credits = db.get(user_id, {}).get('credits', [])
        if not credits:
            await query.edit_message_text("❌ شما اشتراک استفاده نشده‌ای ندارید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
            return ConversationHandler.END
        anon_count = sum(1 for c in credits if c['type'] == 'anon')
        lottery_count = sum(1 for c in credits if c['type'] == 'lottery')
        keyboard = []
        if anon_count > 0: keyboard.append([InlineKeyboardButton(f"🎭 راه‌اندازی ناشناس ({anon_count} اشتراک)", callback_data="setupbot_anon")])
        if lottery_count > 0: keyboard.append([InlineKeyboardButton(f"🎁 راه‌اندازی قرعه‌کشی ({lottery_count} اشتراک)", callback_data="setupbot_lottery")])
        keyboard.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")])
        await query.edit_message_text("🔑 **نوع رباتی که می‌خواهید راه‌اندازی کنید:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END

    elif data.startswith("setupbot_"):
        context.user_data['setup_bot_type'] = data.split('_')[1]
        await query.edit_message_text("🔑 **مرحله ۱:** لطفاً توکن ربات جدید را ارسال کنید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]), parse_mode='Markdown')
        return WAITING_TOKEN

    elif data == "user_bots":
        bots = db.get(user_id, {}).get('bots', {})
        if not bots:
            await query.edit_message_text("<tg-emoji emoji-id=\"4958526153955476488\">❌</tg-emoji> شما هیچ ربات فعالی ندارید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]), parse_mode='HTML')
            return ConversationHandler.END
            
        keyboard = []
        for t, bdata in bots.items():
            b_type_name = "ناشناس" if bdata.get('bot_type', 'anon') == 'anon' else "قرعه‌کشی"
            b_username = bdata.get('bot_username', f"{t.split(':')[0]}")
            
            # در سیستم سرورلس ورسل، اگر انقضا نرسیده باشد و مسدود نباشد، آن را فعال در نظر می‌گیریم
            is_active = (datetime.now() < datetime.fromisoformat(bdata['expiry_date'])) and not bdata.get('user_disabled') and not bdata.get('admin_disabled')
            icon = '🟢' if is_active else '🔴'
            
            keyboard.append([InlineKeyboardButton(f"{icon} {b_username} ({b_type_name})", callback_data=f"mybot_{t.split(':')[0]}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")])
        await query.edit_message_text("<tg-emoji emoji-id=\"5255883984151276991\">🤖</tg-emoji> <b>ربات خود را انتخاب کنید:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return ConversationHandler.END

    elif data.startswith("mybot_") or data.startswith("utoggle_"):
        b_id = data.split('_')[1]
        token = get_token_by_id(user_id, b_id, db)
        if not token: return
        if data.startswith("utoggle_"):
            db[user_id]['bots'][token]['user_disabled'] = not db[user_id]['bots'][token].get('user_disabled', False)
            save_db(db)
        
        bdata = db[user_id]['bots'][token]
        bot_type = bdata.get('bot_type', 'anon')
        days_left = (datetime.fromisoformat(bdata['expiry_date']) - datetime.now()).days
        
        is_active = (datetime.now() < datetime.fromisoformat(bdata['expiry_date'])) and not bdata.get('user_disabled') and not bdata.get('admin_disabled')
        status_server = "در حال کار <tg-emoji emoji-id=\"6269566961168944843\">🚀</tg-emoji>" if is_active else "متوقف 🛑"
        status_admin = "<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> آزاد" if not bdata.get('admin_disabled') else "⛔️ مسدود"
        bot_type_str = "<tg-emoji emoji-id=\"5891093751555694829\">🎭</tg-emoji> پیام ناشناس" if bot_type == 'anon' else "🎁 قرعه‌کشی"
        
        msg = (f"<tg-emoji emoji-id=\"5255883984151276991\">🤖</tg-emoji> <b>ربات:</b> {bdata.get('bot_username', b_id)}\n"
               f"نوع: {bot_type_str}\n"
               f"<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> <b>زمان باقی‌مانده:</b> {days_left} روز\n\n"
               f"وضعیت سرور: {status_server}\n"
               f"مدیریت کل: {status_admin}")
        
        keyboard = [[InlineKeyboardButton("🛒 تمدید زمان ربات", callback_data=f"renew_{b_id}"), InlineKeyboardButton("روشن / خاموش", callback_data=f"utoggle_{b_id}")]]
        
        if bot_type == 'anon':
            msg += (f"\n<tg-emoji emoji-id=\"5334882760735598374\">📝</tg-emoji> نام: {bdata.get('bot_name') or 'ثبت نشده'}\n"
                    f"<tg-emoji emoji-id=\"5890882606668452641\">🔓</tg-emoji> قفل کانال: {bdata.get('force_join', 'ندارد')}\n"
                    f"<tg-emoji emoji-id=\"5318803171978526909\">👥</tg-emoji> ادمین فرعی: {len(bdata.get('co_admins', {}))} نفر")
            keyboard.append([InlineKeyboardButton("✍️ تعیین نام", callback_data=f"setname_{b_id}"), InlineKeyboardButton("👥 ادمین فرعی", callback_data=f"coadmin_{b_id}")])
            keyboard.append([InlineKeyboardButton("🔒 قفل عضویت اجباری", callback_data=f"forcejoin_{b_id}")])
            
        keyboard.append([InlineKeyboardButton("🔗 ثبت/ویرایش آیدی ربات", callback_data=f"editbotusr_{b_id}")])
        keyboard.append([InlineKeyboardButton("👤 تغییر آیدی مدیر اصلی", callback_data=f"chgadmin_{b_id}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="user_bots")])
        try: await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        except: pass
        return ConversationHandler.END

    elif data.startswith("editbotusr_"):
        context.user_data['edit_bid'] = data.split('_')[1]
        await query.edit_message_text("🔗 لطفاً آیدی جدید ربات خود را (همراه با @) بفرستید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))
        return WAITING_EDIT_BOT_USERNAME
    elif data.startswith("renew_"):
        b_id = data.split('_')[1]
        token = get_token_by_id(user_id, b_id, db)
        bot_type = db[user_id]['bots'][token].get('bot_type', 'anon')
        prices_db = db['global']['prices'].get(bot_type, {})
        keyboard = [
            [InlineKeyboardButton(f"1 ماهه - {int(prices_db.get('30',0)):,} تومان", callback_data=f"buydisc_ren_{b_id}_30")],
            [InlineKeyboardButton(f"2 ماهه - {int(prices_db.get('60',0)):,} تومان", callback_data=f"buydisc_ren_{b_id}_60")],
            [InlineKeyboardButton(f"3 ماهه - {int(prices_db.get('90',0)):,} تومان", callback_data=f"buydisc_ren_{b_id}_90")],
            [InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")]
        ]
        await query.edit_message_text(f"🛒 **پلن تمدید ربات را انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END
    elif data.startswith("chgadmin_"):
        context.user_data['edit_bid'] = data.split('_')[1]
        await query.edit_message_text("👤 لطفاً شناسه عددی جدید مدیر اصلی را بفرستید:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))
        return WAITING_NEW_ADMIN_ID
    elif data.startswith("forcejoin_"):
        context.user_data['edit_bid'] = data.split('_')[1]
        await query.edit_message_text("🔒 آیدی کانال را با @ بفرستید (یا کلمه `لغو`):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{data.split('_')[1]}")]]))
        return WAITING_FORCE_JOIN
    elif data.startswith("setname_"):
        context.user_data['edit_bid'] = data.split('_')[1]
        await query.edit_message_text("✍️ نام نمایشی ربات را بفرستید (پشتیبانی از کدهای HTML ایموجی پرمیوم):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]))
        return WAITING_BOT_NAME
    elif data.startswith("coadmin_"):
        b_id = data.split('_')[1]
        context.user_data['edit_bid'] = b_id
        co_admins = db[user_id]['bots'][get_token_by_id(user_id, b_id, db)].get('co_admins', {})
        msg = f"👥 **مدیریت ادمین‌های فرعی ربات {b_id}:**\n" + "".join([f" - `{k}` ({v})\n" for k,v in co_admins.items()]) + "\nبرای اضافه یا حذف ادمین، آیدی عددی او را بفرستید:"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]), parse_mode='Markdown')
        return WAITING_CO_ADMIN

    # تایید/رد فیش توسط ادمین
    elif data.startswith("approve_new_") or data.startswith("reject_new_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        target_uid, bot_type, days = parts[2], parts[3], int(parts[4])
        if parts[0] == "approve":
            if target_uid not in db: db[target_uid] = {'credits': [], 'bots': {}, 'used_free_trial': {'anon': False, 'lottery': False}}
            db[target_uid]['credits'].append({'type': bot_type, 'days': days})
            save_db(db)
            try: await context.bot.send_message(chat_id=target_uid, text=f"🎉 **تایید شد!** اشتراک {days} روزه دریافت کردید.", parse_mode='Markdown')
            except: pass
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ **تایید شد.**", parse_mode='Markdown')
        else:
            try: await context.bot.send_message(chat_id=target_uid, text="❌ **پرداخت شما رد شد.**")
            except: pass
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ **رد شد.**", parse_mode='Markdown')
        return ConversationHandler.END

    elif data.startswith("approve_ren_") or data.startswith("reject_ren_"):
        if int(user_id) != ADMIN_ID: return
        parts = data.split('_')
        target_uid, b_id = parts[2], parts[3]
        if parts[0] == "approve":
            days = int(parts[4])
            token = get_token_by_id(target_uid, b_id, db)
            if token:
                old_expiry = datetime.fromisoformat(db[target_uid]['bots'][token]['expiry_date'])
                if old_expiry < datetime.now(): old_expiry = datetime.now()
                db[target_uid]['bots'][token]['expiry_date'] = (old_expiry + timedelta(days=days)).isoformat()
                save_db(db)
                try: await context.bot.send_message(chat_id=target_uid, text=f"🎉 **تمدید ربات شما تایید شد!** ({days} روز)", parse_mode='Markdown')
                except: pass
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ **تایید شد.**", parse_mode='Markdown')
        else:
            try: await context.bot.send_message(chat_id=target_uid, text="❌ **پرداخت تمدید شما رد شد.**")
            except: pass
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ **رد شد.**", parse_mode='Markdown')
        return ConversationHandler.END

async def show_receipt_request(update_or_query, context, db):
    bot_type = context.user_data['buy_plan_type']
    days = context.user_data['buy_plan_days']
    buy_action = context.user_data['buy_action']
    discount_percent = context.user_data.get('discount_percent', 0)
    
    base_price = int(db['global']['prices'][bot_type][str(days)])
    final_price = int(base_price - (base_price * discount_percent / 100))
    
    caption = f"خرید اشتراک جدید ({'ناشناس' if bot_type=='anon' else 'قرعه‌کشی'})" if buy_action == 'new' else f"تمدید ربات {context.user_data.get('renew_id','')}"
    disc_text = f"\n🎁 تخفیف اعمال شده: {discount_percent}%" if discount_percent > 0 else ""
    msg = (f"<tg-emoji emoji-id=\"5445353829304387411\">💳</tg-emoji> <b>{caption} ({days} روزه)</b>\n\n"
           f"<tg-emoji emoji-id=\"5850406018827162804\">💸</tg-emoji> مبلغ قابل پرداخت: <code>{final_price:,}</code> تومان{disc_text}\n"
           f"<tg-emoji emoji-id=\"5949468580515945223\">🏧</tg-emoji> کارت: <code>{CARD_NUMBER}</code>\n"
           f"<tg-emoji emoji-id=\"5318920793952893165\">👤</tg-emoji> به نام: <code>{CARD_NAME}</code>\n"
           f"<tg-emoji emoji-id=\"5319084260408179576\">🖼</tg-emoji> <b>فیش واریزی را بفرستید:</b>")
    
    if getattr(update_or_query, 'message', None) and update_or_query.message.text:
        await update_or_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]), parse_mode='HTML')
    else:
        await update_or_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_flow")]]), parse_mode='HTML')

async def wrong_receipt_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ فقط عکس فیش واریزی را ارسال کنید.")
    return WAITING_RECEIPT

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
    days = context.user_data.get('buy_plan_days', 30)
    
    if context.user_data.get('buy_action', 'new') == 'new': 
        bot_type = context.user_data.get('buy_plan_type', 'anon')
        cb_approve, cb_reject = f"approve_new_{user.id}_{bot_type}_{days}", f"reject_new_{user.id}_{bot_type}_{days}"
        caption_txt = f"خرید اشتراک جدید ({bot_type}) ({days} روزه)"
    else: 
        b_id = context.user_data.get('renew_id')
        cb_approve, cb_reject = f"approve_ren_{user.id}_{b_id}_{days}", f"reject_ren_{user.id}_{b_id}"
        caption_txt = f"تمدید {b_id} ({days} روزه)"

    keyboard = [[InlineKeyboardButton("✅ تایید", callback_data=cb_approve), InlineKeyboardButton("❌ رد", callback_data=cb_reject)]]
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_id, caption=f"🧾 **{caption_txt}**\n👤 از: `{user.id}`\nتخفیف: {context.user_data.get('discount_percent', 0)}%", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    await update.message.reply_text("✅ فیش ارسال شد.")
    return ConversationHandler.END

async def handle_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_token'] = update.message.text
    await update.message.reply_text("✅ توکن دریافت شد.\n\n🤖 لطفاً آیدی ربات جدید را (همراه با @) بفرستید:")
    return WAITING_BOT_USERNAME

async def handle_bot_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not username.startswith('@'): username = '@' + username
    context.user_data['temp_bot_username'] = username
    await update.message.reply_text("✅ آیدی ربات ثبت شد.\n\n👤 حالا شناسه عددی مدیر کل ربات را بفرستید:")
    return WAITING_ADMIN_ID

async def handle_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: admin_id_child = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ آیدی باید عددی باشد.")
        return WAITING_ADMIN_ID
        
    user_id, token, db = str(update.effective_user.id), context.user_data['temp_token'], load_db()
    bot_type = context.user_data.get('setup_bot_type', 'anon')
    bot_username = context.user_data.get('temp_bot_username', 'نامشخص')
    
    days = 0
    for i, c in enumerate(db[user_id]['credits']):
        if c['type'] == bot_type:
            days = c['days']
            db[user_id]['credits'].pop(i)
            break
            
    db[user_id]['bots'][token] = {
        'bot_type': bot_type, 
        'bot_username': bot_username,
        'admin_id': admin_id_child, 
        'expiry_date': (datetime.now() + timedelta(days=days)).isoformat(),
        'warned': False, 'admin_disabled': False, 'user_disabled': False, 'blocked_users': [],
        'bot_name': "", 'co_admins': {}, 'force_join': ""
    }
    save_db(db)
    
    # 📌 تنظیم Webhook برای ربات جدید در لحظه ساخته شدن روی ورسل
    webhook_url = f"https://{VERCEL_DOMAIN}/webhook/{token}"
    try:
        requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}")
    except: pass
    
    bot_name_fa = "قرعه‌کشی" if bot_type == 'lottery' else "پیام ناشناس"
    log_ch = db['global'].get('log_channel')
    if log_ch:
        try:
            log_txt = (f"🟢 **ربات جدید ساخته شد!**\n\n👤 مشتری: `{user_id}`\n👤 آیدی: @{update.effective_user.username}\n"
                       f"🤖 نوع: {bot_name_fa}\n🔗 آیدی ربات: {bot_username}\n⏳ اعتبار: {days} روز")
            await context.bot.send_message(chat_id=log_ch, text=log_txt, parse_mode='Markdown')
        except: pass
    
    await update.message.reply_text(f"🚀 **ربات {bot_name_fa} شما ساخته و برای {days} روز روشن شد!**", parse_mode='Markdown')
    return ConversationHandler.END

async def handle_global_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: new_price = int(update.message.text)
    except: return WAITING_GLOBAL_PRICE
    db = load_db()
    db['global']['prices'][context.user_data['edit_price_type']][str(context.user_data['edit_price_days'])] = str(new_price)
    save_db(db)
    await update.message.reply_text("✅ قیمت با موفقیت بروزرسانی شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="store_prices")]]))
    return ConversationHandler.END

async def handle_global_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    db = load_db()
    db['global']['force_join'] = "" if txt == "لغو" else txt
    save_db(db)
    await update.message.reply_text("✅ تنظیم شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="store_channels")]]))
    return ConversationHandler.END

async def handle_global_log_ch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    db = load_db()
    db['global']['log_channel'] = "" if txt == "لغو" else txt
    save_db(db)
    await update.message.reply_text("✅ تنظیم شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="store_channels")]]))
    return ConversationHandler.END

async def handle_discount_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_disc_name'] = update.message.text.strip()
    await update.message.reply_text("درصد تخفیف؟ (مثلا 20):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="store_discounts")]]))
    return WAITING_DISCOUNT_PERCENT

async def handle_discount_percent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['new_disc_percent'] = int(update.message.text)
    except: return WAITING_DISCOUNT_PERCENT
    await update.message.reply_text("تعداد مجاز استفاده؟ (مثلا 100):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="store_discounts")]]))
    return WAITING_DISCOUNT_USES

async def handle_discount_uses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: limit = int(update.message.text)
    except: return WAITING_DISCOUNT_USES
    db, code = load_db(), context.user_data['new_disc_name']
    db['global']['discounts'][code] = {'percent': context.user_data['new_disc_percent'], 'limit': limit, 'used': 0}
    save_db(db)
    await update.message.reply_text("✅ کد ساخته شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="store_discounts")]]))
    return ConversationHandler.END

async def handle_check_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code, db = update.message.text.strip(), load_db()
    discounts = db['global'].get('discounts', {})
    if code in discounts and discounts[code].get('used', 0) < discounts[code]['limit']:
        db['global']['discounts'][code]['used'] = discounts[code].get('used', 0) + 1
        save_db(db)
        context.user_data['discount_percent'] = discounts[code]['percent']
        await update.message.reply_text(f"✅ تخفیف {discounts[code]['percent']} درصدی اعمال شد!")
        await show_receipt_request(update, context, db)
        return WAITING_RECEIPT
    else:
        await update.message.reply_text("❌ کد نامعتبر یا تمام شده است.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏩ پرداخت بدون کد", callback_data="skip_discount")]]))
        return WAITING_DISCOUNT

async def handle_edit_bot_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not username.startswith('@'): username = '@' + username
    user_id, b_id, db = str(update.effective_user.id), context.user_data['edit_bid'], load_db()
    db[user_id]['bots'][get_token_by_id(user_id, b_id, db)]['bot_username'] = username
    save_db(db)
    await update.message.reply_text("✅ آیدی ربات ثبت شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))
    return ConversationHandler.END

async def handle_new_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: new_admin_id = int(update.message.text)
    except ValueError: return WAITING_NEW_ADMIN_ID
    user_id, b_id, db = str(update.effective_user.id), context.user_data['edit_bid'], load_db()
    db[user_id]['bots'][get_token_by_id(user_id, b_id, db)]['admin_id'] = new_admin_id
    save_db(db)
    await update.message.reply_text("✅ شناسه مدیر تغییر یافت.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
    return ConversationHandler.END

async def handle_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id, b_id, db = str(update.effective_user.id), context.user_data['edit_bid'], load_db()
    db[user_id]['bots'][get_token_by_id(user_id, b_id, db)]['force_join'] = "" if text == "لغو" else text
    save_db(db)
    await update.message.reply_text("✅ تنظیم شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))
    return ConversationHandler.END

async def handle_bot_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db, user_id, b_id = load_db(), str(update.effective_user.id), context.user_data['edit_bid']
    db[user_id]['bots'][get_token_by_id(user_id, b_id, db)]['bot_name'] = update.message.text
    save_db(db)
    await update.message.reply_text("✅ نام تغییر یافت.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))
    return ConversationHandler.END

async def handle_co_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ad_id_str, user_id, b_id, db = str(update.message.text).strip(), str(update.effective_user.id), context.user_data['edit_bid'], load_db()
    token = get_token_by_id(user_id, b_id, db)
    if ad_id_str in db[user_id]['bots'][token].get('co_admins', {}):
        del db[user_id]['bots'][token]['co_admins'][ad_id_str]
        save_db(db)
        await update.message.reply_text("✅ ادمین فرعی حذف شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))
        return ConversationHandler.END
    context.user_data['pending_coadmin'] = ad_id_str
    await update.message.reply_text("👤 نام ادمین را بفرستید:")
    return WAITING_CO_ADMIN_NAME

async def handle_co_admin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db, user_id, b_id = load_db(), str(update.effective_user.id), context.user_data['edit_bid']
    db[user_id]['bots'][get_token_by_id(user_id, b_id, db)]['co_admins'][context.user_data['pending_coadmin']] = update.message.text
    save_db(db)
    await update.message.reply_text("✅ ادمین اضافه شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"mybot_{b_id}")]]))
    return ConversationHandler.END

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db, success = load_db(), 0
    await update.message.reply_text("⏳ در حال ارسال...")
    for uid in db.keys():
        if uid != 'global':
            try:
                await context.bot.copy_message(chat_id=uid, from_chat_id=update.effective_user.id, message_id=update.message.message_id)
                success += 1
            except: pass
    await update.message.reply_text(f"✅ به {success} نفر ارسال شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
    return ConversationHandler.END

async def handle_manage_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_uid, db = update.message.text.strip(), load_db()
    if target_uid not in db or target_uid == 'global':
        await update.message.reply_text("❌ یافت نشد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
        return ConversationHandler.END
    udata = db[target_uid]
    c_anon = sum(1 for c in udata.get('credits', []) if c['type'] == 'anon')
    c_lottery = sum(1 for c in udata.get('credits', []) if c['type'] == 'lottery')
    b_count = len(udata.get('bots', {}))
    msg = (f"⚙️ **وضعیت کاربر:** `{target_uid}`\n\n🎭 اشتراک ناشناس استفاده نشده: {c_anon}\n"
           f"🎁 اشتراک قرعه‌کشی استفاده نشده: {c_lottery}\n🤖 ربات‌های فعال: {b_count}\n\nگزینه مورد نظر را انتخاب کنید:")
    keyboard = [[InlineKeyboardButton(f"🤖 مدیریت ربات {bdata.get('bot_username', t.split(':')[0])}", callback_data=f"admmngbot_{target_uid}_{t.split(':')[0]}")] for t, bdata in udata.get('bots', {}).items()]
    keyboard.extend([
        [InlineKeyboardButton("➕ افزودن اشتراک ناشناس", callback_data=f"admadd_anon_{target_uid}")],
        [InlineKeyboardButton("➕ افزودن اشتراک قرعه‌کشی", callback_data=f"admadd_lottery_{target_uid}")],
        [InlineKeyboardButton("💬 ارسال پیام تکی به این کاربر", callback_data=f"admsendmsg_{target_uid}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]
    ])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def handle_mod_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: days = int(update.message.text)
    except ValueError: return WAITING_MOD_DAYS
    target_uid, b_id, action, db = context.user_data['mod_bot_uid'], context.user_data['mod_bot_id'], context.user_data['mod_action'], load_db()
    token = get_token_by_id(target_uid, b_id, db)
    old_expiry = datetime.fromisoformat(db[target_uid]['bots'][token]['expiry_date'])
    db[target_uid]['bots'][token]['expiry_date'] = (old_expiry + timedelta(days=days) if action == "add" else old_expiry - timedelta(days=days)).isoformat()
    save_db(db)
    await update.message.reply_text("✅ روزهای اشتراک ویرایش شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="cancel_flow")]]))
    return ConversationHandler.END

async def handle_admin_send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.copy_message(chat_id=context.user_data['send_msg_uid'], from_chat_id=update.effective_user.id, message_id=update.message.message_id)
        await update.message.reply_text("✅ ارسال شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
    except: await update.message.reply_text("❌ خطا در ارسال.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_flow")]]))
    return ConversationHandler.END

# ==========================================
# 🚀 هسته وب‌هوک Flask (مخصوص ورسل)
# ==========================================
flask_app = Flask(__name__)
telegram_apps_cache = {}

async def get_or_create_app(token, bot_type, admin_id):
    if token in telegram_apps_cache:
        return telegram_apps_cache[token]
        
    app = Application.builder().token(token).build()
    
    if bot_type == 'main':
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', main_start), CallbackQueryHandler(handle_all_callbacks, pattern="^.*$")],
            states={
                WAITING_RECEIPT: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_receipt), MessageHandler(filters.ALL & ~filters.COMMAND, wrong_receipt_format)],
                WAITING_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_token)],
                WAITING_BOT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bot_username)],
                WAITING_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_id)],
                WAITING_BOT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bot_name)],
                WAITING_CO_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_co_admin)],
                WAITING_CO_ADMIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_co_admin_name)],
                WAITING_NEW_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_admin_id)],
                WAITING_BROADCAST: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast)],
                WAITING_FORCE_JOIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_force_join)],
                WAITING_MANAGE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manage_user)],
                WAITING_EDIT_BOT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_bot_username)],
                WAITING_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_check_discount)],
                WAITING_GLOBAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_price)],
                WAITING_GLOBAL_FORCE_JOIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_force_join)],
                WAITING_GLOBAL_LOG_CH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_log_ch)],
                WAITING_DISCOUNT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_discount_name)],
                WAITING_DISCOUNT_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_discount_percent)],
                WAITING_DISCOUNT_USES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_discount_uses)],
                WAITING_MOD_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mod_days)],
                WAITING_ADMIN_SEND_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_admin_send_msg)]
            },
            fallbacks=[CommandHandler('start', main_start), CallbackQueryHandler(handle_all_callbacks, pattern="^.*$")],
            allow_reentry=True
        )
        app.add_handler(conv_handler)
        
    elif bot_type == 'anon':
        app.bot_data['admin_id'] = admin_id
        child_conv = ConversationHandler(
            entry_points=[CommandHandler("start", child_start), CallbackQueryHandler(child_start_anon, pattern="^start_anon$"), CallbackQueryHandler(child_admin_actions, pattern="^(creply_|cblock_|cancel_reply)")],
            states={
                CHILD_WAITING_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, child_receive_msg), CallbackQueryHandler(child_confirm_anon, pattern="^(confirm_anon|cancel_anon)$")],
                CHILD_ADMIN_REPLY: [MessageHandler(filters.ALL & ~filters.COMMAND, child_do_reply), CallbackQueryHandler(child_admin_actions, pattern="^cancel_reply$")]
            },
            fallbacks=[CommandHandler("start", child_start)], allow_reentry=True
        )
        app.add_handler(child_conv)
        
    elif bot_type == 'lottery':
        app.bot_data['admin_id'] = admin_id
        app.bot_data['db_file'] = f"/tmp/giveaway_db_{token.split(':')[0]}.json"
        l_load_data(app.bot_data['db_file'], app.bot_data)
        
        app.add_handler(CommandHandler("start", l_start))
        app.add_handler(CommandHandler("send_prize", l_send_prize))
        app.add_handler(CommandHandler("clear", l_clear_old_keyboard))
        app.add_handler(CallbackQueryHandler(l_button_handler))
        app.add_handler(MessageHandler(filters.Sticker.ALL, l_handle_sticker))
        app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.Sticker.ALL, l_handle_text))
        
    await app.initialize()
    telegram_apps_cache[token] = app
    return app

async def process_update_async(token, bot_type, admin_id, update_dict):
    tg_app = await get_or_create_app(token, bot_type, admin_id)
    update = Update.de_json(update_dict, tg_app.bot)
    await tg_app.process_update(update)

@flask_app.route(f'/webhook/<token>', methods=['POST'])
def webhook_handler(token):
    update_dict = request.get_json()
    if not update_dict: return "OK", 200
        
    if token == MAIN_BOT_TOKEN:
        bot_type = 'main'
        admin_id = ADMIN_ID
    else:
        db = load_db()
        uid = get_uid_by_token(token, db)
        if not uid: return "Not Found", 404
        
        bdata = db[uid]['bots'][token]
        expiry = datetime.fromisoformat(bdata['expiry_date'])
        
        if datetime.now() > expiry or bdata.get('admin_disabled') or bdata.get('user_disabled'):
            return "Disabled", 403
            
        bot_type = bdata.get('bot_type', 'anon')
        admin_id = bdata['admin_id']

    asyncio.run(process_update_async(token, bot_type, admin_id, update_dict))
    return "OK", 200

@flask_app.route('/', methods=['GET'])
def index():
    return "SaaS Bot Platform is running perfectly on Vercel!"

app = flask_app
