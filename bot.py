import os
import sqlite3
import random
import asyncio
import logging
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted, BusinessConnection, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart, Command

# Токен вашего @ArtefaktSpyBot из @BotFather
BOT_TOKEN = "8689486048:AAFkgdmV4ZTtL8gAkfmEjWeXkrAufMM42kI"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Внутреннее хранилище в оперативной памяти сервера (RAM)
connection_owners = {}     # Карта соответствий { business_connection_id: user_id }
business_msg_history = {}  # Кэш истории текстовых сообщений { conn_id: { message_id: text } }


# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ SQLITE ---
def init_db():
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT 'Игрок',
            balance INTEGER DEFAULT 0,
            last_bonus TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS games (
            game_id TEXT PRIMARY KEY,
            chat_id INTEGER,
            player_x INTEGER,
            player_o INTEGER,
            board TEXT,
            turn TEXT,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def add_user_if_not_exists(user_id: int, username: str = "Игрок"):
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, username) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username = ?
    """, (user_id, username, username))
    conn.commit()
    conn.close()

def get_balance(user_id: int) -> int:
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res if res else 0

def add_balance(user_id: int, amount: int):
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


# Фоновый таймер ожидания соперника (1 минута)
async def wait_for_opponent(chat_id: int, message_id: int, game_id: str):
    await asyncio.sleep(60)
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM games WHERE game_id = ?", (game_id,))
    res = cursor.fetchone()
    if res and res == "waiting":
        cursor.execute("UPDATE games SET status = 'timeout' WHERE game_id = ?", (game_id,))
        conn.commit()
        conn.close()
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ **Соперник не найден. Попробуйте еще раз!**", parse_mode="Markdown")
        except Exception: pass
    else:
        conn.close()
# Отслеживание подключения бота к бизнес-аккаунтам
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    user_id = connection.user.id
    conn_id = connection.id
    if connection.is_enabled:
        connection_owners[conn_id] = user_id
        add_user_if_not_exists(user_id, connection.user.first_name)
        try:
            await bot.send_message(chat_id=user_id, text="🚀 **ArtefaktSpyBot успешно подключен!**", parse_mode="Markdown")
        except Exception: pass
    else:
        try:
            connection_owners.pop(conn_id, None)
            business_msg_history.pop(conn_id, None)
        except Exception: pass


# Приветственное сообщение
@dp.message(CommandStart())
async def cmd_start(message: Message):
    add_user_if_not_exists(message.from_user.id, message.from_user.first_name)
    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot**.\n\n"
        "💰 **Экономика:**\n"
        "• `/bal` — Баланс кошелька\n"
        "• `/baltop` — Топ-10 игроков\n"
        "• `/bonus` — Ежедневный бонус\n\n"
        "🎮 **Игровой модуль:**\n"
        "• `/game` — Игра с реальным соперником (только в группах)\n"
        "• `/gamebot` — Одиночная игра против бота (работает везде)",
        parse_mode="Markdown"
    )


# Команды проверки баланса и топа лидирующих игроков
@dp.message(Command("bal"))
@dp.message(F.text.lower().in_(["/bal", ".bal", "баланс"]))
async def cmd_balance(message: Message):
    user_id = message.from_user.id
    add_user_if_not_exists(user_id, message.from_user.first_name)
    await message.answer(f"💰 Ваш текущий баланс: **{get_balance(user_id)} aSpy**", parse_mode="Markdown")

@dp.message(Command("baltop"))
@dp.message(F.text.lower().in_(["/baltop", ".baltop", "топ"]))
async def cmd_baltop(message: Message):
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, balance FROM users ORDER BY balance DESC LIMIT 10")
    leaders = cursor.fetchall()
    conn.close()
    if not leaders: return await message.answer("📋 Список лидеров пока пуст.")
    top_text = "🏆 **ТОП-10 ИГРОКОВ ПО БАЛАНСУ aSpy** 🏆\n\n"
    for index, leader in enumerate(leaders, start=1):
        user_id, username, balance = leader
        medal = "🥇" if index == 1 else ("🥈" if index == 2 else ("🥉" if index == 3 else f"*{index}.*"))
        top_text += f"{medal} {username or f'ID: {user_id}'} — **{balance} aSpy**\n"
    await message.answer(top_text, parse_mode="Markdown")


# --- МОДУЛЬ ЗАПУСКА ИГРЫ КРЕСТИКИ-НОЛИКИ ---
def get_game_keyboard(game_id: str, board: list, status: str) -> InlineKeyboardMarkup:
    buttons = []
    if status == "waiting":
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤝 Вступить в игру (за О)", callback_data=f"ttt_join:{game_id}")]])
    for i in range(3):
        row = []
        for j in range(3):
            index = i * 3 + j
            cell = board[index]
            text = " " if cell == "" else ("❌" if cell == "X" else "⭕️")
            cb_data = "ttt_noop" if (cell != "" or status == "ended") else f"ttt_hit:{game_id}:{index}"
            row.append(InlineKeyboardButton(text=text, callback_data=cb_data))
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# СТРОГОЕ ИСПРАВЛЕНИЕ: Полностью восстановлены все 8 выигрышных линий поля
def check_winner(b: list):
    lines = [, [3, 4, 5], [6, 7, 8],  # Горизонтальные линии, [1, 4, 7], [2, 5, 8],  # Вертикальные линии, [2, 4, 6]              # Диагональные линии
    ]
    for line in lines:
        if b[line[0]] != "" and b[line[0]] == b[line[1]] == b[line[2]]: 
            return b[line[0]]
    return "draw" if "" not in b else None


# Команда /game выдает ошибку в ЛС бота
@dp.message(Command("game"))
@dp.message(F.text.lower().in_(["/game", ".game"]))
async def start_game(message: Message):
    chat_id, user_id, user_name = message.chat.id, message.from_user.id, message.from_user.first_name
    add_user_if_not_exists(user_id, user_name)
    
    if message.chat.type == "private":
        await message.answer(
            "⚠️ **Ошибка:** кажется, вы находитесь одни в группе. Попробуйте команду /gamebot (игра с ботом):",
            parse_mode="Markdown"
        )
        return

    game_id = f"g_{chat_id}_{message.message_id}"
    board = [""] * 9
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?)", (game_id, chat_id, user_id, None, json.dumps(board), "X", "waiting"))
    conn.commit(); conn.close()
    
    sent_msg = await message.answer(f"🎮 Игрок **{user_name}** создал матч!\nУ соперника есть **1 минута**, чтобы зайти в игру.", reply_markup=get_game_keyboard(game_id, board, "waiting"), parse_mode="Markdown")
    asyncio.create_task(wait_for_opponent(chat_id=chat_id, message_id=sent_msg.message_id, game_id=game_id))


# Команда /gamebot (игра строго против ИИ бота)
@dp.message(Command("gamebot"))
@dp.message(F.text.lower().in_(["/gamebot", ".gamebot"]))
async def start_game_bot(message: Message):
    chat_id, user_id, user_name = message.chat.id, message.from_user.id, message.from_user.first_name
    add_user_if_not_exists(user_id, user_name)
    
    game_id = f"g_{chat_id}_{message.message_id}"
    board = [""] * 9
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?)", (game_id, chat_id, user_id, bot.id, json.dumps(board), "X", "playing"))
    conn.commit(); conn.close()
    
    await message.answer(f"🤖 **Матч против ArtefaktBot запущен!** Ваш ход (❌):", reply_markup=get_game_keyboard(game_id, board, "playing"), parse_mode="Markdown")
@dp.callback_query(F.data.startswith("ttt_join:"))
async def callback_ttt_join(callback: CallbackQuery):
    game_id = callback.data.split(":")
    user_id, user_name = callback.from_user.id, callback.from_user.first_name
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT player_x, status, board FROM games WHERE game_id = ?", (game_id,))
    res = cursor.fetchone()
    if not res: conn.close(); return await callback.answer("Игра не найдена.", show_alert=True)
    player_x, status, board_json = res
    if status == "timeout": conn.close(); return await callback.answer("Время ожидания соперника истекло!", show_alert=True)
    if user_id == player_x: conn.close(); return await callback.answer("Нельзя играть против самого себя!", show_alert=True)
    add_user_if_not_exists(user_id, user_name)
    board = json.loads(board_json)
    cursor.execute("UPDATE games SET player_o = ?, status = 'playing' WHERE game_id = ?", (user_id, game_id))
    conn.commit(); conn.close()
    await callback.answer()
    await callback.message.edit_text(f"🎮 Игра началась!\n❌ Ходит первый игрок. ⭕️ Ожидает **{user_name}**.", reply_markup=get_game_keyboard(game_id, board, "playing"))

@dp.callback_query(F.data.startswith("ttt_hit:"))
async def callback_ttt_hit(callback: CallbackQuery):
    _, game_id, cell_index = callback.data.split(":")
    cell_index, user_id = int(cell_index), callback.from_user.id
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT player_x, player_o, board, turn, status FROM games WHERE game_id = ?", (game_id,))
    game = cursor.fetchone()
    if not game or game != "playing": conn.close(); return await callback.answer("Игра завершена.")
    player_x, player_o, board_json, turn, status = game
    board = json.loads(board_json)
    if (turn == "X" and user_id != player_x) or (turn == "O" and user_id != player_o): conn.close(); return await callback.answer("Сейчас не ваш ход!", show_alert=True)
    board[cell_index] = turn
    next_turn = "O" if turn == "X" else "X"
    win_state = check_winner(board)
    if win_state:
        cursor.execute("UPDATE games SET board = ?, status = 'ended' WHERE game_id = ?", (json.dumps(board), game_id))
        conn.commit(); conn.close()
        if win_state == "draw":
            if player_x: add_balance(player_x, 30)
            if player_o and player_o != bot.id: add_balance(player_o, 30)
            msg = "🤝 **Ничья!** Оба игрока получают по **+30 aSpy**."
        else:
            winner = player_x if win_state == "X" else player_o
            loser = player_o if win_state == "X" else player_x
            if winner: add_balance(winner, 100)
            if loser and loser != bot.id: add_balance(loser, -100)
            msg = f"🎉 Победил знак {win_state}! Выигрыш: **+100 aSpy**, проигрыш: **-100 aSpy**."
        return await callback.message.edit_text(f"🏁 **Игра завершена!**\n\n{msg}", reply_markup=get_game_keyboard(game_id, board, "ended"), parse_mode="Markdown")
    if player_o == bot.id and next_turn == "O":
        empty_cells = [i for i, cell in enumerate(board) if cell == ""]
        board[random.choice(empty_cells)] = "O"
        win_state = check_winner(board)
        if win_state:
            cursor.execute("UPDATE games SET board = ?, status = 'ended' WHERE game_id = ?", (json.dumps(board), game_id))
            conn.commit(); conn.close()
            msg = "🤝 **Ничья с Ботом!** (+30 aSpy)" if win_state == "draw" else "🤖 **Бот выиграл!** Вы потеряли **-100 aSpy**."
            if win_state == "draw": add_balance(player_x, 30)
            else: add_balance(player_x, -100)
            return await callback.message.edit_text(f"🏁 **Игра завершена!**\n\n{msg}", reply_markup=get_game_keyboard(game_id, board, "ended"), parse_mode="Markdown")
        next_turn = "X"
    cursor.execute("UPDATE games SET board = ?, turn = ? WHERE game_id = ?", (json.dumps(board), next_turn, game_id))
    conn.commit(); conn.close(); await callback.answer()
    await callback.message.edit_text(f"🎮 Игра продолжается. Ход за: **{next_turn}**", reply_markup=get_game_keyboard(game_id, board, "playing"))

@dp.callback_query(F.data == "ttt_noop")
async def ttt_noop(c: CallbackQuery): await c.answer()

@dp.message(Command("bonus"))
@dp.message(F.text.lower().in_(["/bonus", ".bonus", "бонус"]))
async def cmd_bonus(m: Message):
    uid = m.from_user.id
    add_user_if_not_exists(uid, m.from_user.first_name)
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT last_bonus FROM users WHERE user_id = ?", (uid,))
    res = cursor.fetchone()
    now = datetime.now()
    if res and res and now < datetime.strptime(res, "%Y-%m-%d %H:%M:%S") + timedelta(days=1):
        conn.close()
        return await m.answer("⏳ Бонус доступен раз в 24 часа.", parse_mode="Markdown")
    prize = random.randint(10, 50)
    cursor.execute("UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?", (prize, now.strftime("%Y-%m-%d %H:%M:%S"), uid))
    conn.commit(); conn.close()
    await m.answer(f"🎁 Получено: **+{prize} aSpy**!", parse_mode="Markdown")

@dp.business_message()
async def handle_business_message(m: Message):
    conn_id = m.business_connection_id
    if conn_id not in connection_owners:
        try:
            inf = await bot.get_business_connection(business_connection_id=conn_id)
            connection_owners[conn_id] = inf.user.id
        except Exception: pass
    if conn_id not in business_msg_history: business_msg_history[conn_id] = {}
    if m.text: business_msg_history[conn_id][m.message_id] = m.text
    elif m.caption: business_msg_history[conn_id][m.message_id] = m.caption
    oid = connection_owners.get(conn_id)
    if oid and m.from_user.id != oid:
        cap = f"⚡️ **Мгновенный перехват медиа!**\n👤 **От**: {m.from_user.full_name}"
        if m.photo: await bot.send_photo(chat_id=oid, photo=m.photo[-1].file_id, caption=cap, parse_mode="Markdown")
        elif m.video: await bot.send_video(chat_id=oid, video=m.video.file_id, caption=cap, parse_mode="Markdown")

@dp.edited_business_message()
async def handle_edited_business_message(m: Message):
    conn_id = m.business_connection_id
    hist = business_msg_history.get(conn_id, {})
    if m.message_id in hist and hist[m.message_id] != m.text:
        oid = connection_owners.get(conn_id)
        if oid:
            add_balance(oid, 5)
            await bot.send_message(chat_id=oid, text=f"🕵️‍♂️ **Изменено сообщение от {m.from_user.full_name}!** (+5 aSpy)\n\n**Было:** {hist[m.message_id]}\n**Стало:** {m.text}", parse_mode="Markdown")
            hist[m.message_id] = m.text

@dp.deleted_business_messages()
async def handle_deleted_business_messages(dm: BusinessMessagesDeleted):
    hist = business_msg_history.get(dm.business_connection_id, {})
    oid = connection_owners.get(dm.business_connection_id)
    if oid:
        for mid in dm.message_ids:
            if mid in hist:
                add_balance(oid, 5)
                await bot.send_message(chat_id=oid, text=f"🗑 **Удалено сообщение!** (+5 aSpy)\n\n**Было:** {hist[mid]}", parse_mode="Markdown")
                hist.pop(mid, None)

async def main():
    print("ArtefaktSpyBot успешно запущен со всеми исправлениями разделения игр!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
