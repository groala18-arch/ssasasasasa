import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

TOKEN = "8895138994:AAEpJHT7QYAWzYtWxxd_bekpJ1VZMmtktGE"
WEBAPP_URL = "https://games-card.up.railway.app"

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start_command(message):
    markup = InlineKeyboardMarkup()
    # Создаем кнопку с WebApp
    web_app_btn = InlineKeyboardButton(
        text="🃏 Играть в Свару",
        web_app=WebAppInfo(url=WEBAPP_URL)
    )
    markup.add(web_app_btn)

    bot.send_message(
        message.chat.id,
        "Добро пожаловать в клуб! Жми кнопку ниже, чтобы открыть стол.",
        reply_markup=markup
    )

if __name__ == "__main__":
    print("🤖 Телеграм-бот запущен...")
    bot.infinity_polling()