import threading
import asyncio
import os
import hmac
import hashlib
import json
import time
from urllib.parse import parse_qs, unquote

# --- ИМПОРТЫ ДЛЯ БОТА ---
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# --- ИМПОРТЫ ДЛЯ СЕРВЕРА ---
import socketio
from aiohttp import web

# --- ИМПОРТЫ ТВОЕЙ ЛОГИКИ ---
from logic import SvaraGame
# from poker_logic import PokerGame  # Раскомментируй, когда будет готов файл покера
import database

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8895138994:AAHZpVyZA-seb2cXqakY-pvKjYKZEe4fSn0")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://games-card.up.railway.app")

# ==========================================
# 2. ЛОГИКА ТЕЛЕГРАМ-БОТА (Бронебойный запуск)
# ==========================================
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def start_command(message):
    markup = InlineKeyboardMarkup()
    web_app_btn = InlineKeyboardButton(
        text="🃏 Играть в Свару",
        web_app=WebAppInfo(url=WEBAPP_URL)
    )
    markup.add(web_app_btn)

    bot.send_message(
        message.chat.id,
        "Добро пожаловать в клуб! Жми кнопку ниже, чтобы открыть лобби.",
        reply_markup=markup
    )

def run_bot():
    """Запуск бота с защитой от падения веб-сервера"""
    print("🤖 Телеграм-бот запускается...")
    while True:
        try:
            # skip_pending=True убивает зависшие запросы, чтобы избежать 409 ошибки
            bot.infinity_polling(skip_pending=True)
        except Exception as e:
            print(f"❌ КРАШ БОТА: {e}")
            print("⏳ Сервер продолжает работу. Перезапуск бота через 5 секунд...")
            time.sleep(5)

# ==========================================
# 3. ЛОГИКА ВЕБ-СЕРВЕРА И ИГРЫ
# ==========================================
sio = socketio.AsyncServer(async_mode='aiohttp', cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

def broadcast_game_log(room_id, message):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(sio.emit('game_log', {'text': message}, room=room_id))
    except RuntimeError:
        pass

games = {
    'table_1': SvaraGame('table_1', log_callback=broadcast_game_log),
    'table_2': SvaraGame('table_2', log_callback=broadcast_game_log),
    'table_vip': SvaraGame('table_vip', log_callback=broadcast_game_log),
}

TABLE_LIMITS = {
    'table_1': {'min': 500, 'name': 'Уличный Катран'},
    'table_2': {'min': 5000, 'name': 'Зал Казино'},
    'table_vip': {'min': 100000, 'name': 'Кабинет Дона'},
}

def validate_telegram_data(init_data: str, token: str) -> dict:
    try:
        parsed_data = dict(parse_qs(init_data))
        if 'hash' not in parsed_data:
            return None

        received_hash = parsed_data.pop('hash')[0]
        data_check_arr = []
        for key, value in sorted(parsed_data.items()):
            data_check_arr.append(f"{key}={value[0]}")
        data_check_string = "\n".join(data_check_arr)

        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash == received_hash:
            user_json = parsed_data.get('user', ['{}'])[0]
            return json.loads(user_json)
        return None
    except Exception as e:
        print(f"Ошибка валидации Telegram: {e}")
        return None

async def broadcast_state(room_id):
    game = games[room_id]
    active_sids = list(game.players.keys())

    for client_sid in active_sids:
        player_state = game.get_state(request_sid=client_sid)
        await sio.emit('update_state', player_state, room=client_sid)

    spectator_state = game.get_state(request_sid=None)
    if active_sids:
        await sio.emit('update_state', spectator_state, room=room_id, skip_sid=active_sids)
    else:
        await sio.emit('update_state', spectator_state, room=room_id)

# --- РОУТЕРЫ ---
# Жестко прописываем путь к папке, чтобы Linux не потерял файлы
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

async def lobby_handler(request):
    try:
        file_path = os.path.join(BASE_DIR, 'lobby.html')
        with open(file_path, 'r', encoding='utf-8') as f:
            return web.Response(text=f.read(), content_type='text/html')
    except Exception as e:
        return web.Response(text=f"Ошибка сервера: файл lobby.html не найден. {e}", status=500)

async def game_handler(request):
    try:
        room_id = request.query.get('room', 'table_1')
        filename = 'table_poker.html' if 'poker' in room_id else 'table.html'
        file_path = os.path.join(BASE_DIR, filename)

        if not os.path.exists(file_path):
            return web.Response(text=f"Файл стола {filename} не найден", status=404)

        with open(file_path, 'r', encoding='utf-8') as f:
            return web.Response(text=f.read(), content_type='text/html')
    except Exception as e:
        return web.Response(text=f"Ошибка сервера: {e}", status=500)

async def api_profile(request):
    tg_id = request.query.get('tg_id')
    name = request.query.get('name', 'Аноним')
    if not tg_id:
        return web.json_response({'error': 'no tg_id'}, status=400)

    user_db = await asyncio.to_thread(database.get_or_create_user, int(tg_id), name)
    return web.json_response({'balance': user_db['balance'], 'username': user_db['username']})

async def api_topup(request):
    tg_id = request.query.get('tg_id')
    if not tg_id:
        return web.json_response({'error': 'no tg_id'}, status=400)

    await asyncio.to_thread(database.add_to_balance, int(tg_id), 50000)
    user_db = await asyncio.to_thread(database.get_or_create_user, int(tg_id), "Аноним")
    return web.json_response({'success': True, 'new_balance': user_db['balance']})

async def api_tables_info(request):
    info = {room: len(game.players) for room, game in games.items()}
    return web.json_response(info)

app.router.add_get('/', lobby_handler)
app.router.add_get('/game', game_handler)
app.router.add_get('/api/profile', api_profile)
app.router.add_get('/api/topup', api_topup)
app.router.add_get('/api/tables_info', api_tables_info)
app.router.add_static('/static/', path=os.path.join(BASE_DIR, 'static'), name='static')

# --- SOCKET.IO СОБЫТИЯ ---
def get_room_and_game(session):
    room_id = session.get('room')
    return room_id, games.get(room_id) if room_id else None

@sio.event
async def connect(sid, environ):
    query = environ.get('QUERY_STRING', '')
    params = parse_qs(query)
    room_id = params.get('room', [''])[0]

    if room_id in games:
        await sio.enter_room(sid, room_id)
        await sio.save_session(sid, {'room': room_id})
        game = games[room_id]
        print(f"[+] Клиент {sid} подключился к {room_id}")
        await sio.emit('update_state', game.get_state(request_sid=sid), room=sid)
    else:
        return False

@sio.event
async def disconnect(sid):
    session = await sio.get_session(sid)
    room_id, game = get_room_and_game(session)
    if not game: return

    print(f"[-] Отключился клиент: {sid} от {room_id}")
    if sid in game.players:
        player_data = game.players.pop(sid)
        game.remove_player(sid)
        tg_id = player_data['tg_id']
        table_balance = player_data['balance']

        await asyncio.to_thread(database.add_to_balance, tg_id, table_balance)
        await broadcast_state(room_id)

@sio.event
async def join_game(sid, data):
    session = await sio.get_session(sid)
    room_id, game = get_room_and_game(session)
    if not game: return

    init_data = data.get('initData')
    if not init_data:
        await sio.emit('error', {'message': 'Ошибка авторизации'}, room=sid)
        return

    tg_user = validate_telegram_data(init_data, BOT_TOKEN)
    if not tg_user:
        await sio.emit('error', {'message': 'Недействительная подпись'}, room=sid)
        return

    tg_id = tg_user.get('id')
    name = tg_user.get('first_name', 'Аноним')
    seat = data.get('seat')

    try: buyin_amount = int(data.get('buyin', 0))
    except ValueError: buyin_amount = 0

    table_limit = TABLE_LIMITS[room_id]['min']
    if buyin_amount < table_limit:
        await sio.emit('error', {'message': f"Минимум: {table_limit}!"}, room=sid)
        return

    user_db = await asyncio.to_thread(database.get_or_create_user, tg_id, name)
    if buyin_amount > user_db.get('balance', 0):
        await sio.emit('error', {'message': 'Недостаточно средств!'}, room=sid)
        return

    await asyncio.to_thread(database.add_to_balance, tg_id, -buyin_amount)
    success, msg = game.add_player(sid=sid, tg_id=tg_id, name=user_db['username'], balance=buyin_amount, seat=seat)

    if success:
        await broadcast_state(room_id)
    else:
        await asyncio.to_thread(database.add_to_balance, tg_id, buyin_amount)
        await sio.emit('error', {'message': msg}, room=sid)

@sio.event
async def leave_game(sid):
    session = await sio.get_session(sid)
    room_id, game = get_room_and_game(session)
    if not game: return

    if sid in game.players:
        player_data = game.players.pop(sid)
        game.remove_player(sid)
        await asyncio.to_thread(database.add_to_balance, player_data['tg_id'], player_data['balance'])
        await broadcast_state(room_id)

@sio.event
async def player_action(sid, data):
    session = await sio.get_session(sid)
    room_id, game = get_room_and_game(session)
    if not game: return

    action_type = data.get('action')
    amount = data.get('amount', 0)

    if not action_type:
        return

    success, msg = game.handle_action(sid, action_type, amount)

    if success:
        await broadcast_state(room_id)
        if game.state == "ROUND_END":
            sids_to_remove = [p_sid for p_sid, p_data in game.players.items() if p_data['balance'] <= 0]
            for p_sid in sids_to_remove:
                game.remove_player(p_sid)
                await sio.emit('error', {'message': 'Баланс исчерпан!'}, room=p_sid)
            if sids_to_remove:
                await broadcast_state(room_id)
    else:
        await sio.emit('error', {'message': msg}, room=sid)

@sio.event
async def request_state(sid, data=None):
    session = await sio.get_session(sid)
    room_id, game = get_room_and_game(session)
    if game:
        await sio.emit('update_state', game.get_state(request_sid=sid), room=sid)

async def game_timer_loop():
    while True:
        await asyncio.sleep(1)
        for room_id, game in games.items():
            try:
                if game.check_timeouts():
                    await broadcast_state(room_id)
            except Exception:
                pass

async def start_background_tasks(app):
    app['timer_task'] = asyncio.create_task(game_timer_loop())

app.on_startup.append(start_background_tasks)

# ==========================================
# 4. ЗАПУСК
# ==========================================
if __name__ == '__main__':
    # Бот теперь работает в защищенном фоне
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    print("🚀 Веб-сервер запускается...")
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host='0.0.0.0', port=port)
