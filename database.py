import sqlite3

DB_NAME = "svara.db"

def init_db():
    """Создает таблицы, если их еще нет."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 50000
            )
        ''')
        conn.commit()

def get_or_create_user(tg_id, username):
    """Возвращает данные юзера, либо создает нового со стартовым балансом."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,))
        result = cursor.fetchone()

        if result:
            return {'tg_id': tg_id, 'username': username, 'balance': result[0]}
        else:
            start_balance = 50000
            cursor.execute("INSERT INTO users (tg_id, username, balance) VALUES (?, ?, ?)",
                           (tg_id, username, start_balance))
            conn.commit()
            return {'tg_id': tg_id, 'username': username, 'balance': start_balance}

def update_balance(tg_id, new_balance):
    """Устанавливает новое абсолютное значение баланса."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = ? WHERE tg_id = ?", (new_balance, tg_id))
        conn.commit()

def add_to_balance(tg_id, amount):
    """Безопасно прибавляет или отнимает (если amount отрицательный) сумму."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id))
        conn.commit()

# Инициализируем базу при импорте
init_db()