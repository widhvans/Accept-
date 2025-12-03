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

# --- Logging Setup ---
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

# --- HEALTH CHECK SERVER (THE FIX FOR KOYEB) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

def start_health_check():
    # Koyeb/Render usually provide a PORT env variable. Default to 8000.
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"🌍 Health Check Server is listening on port {port}...")
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
        "Me upcoming aur pending join requests ko auto-accept kar sakta hu.\n"
        "Shuru karne ke liye neeche button par click karein."
    )
    
    keyboard = [
        [InlineKeyboardButton("🔗 Connect Channel/Group", callback_data="connect_chat")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

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
        "👇 **Select Mode:**\n"
        "• **Upcoming:** Nayi requests turant accept hongi.\n"
        "• **Pending:** Requests store hongi (queue), baad me `/accept` se approve karein."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🟢 Upcoming Mode", callback_data="set_mode_upcoming"),
            InlineKeyboardButton("🟠 Pending Mode", callback_data="set_mode_pending")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

async def set_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != config.OWNER_ID:
        await query.answer("You are not the owner!", show_alert=True)
        return

    data = query.data
    if data == "set_mode_upcoming":
        set_mode_db("upcoming")
        new_text = "✅ **Mode Set: Upcoming**\nAb nayi requests turant auto-accept hongi."
    elif data == "set_mode_pending":
        set_mode_db("pending")
        new_text = "✅ **Mode Set: Pending**\nAb nayi requests store ki jayengi. Group me `/accept` likh kar approve karein."
    
    await query.edit_message_text(new_text, parse_mode="Markdown")

# --- Accept Command (Group Admin Only) ---
async def accept_pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == "private":
        await update.message.reply_text("⚠️ Ye command us Group/Channel me use karein jaha requests accept karni hain.")
        return

    try:
        member = await chat.get_member(user.id)
        if member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER] and user.id != config.OWNER_ID:
            await update.message.reply_text("🚫 Aap is chat ke Admin nahi hain.")
            return
    except:
        pass 

    status_msg = await update.message.reply_text("⏳ **Checking pending requests database...**", parse_mode="Markdown")
    
    pending_requests = list(pending_col.find({"chat_id": chat.id}))
    
    if not pending_requests:
        await status_msg.edit_text("✅ **No Pending Requests found in Database.**")
        return
    
    await status_msg.edit_text(f"🚀 **Processing {len(pending_requests)} requests...**")
    
    success_count = 0
    fail_count = 0
    
    for req in pending_requests:
        try:
            await context.bot.approve_chat_join_request(chat_id=req['chat_id'], user_id=req['user_id'])
            success_count += 1
            pending_col.delete_one({"_id": req['_id']})
        except Exception as e:
            logger.error(f"Failed to accept {req['user_id']}: {e}")
            fail_count += 1
    
    await status_msg.edit_text(
        f"✅ **Operation Completed!**\n\n"
        f"👥 Total Processed: `{len(pending_requests)}`\n"
        f"✅ Accepted: `{success_count}`\n"
        f"❌ Failed/Expired: `{fail_count}`"
    )

# --- Connect Button Handler ---
async def connect_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    
    if len(user_data['chats']) >= 3:
        await query.edit_message_text("❌ **Limit Reached!**\nAap already 3 channel/group connect kar chuke hain.")
        return ConversationHandler.END

    await query.edit_message_text(
        "📝 **Send Channel/Group ID**\n\n"
        "Format: `-100xxxxxxxxx`\n\n"
        "⚠️ **Note:** ID bhejne se pehle, Bot ko us Channel/Group me **Admin** banayein."
    )
    return WAITING_FOR_ID

# --- Process ID and Check Admin ---
async def receive_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id_text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if not chat_id_text.startswith("-100"):
        await update.message.reply_text("❌ Invalid ID Format! `-100...` se shuru hona chahiye.")
        return WAITING_FOR_ID
    
    try:
        chat_id = int(chat_id_text)
    except ValueError:
        await update.message.reply_text("❌ Ye number nahi hai. Dobara bhejein.")
        return WAITING_FOR_ID

    status_msg = await update.message.reply_text("⏳ Verifying...")

    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            await status_msg.edit_text("❌ Bot is not Admin there! Pehle Bot ko admin banayein.")
            return ConversationHandler.END

        users_col.update_one({"user_id": user_id}, {"$addToSet": {"chats": chat_id}})
        chat_info = await context.bot.get_chat(chat_id)
        
        await status_msg.edit_text(
            f"✅ **Success!**\nConnected: **{chat_info.title}** (`{chat_id}`)\nAb is group/channel ki request auto accept hongi."
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Operation Cancelled.")
    return ConversationHandler.END

# --- Auto Accept Logic ---
async def auto_approve_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_request = update.chat_join_request
    chat_id = join_request.chat.id
    user_id = join_request.from_user.id
    
    is_registered = users_col.find_one({"chats": chat_id})
    if not is_registered:
        return

    current_mode = get_mode()

    if current_mode == "upcoming":
        try:
            await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
            logging.info(f"Upcoming Mode: Approved user {user_id}")
        except Exception as e:
            logging.error(f"Failed to approve {user_id}: {e}")

    elif current_mode == "pending":
        try:
            existing = pending_col.find_one({"chat_id": chat_id, "user_id": user_id})
            if not existing:
                pending_col.insert_one({
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "date": join_request.date
                })
                logging.info(f"Pending Mode: Queued user {user_id}")
        except Exception as e:
            logging.error(f"Failed to queue request: {e}")

# --- Main Application ---
def main():
    # 1. Start the Health Check Server in a background thread
    #    This prevents Koyeb from killing the bot.
    health_thread = Thread(target=start_health_check, daemon=True)
    health_thread.start()

    # 2. Start the Bot
    application = ApplicationBuilder().token(config.BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(connect_button, pattern='^connect_chat$')],
        states={
            WAITING_FOR_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_chat_id)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("change", change_mode_command))
    application.add_handler(CommandHandler("accept", accept_pending_command))
    application.add_handler(CallbackQueryHandler(set_mode_callback, pattern='^set_mode_'))
    application.add_handler(conv_handler)
    application.add_handler(ChatJoinRequestHandler(auto_approve_request))

    print("🤖 Bot is Running with Modes (Upcoming/Pending)...")
    application.run_polling()

if __name__ == '__main__':
    main()
