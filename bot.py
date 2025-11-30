import logging
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

# --- MongoDB Setup ---
client = MongoClient(config.MONGO_URI)
db = client['AutoAcceptBot']
users_col = db['users']
# Schema: { "user_id": 123, "chats": [-100123, -100456] }

# --- States for Conversation ---
WAITING_FOR_ID = 1

# --- Helper Functions ---
def get_user_data(user_id):
    data = users_col.find_one({"user_id": user_id})
    if not data:
        users_col.insert_one({"user_id": user_id, "chats": []})
        return {"user_id": user_id, "chats": []}
    return data

# --- Start Command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user_data(user.id)
    connected_count = len(user_data.get("chats", []))
    
    text = (
        f"👋 **Hello {user.first_name}!**\n\n"
        f"🆔 **Your ID:** `{user.id}`\n"
        f"🤖 **Bot Status:** Active\n"
        f"🔗 **Connected Chats:** {connected_count}/3\n\n"
        "Me upcoming aur pending join requests ko auto-accept kar sakta hu.\n"
        "Shuru karne ke liye neeche button par click karein."
    )
    
    keyboard = [
        [InlineKeyboardButton("🔗 Connect Channel/Group", callback_data="connect_chat")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

# --- Button Handler ---
async def connect_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = get_user_data(user_id)
    
    # Check Limit (Max 3)
    if len(user_data['chats']) >= 3:
        await query.edit_message_text(
            "❌ **Limit Reached!**\nAap already 3 channel/group connect kar chuke hain."
        )
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
    
    # Basic Validation
    if not chat_id_text.startswith("-100"):
        await update.message.reply_text("❌ Invalid ID Format! `-100...` se shuru hona chahiye. Try again.")
        return WAITING_FOR_ID
    
    try:
        chat_id = int(chat_id_text)
    except ValueError:
        await update.message.reply_text("❌ Ye number nahi hai. Dobara bhejein.")
        return WAITING_FOR_ID

    status_msg = await update.message.reply_text("⏳ Verifying...")

    try:
        # Check if bot is admin in that chat
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        
        if bot_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            await status_msg.edit_text("❌ Bot is not Admin there! Pehle Bot ko admin banayein.")
            return ConversationHandler.END

        # Save to DB
        users_col.update_one(
            {"user_id": user_id},
            {"$addToSet": {"chats": chat_id}} # $addToSet duplicates prevent karta hai
        )
        
        chat_info = await context.bot.get_chat(chat_id)
        chat_title = chat_info.title
        
        await status_msg.edit_text(
            f"✅ **Success!**\n\n"
            f"Connected: **{chat_title}** (`{chat_id}`)\n"
            f"Ab is group/channel ki request auto accept hongi."
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: Bot shayad us chat me add nahi hai ya ID galat hai.\nError: {str(e)}")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Operation Cancelled.")
    return ConversationHandler.END

# --- Auto Accept Logic (The Core Feature) ---
async def auto_approve_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_request = update.chat_join_request
    chat_id = join_request.chat.id
    user_id = join_request.from_user.id
    
    # Check if this chat is in our Database (Registered by any user)
    # Hum check karte hain ki kya ye chat_id kisi bhi user ke document me hai
    is_registered = users_col.find_one({"chats": chat_id})
    
    if is_registered:
        try:
            await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
            logging.info(f"Approved user {user_id} in chat {chat_id}")
            
            # Optional: User ko PM bhej sakte ho (agar allowed ho)
            # await context.bot.send_message(user_id, "Your request has been accepted!")
            
        except Exception as e:
            logging.error(f"Failed to approve: {e}")

# --- Main Application ---
def main():
    application = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # Conversation Handler Setup
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(connect_button, pattern='^connect_chat$')],
        states={
            WAITING_FOR_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_chat_id)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # Handlers Add karna
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    
    # Join Request Handler (Upcoming + Pending*)
    # *Note: Pending requests tabhi process hongi jab wo trigger hongi ya bot restart hoke fetch karega (API limitation)
    application.add_handler(ChatJoinRequestHandler(auto_approve_request))

    print("🤖 Bot is Running...")
    application.run_polling()

if __name__ == '__main__':
    main()
