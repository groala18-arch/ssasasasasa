import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

TOKEN = "8755606935:AAGqyXPEG4sGGFcHft18czu6Bg6dLpwqfPI"
WEBAPP_URL = "https://4bf6-5-253-66-23.ngrok-free.app"

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