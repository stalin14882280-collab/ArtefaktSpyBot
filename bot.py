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


# --- МОДЕРНИЗАЦИЯ БАЗЫ ДАННЫХ ДЛЯ ЛОГОВ И АДМИНКИ ---
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER,
            receiver_id INTEGER,
            amount INTEGER,
            date TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_x_name TEXT,
            player_o_name TEXT,
            result TEXT,
            date TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def add_user_if_not_exists(user_id: int, username: str = "Игрок"):
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if not res:
        cursor.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    else:
        if username != "Игрок":
            cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
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

def get_username(user_id: int) -> str:
    if user_id == bot.id:
        return "ArtefaktBot"
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res if res else f"ID: {user_id}"


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


@dp.message(CommandStart())
async def cmd_start(message: Message):
    add_user_if_not_exists(message.from_user.id, message.from_user.first_name)
    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot**.\n\n"
        "🕵️‍♂️ **Шпионский модуль (работает в фоне):**\n"
        "• Перехватываю и сохраняю удаленные текстовые сообщения\n"
        "• Фиксирую любые изменения текста в формате (Было / Стало)\n"
        "• Мгновенно дублирую все входящие фото и видеоролики из ваших чатов\n\n"
        "💰 **Экономика:**\n"
        "• `/bal` — Баланс кошелька\n"
        "• `/baltop` — Топ-10 игроков\n"
        "• `/pay [ID] [сумма]` — Перевод валюты\n"
        "• `/bonus` — Ежедневный бонус\n\n"
        "🎮 **Игровой модуль:**\n"
        "• `/game` — Игра с реальным соперником\n"
        "• `/gamebot` — Одиночная игра против бота",
        parse_mode="Markdown"
    )


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
        top_text += f"{medal} {username} `(ID: {user_id})` — **{balance} aSpy**\n"
    await message.answer(top_text, parse_mode="Markdown")


@dp.message(Command("pay"))
async def cmd_pay(message: Message):
    user_id = message.from_user.id
    parts = message.text.split()
    if len(parts) < 3:
        return await message.answer("⚠️ **Использование:** `/pay [ID пользователя] [сумма]`", parse_mode="Markdown")
    target_id_str, amount_str = parts, parts
    if not target_id_str.isdigit() or not amount_str.isdigit():
        return await message.answer("❌ **Ошибка:** ID и сумма должны быть числами.")
    target_id, amount = int(target_id_str), int(amount_str)
    if amount <= 0:
        return await message.answer("❌ **Ошибка:** Сумма перевода должна быть больше нуля.")
    if user_id == target_id:
        return await message.answer("❌ **Ошибка:** Нельзя переводить аспаи самому себе.")
    if get_balance(user_id) < amount:
        return await message.answer("❌ **Ошибка:** Недостаточно аспаев для перевода.")
    add_balance(user_id, -amount)
    add_balance(target_id, amount)
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO transfers (sender_id, receiver_id, amount, date) VALUES (?, ?, ?, ?)",
                   (user_id, target_id, amount, datetime.now().strftime("%d.%m %H:%M")))
    conn.commit(); conn.close()
    await message.answer(f"✅ Успешный перевод! Отправлено **{amount} aSpy** пользователю с ID `{target_id}`.", parse_mode="Markdown")


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or parts != "060510":
        return await message.answer("❌ **Ошибка:** Неверный пароль администратора.")
    try: await message.delete()
    except Exception: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 История платежей", callback_data=f"adm_logs_pay:{message.from_user.id}")],
        [InlineKeyboardButton(text="🎮 История игр", callback_data=f"adm_logs_games:{message.from_user.id}")],
        [InlineKeyboardButton(text="💰 Начислить себе +5000 aSpy", callback_data=f"adm_give_money:{message.from_user.id}")]
    ])
    await message.answer("👑 **Панель главного администратора ArtefaktSpy**\n\nУправляйте логами сервера и экономикой с помощью кнопок ниже:", reply_markup=kb, parse_mode="Markdown")
@dp.callback_query(F.data.startswith("adm_"))
async def callback_admin_panel(callback: CallbackQuery):
    data_parts = callback.data.split(":")
    action, allowed_admin_id = data_parts, int(data_parts)
    if callback.from_user.id != allowed_admin_id:
        return await callback.answer("⛔️ Доступ запрещен!", show_alert=True)
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    if action == "adm_logs_pay":
        cursor.execute("SELECT sender_id, receiver_id, amount, date FROM transfers ORDER BY id DESC LIMIT 5")
        logs = cursor.fetchall(); conn.close()
        if not logs: return await callback.answer("История переводов пуста.", show_alert=True)
        text = "📜 **ПОСЛЕДНИЕ 5 ПЛАТЕЖЕЙ НА СЕРВЕРЕ**\n\n"
        for log in logs: text += f"📅 `[{log}]` ID `{log}` ➡️ ID `{log}`: **{log} aSpy**\n"
        await callback.message.answer(text, parse_mode="Markdown"); await callback.answer()
    elif action == "adm_logs_games":
        cursor.execute("SELECT player_x_name, player_o_name, result, date FROM game_logs ORDER BY id DESC LIMIT 5")
        logs = cursor.fetchall(); conn.close()
        if not logs: return await callback.answer("История игр пуста.", show_alert=True)
        text = "🎮 **ПОСЛЕДНИЕ 5 ИГР НА СЕРВЕРЕ**\n\n"
        for log in logs: text += f"📅 `[{log}]` **{log}** 🆚 **{log}** ➡️ Итог: *{log}*\n"
        await callback.message.answer(text, parse_mode="Markdown"); await callback.answer()
    elif action == "adm_give_money":
        conn.close(); add_balance(allowed_admin_id, 5000)
        await callback.answer("💰 Баланс успешно пополнен на +5000 aSpy!", show_alert=True)


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

def check_winner(b: list):
    lines = [,,,,,,,]
    for line in lines:
        if b[line] != "" and b[line] == b[line] == b[line]: return b[line]
    return "draw" if "" not in b else None


@dp.message(Command("game"))
@dp.message(F.text.lower().in_(["/game", ".game"]))
async def start_game(message: Message):
    chat_id, user_id, user_name = message.chat.id, message.from_user.id, message.from_user.first_name
    add_user_if_not_exists(user_id, user_name)
    if message.chat.type == "private":
        return await message.answer("⚠️ Кажется, вы находитесь одни в группе. Попробуйте /gamebot", parse_mode="Markdown")
    game_id = f"g_{chat_id}_{message.message_id}"
    board = [""] * 9
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?)", (game_id, chat_id, user_id, None, json.dumps(board), "X", "waiting"))
    conn.commit(); conn.close()
    sent_msg = await message.answer(f"🎮 Игрок **{user_name}** создал матч!\nОжидание соперника 1 минута...", reply_markup=get_game_keyboard(game_id, board, "waiting"), parse_mode="Markdown")
    asyncio.create_task(wait_for_opponent(chat_id=chat_id, message_id=sent_msg.message_id, game_id=game_id))


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
    await message.answer(f"🤖 **Матч против бота запущен!** Ваш ход (❌):", reply_markup=get_game_keyboard(game_id, board, "playing"), parse_mode="Markdown")
@dp.callback_query(F.data.startswith("ttt_join:"))
async def callback_ttt_join(callback: CallbackQuery):
    game_id = callback.data.split(":")
    user_id, user_name = callback.from_user.id, callback.from_user.first_name
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT player_x, status, board FROM games WHERE game_id = ?", (game_id,))
    res = cursor.fetchone()
    if not res: conn.close(); return await callback.answer("Игра не найдена.")
    player_x, status, board_json = res
    if str(status) == "timeout": conn.close(); return await callback.answer("Время истекло!", show_alert=True)
    if user_id == player_x: conn.close(); return await callback.answer("Нельзя играть с собой!", show_alert=True)
    add_user_if_not_exists(user_id, user_name)
    board = json.loads(board_json)
    cursor.execute("UPDATE games SET player_o = ?, status = 'playing' WHERE game_id = ?", (user_id, game_id))
    conn.commit(); conn.close(); await callback.answer()
    await callback.message.edit_text(f"🎮 Игра началась! Ходит ❌.", reply_markup=get_game_keyboard(game_id, board, "playing"))


@dp.callback_query(F.data.startswith("ttt_hit:"))
async def callback_ttt_hit(callback: CallbackQuery):
    _, game_id, cell_index = callback.data.split(":")
    cell_index, user_id = int(cell_index), callback.from_user.id
    conn = sqlite3.connect("artefakt_spy.db")
    cursor = conn.cursor()
    cursor.execute("SELECT player_x, player_o, board, turn, status FROM games WHERE game_id = ?", (game_id,))
    game = cursor.fetchone()
    if not game: conn.close(); return await callback.answer("Игра не найдена.")
    player_x, player_o, board_json, turn, status = game
    if str(status) != "playing": conn.close(); return await callback.answer("Матч завершен!")
    board = json.loads(board_json)
    if (turn == "X" and user_id != player_x) or (turn == "O" and user_id != player_o): conn.close(); return await callback.answer("Не ваш ход!", show_alert=True)
    board[cell_index] = turn
    next_turn = "O" if turn == "X" else "X"
    win_state = check_winner(board)
    if win_state:
        cursor.execute("UPDATE games SET board = ?, status = 'ended' WHERE game_id = ?", (json.dumps(board), game_id))
        p_x_name, p_o_name = get_username(player_x), get_username(player_o)
        res_text = "Ничья" if win_state == "draw" else f"Победил {win_state}"
        cursor.execute("INSERT INTO game_logs (player_x_name, player_o_name, result, date) VALUES (?, ?, ?, ?)", (p_x_name, p_o_name, res_text, datetime.now().strftime("%d.%m %H:%M")))
        conn.commit(); conn.close()
        if win_state == "draw":
            if player_x: add_balance(player_x, 30)
            if player_o and player_o != bot.id: add_balance(player_o, 30)
            msg = "🤝 **Ничья!** Оба получили по **+30 aSpy**."
        else:
            winner = player_x if win_state == "X" else player_o
            loser = player_o if win_state == "X" else player_x
            if winner: add_balance(winner, 100)
            if loser and loser != bot.id: add_balance(loser, -100)
            msg = f"🎉 Победил {win_state}! Награда выдана."
        return await callback.message.edit_text(f"🏁 **Игра завершена!**\n\n{msg}", reply_markup=get_game_keyboard(game_id, board, "ended"), parse_mode="Markdown")
    if player_o == bot.id and next_turn == "O":
        empty_cells = [i for i, cell in enumerate(board) if cell == ""]
        if empty_cells:
            board[random.choice(empty_cells)] = "O"
            win_state = check_winner(board)
            if win_state:
                cursor.execute("UPDATE games SET board = ?, status = 'ended' WHERE game_id = ?", (json.dumps(board), game_id))
                p_x_name = get_username(player_x)
                res_text = "Ничья" if win_state == "draw" else "Выиграл Бот"
                cursor.execute("INSERT INTO game_logs (player_x_name, player_o_name, result, date) VALUES (?, ?, ?, ?)", (p_x_name, "ArtefaktBot", res_text, datetime.now().strftime("%d.%m %H:%M")))
                conn.commit(); conn.close()
                msg = "🤝 **Ничья с Ботом!** (+30)" if win_state == "draw" else "🤖 **Бот выиграл!** (-100)"
                if win_state == "draw": add_balance(player_x, 30)
                else: add_balance(player_x, -100)
                return await callback.message.edit_text(f"🏁 **Игра завершена!**\n\n{msg}", reply_markup=get_game_keyboard(game_id, board, "ended"), parse_mode="Markdown")
        next_turn = "X"
    cursor.execute("UPDATE games SET board = ?, turn = ? WHERE game_id = ?", (json.dumps(board), next_turn, game_id))
    conn.commit(); conn.close(); await callback.answer()
    await callback.message.edit_text(f"🎮 Ход за значком: **{next_turn}**", reply_markup=get_game_keyboard(game_id, board, "playing"))


@dp.callback_query(F.data == "ttt_noop")
async def ttt_noop(c: CallbackQuery): await c.answer("Ячейка занята!")


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
    if res and res:
        if now < datetime.strptime(res, "%Y-%m-%d %H:%M:%S") + timedelta(days=1):
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
    print("ArtefaktSpyBot запущен со всеми модулями логирования!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
