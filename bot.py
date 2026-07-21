import asyncio
import json
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted, BusinessConnection
from aiogram.filters import CommandStart

# ⚠️ Токен лучше хранить не в коде, а в переменной окружения.
# Если этот токен где-то "засветился" — перевыпусти его через @BotFather (/revoke).
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_СВОЙ_ТОКЕН_СЮДА")

STATE_FILE = "bot_state.json"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Хранилища в памяти ---
connection_owners: dict[str, int] = {}      # { business_connection_id: user_id }
business_msg_history: dict[str, dict] = {}  # { conn_id: { message_id: text } }
muted_users: dict[str, int] = {}            # { "conn_id:chat_id": target_user_id }  (строковый ключ для JSON)
afk_status: dict[int, str] = {}             # { user_id: reason_text }


# --- Персистентность: сохраняем муты и afk между перезапусками ---
def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "muted_users": muted_users,
                    "afk_status": {str(k): v for k, v in afk_status.items()},
                },
                f,
                ensure_ascii=False,
            )
    except Exception as e:
        logging.error(f"Не удалось сохранить состояние: {e}")


def load_state():
    global muted_users, afk_status
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        muted_users = data.get("muted_users", {})
        afk_status = {int(k): v for k, v in data.get("afk_status", {}).items()}
    except Exception as e:
        logging.error(f"Не удалось загрузить состояние: {e}")


def mute_key(conn_id: str, chat_id: int) -> str:
    return f"{conn_id}:{chat_id}"


# 1. Подключение / отключение автоматизации чатов
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    user_id = connection.user.id
    conn_id = connection.id

    if connection.is_enabled:
        connection_owners[conn_id] = user_id
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🚀 **Бот подключен!**\n\nТеперь я слежу за вашими чатами и выполняю команды.",
                parse_mode="Markdown",
            )
            logging.info(f"Пользователь {user_id} подключил соединение {conn_id}")
        except Exception as e:
            logging.error(f"Ошибка уведомления о подключении: {e}")
    else:
        try:
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ **Бот отключен**\n\nВы отключили автоматизацию чатов для этого аккаунта.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logging.error(f"Ошибка уведомления об отключении: {e}")

        connection_owners.pop(conn_id, None)
        business_msg_history.pop(conn_id, None)
        keys_to_remove = [k for k in muted_users if k.startswith(f"{conn_id}:")]
        for key in keys_to_remove:
            muted_users.pop(key, None)
        save_state()


# 2. /start
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot** в режиме Автоматизации.\n\n"
        "📜 **Команды ответом на сообщение человека:**\n"
        "  ` .mute ` — включить автоудаление его сообщений.\n"
        "  ` .unmute ` — снять ограничение.\n"
        "  ` .note ` — сохранить сообщение в избранное.\n\n"
        "📜 **Команды в любом месте чата:**\n"
        "  ` .afk [причина] ` — автоответчик.\n"
        "  ` .unafk ` — выключить автоответчик.\n"
        "  ` .read ` — принудительно прочитать чат.\n",
        parse_mode="Markdown",
    )


# 3. Команды (.mute/.unmute/.note/.afk/.unafk/.read)
# ВАЖНО: этот хендлер зарегистрирован РАНЬШЕ общего перехватчика (п.4),
# и у него более специфичный фильтр — поэтому он должен успеть обработать
# сообщение первым. Именно порядок регистрации + специфичность фильтра
# чинят баг, из-за которого команды раньше никогда не выполнялись.
@dp.business_message(F.text.startswith("."))
async def handle_business_commands(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id

    text_parts = message.text.strip().split(maxsplit=1)
    if not text_parts:
        return

    command = text_parts[0].lower()
    args = text_parts[1] if len(text_parts) > 1 else ""

    owner_id = connection_owners.get(conn_id)
    if not owner_id:
        try:
            conn_info = await bot.get_business_connection(business_connection_id=conn_id)
            owner_id = conn_info.user.id
            connection_owners[conn_id] = owner_id
        except Exception as e:
            logging.error(f"Не удалось восстановить сессию: {e}")
            return

    # --- Команды, требующие ответа на сообщение собеседника ---
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.full_name

        if command == ".mute":
            muted_users[mute_key(conn_id, chat_id)] = target_user_id
            save_state()
            try:
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
                await bot.send_message(
                    chat_id=owner_id,
                    text=f"🔇 **{target_username} замучен в чате {chat_id}**\nНовые сообщения будут удаляться.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logging.error(f"Сбой .mute: {e}")
            return

        elif command == ".unmute":
            muted_users.pop(mute_key(conn_id, chat_id), None)
            save_state()
            try:
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
                await bot.send_message(
                    chat_id=owner_id,
                    text=f"🔊 **Мут с {target_username} в чате {chat_id} снят**",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logging.error(f"Сбой .unmute: {e}")
            return

        elif command == ".note":
            note_content = message.reply_to_message.text or message.reply_to_message.caption or "[Медиафайл]"
            try:
                await bot.send_message(
                    chat_id=owner_id,
                    text=f"📌 **Заметка из чата `{chat_id}`**:\n\n{note_content}",
                    parse_mode="Markdown",
                )
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            except Exception as e:
                logging.error(f"Сбой .note: {e}")
            return

    # --- Самостоятельные команды ---
    if command == ".afk":
        reason = args if args else "Отсутствую"
        afk_status[owner_id] = reason
        save_state()
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            await bot.send_message(
                chat_id=owner_id,
                text=f"💤 **Режим «Не беспокоить» включён**\nПричина: _{reason}_",
                parse_mode="Markdown",
            )
        except Exception as e:
            logging.error(f"Сбой .afk: {e}")

    elif command == ".unafk":
        afk_status.pop(owner_id, None)
        save_state()
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            await bot.send_message(
                chat_id=owner_id,
                text="🟢 **Режим «Не беспокоить» выключен**",
                parse_mode="Markdown",
            )
        except Exception as e:
            logging.error(f"Сбой .unafk: {e}")

    elif command == ".read":
        try:
            await bot.read_business_message(business_connection_id=conn_id, chat_id=chat_id, message_id=message.message_id)
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        except Exception as e:
            logging.error(f"Не удалось прочесть чат: {e}")


# 4. Общий перехватчик: мут-фильтрация, AFK-автоответ, архивация истории.
# Фильтр ~F.text.startswith(".") исключает команды — их уже обработал
# хендлер выше, повторно сюда они попадать не должны.
@dp.business_message(~F.text.startswith("."))
async def handle_business_message(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    user_id = message.from_user.id

    if conn_id not in connection_owners:
        try:
            conn_info = await bot.get_business_connection(business_connection_id=conn_id)
            connection_owners[conn_id] = conn_info.user.id
        except Exception as e:
            logging.error(f"Не удалось восстановить сессию: {e}")

    # Мгновенное удаление сообщений замученного пользователя
    if muted_users.get(mute_key(conn_id, chat_id)) == user_id:
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        except Exception as e:
            logging.error(f"Ошибка автоудаления в муте: {e}")
        return

    # Автоответ AFK
    owner_id = connection_owners.get(conn_id)
    if owner_id and user_id != owner_id and owner_id in afk_status:
        if message.text and not message.from_user.is_bot:
            reason = afk_status[owner_id]
            afk_reply = f"🤖 [Автоответ] Сейчас я занят. Причина: *{reason}*"
            await bot.send_message(chat_id=chat_id, text=afk_reply, business_connection_id=conn_id, parse_mode="Markdown")

    # Архивация для детектора изменений/удалений
    if conn_id not in business_msg_history:
        business_msg_history[conn_id] = {}
    if message.text:
        business_msg_history[conn_id][message.message_id] = message.text


# 5. Детектор редактирования сообщений
@dp.edited_business_message()
async def handle_edited_business_message(message: Message):
    conn_id = message.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})

    if message.message_id in user_msgs:
        old_text = user_msgs[message.message_id]
        new_text = message.text

        if old_text != new_text:
            user_msgs[message.message_id] = new_text
            owner_id = connection_owners.get(conn_id)
            if not owner_id:
                return
            try:
                log_text = (
                    f"🕵️ **Изменено сообщение от {message.from_user.full_name}!**\n"
                    f"🌐 **Чат**: `{message.chat.full_name or message.chat.id}`\n\n"
                    f"**Было:** {old_text}\n"
                    f"**Стало:** {new_text}"
                )
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка лога изменений: {e}")


# 6. Детектор удаления сообщений
@dp.deleted_business_messages()
async def handle_deleted_business_messages(deleted_messages: BusinessMessagesDeleted):
    conn_id = deleted_messages.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})

    owner_id = connection_owners.get(conn_id)
    if not owner_id:
        return

    for msg_id in deleted_messages.message_ids:
        if msg_id in user_msgs:
            old_text = user_msgs[msg_id]
            try:
                log_text = (
                    f"🗑 **Удалено сообщение в чате!**\n"
                    f"🌐 **ID чата**: `{deleted_messages.chat.id}`\n\n"
                    f"**Было:** {old_text}"
                )
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка лога удаления: {e}")
            del user_msgs[msg_id]


async def main():
    load_state()
    print("ArtefaktSpyBot запущен (исправленный порядок обработчиков)!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
