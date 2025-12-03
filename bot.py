import logging
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    filters,
    ConversationHandler
)
from pymongo import MongoClient
import config

# --- Logging Setup (Enhanced) ---
# Info level log console pe dikhega taaki pata chale bot kya kar raha hai
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- MongoDB Setup ---
client = MongoClient(config.MONGO_URI)
db = client['AutoAcceptBot']
users_col = db['users']
settings_col = db['settings']
pending_col = db['pending_requests']

# Ensure default mode exists
if not settings_col.find_one({"_id": "global_mode"}):
    settings_col.insert_one({"_id": "global_mode", "value": "upcoming"})

# --- States for Conversation ---
WAITING_FOR_ID = 1

# --- HEALTH CHECK SERVER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

def start_health_check():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"🌍 Health Check Server is listening on port {port}...")
    server.serve_forever()

# --- Helper Functions ---
def get_user_data(user_id):
    data = users_col.find_one({"user_id": user_id})
    if not data:
        users_col.insert_one({"user_id": user_id, "chats": []})
        return {"user_id": user_id, "chats": []}
    return data

def get_mode():
    setting = settings_col.find_one({"_id": "global_mode"})
    return setting['value'] if setting else "upcoming"

def set_mode_db(mode_value):
    settings_col.update_one(
        {"_id": "global_mode"},
        {"$set": {"value": mode_value}},
        upsert=True
    )

# --- Start Command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user_data(user.id)
    connected_count = len(user_data.get("chats", []))
    
    text = (
        f"👋 **Hello {user.first_name}!**\n\n"
        f"🆔 **Your ID:** `{user.id}`\n"
        f"🤖 **Bot Status:** Active\n"
        f"⚙️ **Current Mode:** `{get_mode().title()}`\n"
        f"🔗 **Connected Chats:** {connected_count}/3\n\n"
        "**Options:**\n"
        "1. Connect: Naya group/channel add karein.\n"
        "2. Disconnect: Existing group ko hatayein.\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔗 Connect Chat", callback_data="connect_chat")],
        [InlineKeyboardButton("❌ Disconnect Chat", callback_data="disconnect_mode")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

# --- Disconnect Logic ---
async def disconnect_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    chats = user_data.get("chats", [])
    
    if not chats:
        await query.edit_message_text("❌ **Koi Chat Connected Nahi Hai.**\nPehle connect karein.")
        return

    keyboard = []
    for chat_id in chats:
        try:
            # Try to fetch chat title
            chat = await context.bot.get_chat(chat_id)
            btn_text = f"🗑 {chat.title}"
        except:
            # If bot kicked or error, show ID
            btn_text = f"🗑 {chat_id}"
        
        # Callback data format: unlink_123456789
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"unlink_{chat_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_start")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "❌ **Select Chat to Disconnect:**\nIsse click karne par bot us chat se disconnect ho jayega.",
        reply_markup=reply_markup
    )

async def unlink_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data # unlink_-10012345
    chat_id_to_remove = int(data.split("_")[1])
    user_id = query.from_user.id
    
    # Update DB: Remove chat_id from array
    result = users_col.update_one(
        {"user_id": user_id},
        {"$pull": {"chats": chat_id_to_remove}}
    )
    
    if result.modified_count > 0:
        await query.edit_message_text(f"✅ **Disconnected!**\nChat ID `{chat_id_to_remove}` remove kar diya gaya hai.", parse_mode="Markdown")
    else:
        await query.edit_message_text("❌ **Error:** Chat remove nahi ho paya ya pehle hi hat chuka hai.")

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)

# --- Change Mode Command (Owner Only) ---
async def change_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != config.OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Ye command sirf Bot Owner ke liye hai.")
        return

    current_mode = get_mode()
    text = (
        f"⚙️ **Change Bot Mode**\n\n"
        f"Current Mode: **{current_mode.title()}**\n\n"
        "👇 **Select Mode:**"
    )
    keyboard = [
        [InlineKeyboardButton("🟢 Upcoming Mode", callback_data="set_mode_upcoming")],
        [InlineKeyboardButton("🟠 Pending Mode", callback_data="set_mode_pending")]
    ]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def set_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("Not allowed!", show_alert=True)
        return

    if query.data == "set_mode_upcoming":
        set_mode_db("upcoming")
        msg = "✅ **Mode: Upcoming**\nNew requests will be auto-accepted immediately."
    else:
        set_mode_db("pending")
        msg = "✅ **Mode: Pending**\nNew requests will be QUEUED in DB. Use `/accept` to approve."
    
    await query.edit_message_text(msg, parse_mode="Markdown")

# --- Accept Command (Enhanced Logic & Logging) ---
async def accept_pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == "private":
        await update.message.reply_text("⚠️ Please run this command inside the Group/Channel.")
        return

    # Check Admin
    try:
        member = await chat.get_member(user.id)
        if member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER] and user.id != config.OWNER_ID:
            await update.message.reply_text("🚫 You are not an Admin.")
            return
    except:
        pass 

    status_msg = await update.message.reply_text("⏳ **Fetching pending requests from DB...**", parse_mode="Markdown")
    
    # Fetch from DB (ensure chat.id is int)
    pending_requests = list(pending_col.find({"chat_id": chat.id}))
    
    logger.info(f"CMD /accept: Found {len(pending_requests)} requests for chat {chat.id}")

    if not pending_requests:
        await status_msg.edit_text(
            "⚠️ **No Pending Requests Found in Database.**\n\n"
            "Possible reasons:\n"
            "1. Bot 'Upcoming Mode' me tha (requests save nahi hue).\n"
            "2. DB already clear hai.\n"
            "3. Bot ko requests receive hi nahi hue."
        )
        return
    
    await status_msg.edit_text(f"🚀 **Processing {len(pending_requests)} requests... Check Logs!**")
    
    success_count = 0
    fail_count = 0
    
    for req in pending_requests:
        user_id_target = req['user_id']
        try:
            logger.info(f"Attempting to approve user {user_id_target} in chat {chat.id}...")
            
            # API CALL
            await context.bot.approve_chat_join_request(chat_id=chat.id, user_id=user_id_target)
            
            logger.info(f"✅ SUCCESS: Approved user {user_id_target}")
            success_count += 1
            
            # Delete from DB only on success
            pending_col.delete_one({"_id": req['_id']})
            
        except Exception as e:
            logger.error(f"❌ FAILED: User {user_id_target} | Error: {e}")
            fail_count += 1
            # Optional: Delete invalid request so we don't loop it again?
            # pending_col.delete_one({"_id": req['_id']}) 
    
    await status_msg.edit_text(
        f"✅ **Batch Process Complete!**\n\n"
        f"🔢 Total: `{len(pending_requests)}`\n"
        f"✅ Accepted: `{success_count}`\n"
        f"❌ Failed: `{fail_count}`\n\n"
        "Check Logs for details."
    )

# --- Connect & Validation Logic ---
async def connect_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if len(get_user_data(query.from_user.id)['chats']) >= 3:
        await query.edit_message_text("❌ Limit Reached (Max 3). Use Disconnect option.")
        return ConversationHandler.END

    await query.edit_message_text("📝 **Send Channel/Group ID** (e.g., `-100xxxx`).\nEnsure Bot is Admin first!")
    return WAITING_FOR_ID

async def receive_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        chat_id = int(text)
    except ValueError:
        await update.message.reply_text("❌ Invalid ID (Must be integer). Try again.")
        return WAITING_FOR_ID

    msg = await update.message.reply_text("⏳ Verifying Admin rights...")
    try:
        member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            await msg.edit_text("❌ Bot is not Admin in that chat!")
            return ConversationHandler.END
        
        users_col.update_one({"user_id": update.effective_user.id}, {"$addToSet": {"chats": chat_id}})
        title = (await context.bot.get_chat(chat_id)).title
        await msg.edit_text(f"✅ Connected: **{title}**")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

# --- Auto Approve Event Handler ---
async def auto_approve_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jr = update.chat_join_request
    chat_id = jr.chat.id
    user_id = jr.from_user.id
    
    # 1. Validation: Is chat connected?
    if not users_col.find_one({"chats": chat_id}):
        return

    mode = get_mode()
    
    if mode == "upcoming":
        try:
            await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
            logger.info(f"✅ [Upcoming Mode] Auto-Approved User {user_id} in Chat {chat_id}")
        except Exception as e:
            logger.error(f"❌ [Upcoming Mode] Failed {user_id}: {e}")
            
    elif mode == "pending":
        try:
            if not pending_col.find_one({"chat_id": chat_id, "user_id": user_id}):
                pending_col.insert_one({
                    "chat_id": chat_id, 
                    "user_id": user_id, 
                    "date": jr.date
                })
                logger.info(f"📥 [Pending Mode] Queued User {user_id} in DB for Chat {chat_id}")
            else:
                logger.info(f"⚠️ [Pending Mode] User {user_id} already in queue.")
        except Exception as e:
            logger.error(f"❌ [Pending Mode] DB Error: {e}")

# --- Main ---
def main():
    Thread(target=start_health_check, daemon=True).start()
    
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(connect_button, pattern='^connect_chat$')],
        states={WAITING_FOR_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_chat_id)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("change", change_mode_command))
    app.add_handler(CommandHandler("accept", accept_pending_command))
    
    app.add_handler(CallbackQueryHandler(disconnect_mode_handler, pattern='^disconnect_mode$'))
    app.add_handler(CallbackQueryHandler(unlink_chat_handler, pattern='^unlink_'))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern='^back_to_start$'))
    app.add_handler(CallbackQueryHandler(set_mode_callback, pattern='^set_mode_'))
    
    app.add_handler(conv)
    app.add_handler(ChatJoinRequestHandler(auto_approve_request))
    
    print("🤖 Bot is Running...")
    app.run_polling()

if __name__ == '__main__':
    main()
