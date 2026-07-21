import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted, BusinessConnection
from aiogram.filters import CommandStart

# Токен вашего @ArtefaktSpyBot из @BotFather
BOT_TOKEN = "8689486048:AAFkgdmV4ZTtL8gAkfmEjWeXkrAufMM42kI"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Внутреннее хранилище в оперативной памяти сервера (RAM)
connection_owners = {}     # Карта соответствий { business_connection_id: user_id }
business_msg_history = {}  # Кэш истории { conn_id: { message_id: text } }
muted_users = {}           # Списки ограничений { (chat_id/conn_id, target_id): True }
afk_status = {}            # Статусы отсутствия { user_id: reason_text }


# 1. Отслеживание подключения и отключения бота от бизнес-аккаунта
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    user_id = connection.user.id
    conn_id = connection.id
    
    if connection.is_enabled:
        connection_owners[conn_id] = user_id
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🚀 **Бот подключен!**\n\nТеперь я успешно интегрирован в ваши чаты. Я буду отслеживать удаления, изменения сообщений и выполнять команды.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Ошибка уведомления о подключении: {e}")
    else:
        try:
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ **Бот отключен**\n\nВы отключили автоматизацию чатов для этого аккаунта.",
                parse_mode="Markdown"
            )
            connection_owners.pop(conn_id, None)
            business_msg_history.pop(conn_id, None)
        except Exception as e:
            logging.error(f"Ошибка уведомления об отключении: {e}")


# 2. Приветственное сообщение при старте бота в ЛС
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot** в режиме Автоматизации.\n\n"
        "📜 **Доступные команды в ваших бизнес-чатах:**\n"
        "• Ответом на сообщение человека:\n"
        "  ` .mute ` — включить автоудаление его реплик.\n"
        "  ` .unmute ` — снять блокировку реплик.\n"
        "  ` .note ` — сохранить это сообщение в Избранное.\n\n"
        "• Обычным текстом в любом месте чата:\n"
        "  ` .afk [причина] ` — автоответчик.\n"
        "  ` .unafk ` — выключить автоответчик.\n"
        "  ` .spam [текст] [кол-во] ` — запустить массовую отправку.\n",
        parse_mode="Markdown"
    )


# 3. Базовый перехватчик сообщений (Слежка, Мут и логика AFK)
@dp.business_message()
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

    # Фильтрация и уничтожение реплик замученного пользователя
    if muted_users.get((chat_id, user_id)) or muted_users.get((conn_id, user_id)):
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            return
        except Exception:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
                return
            except Exception as e:
                logging.error(f"Не удалось удалить сообщение замученного: {e}")

    # Исполнение автоответа в режиме AFK
    owner_id = connection_owners.get(conn_id)
    if owner_id and user_id != owner_id and owner_id in afk_status:
        if message.text and not message.from_user.is_bot:
            reason = afk_status[owner_id]
            afk_reply = f"🤖 [Автоответ] Сейчас я занят. Причина: *{reason}*"
            await bot.send_message(chat_id=chat_id, text=afk_reply, business_connection_id=conn_id, parse_mode="Markdown")

    if conn_id not in business_msg_history:
        business_msg_history[conn_id] = {}
    if message.text:
        business_msg_history[conn_id][message.message_id] = message.text
# Универсальный хэндлер для обычных сообщений и бизнес-сообщений
async def process_dot_commands(message: Message, is_business: bool = False):
    chat_id = message.chat.id
    conn_id = message.business_connection_id if is_business else None
    
    raw_text = message.text.strip()
    text_parts = raw_text.split(maxsplit=1)
    if not text_parts:
        return
        
    command = text_parts[0].lower()
    args = text_parts[1] if len(text_parts) > 1 else ""

    if is_business:
        owner_id = connection_owners.get(conn_id)
    else:
        owner_id = message.from_user.id

    if not owner_id:
        return

    async def delete_cmd():
        try:
            if is_business:
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            else:
                await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
        except Exception:
            pass

    async def send_status(text: str):
        try:
            if is_business:
                await bot.send_message(chat_id=chat_id, business_connection_id=conn_id, text=text, parse_mode="Markdown")
            else:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception:
            pass

    # --- НОВАЯ КОМАНДА: .spam [текст] [количество] ---
    if command == ".spam":
        await delete_cmd()
        if not args:
            await send_status("⚠️ **Использование:** `.spam [текст] [количество]`")
            return
            
        # Разбиваем аргументы с конца, чтобы выделить число
        spam_parts = args.rsplit(maxsplit=1)
        if len(spam_parts) < 2 or not spam_parts[1].isdigit():
            await send_status("⚠️ **Ошибка:** Укажите количество сообщений числом в конце команды.")
            return
            
        spam_text = spam_parts[0]
        count = int(spam_parts[1])
        
        # Ограничение безопасности против зависания сервера
        if count > 100:
            count = 100
            await send_status("🛡 **Защита:** Установлен лимит максимум 100 сообщений за один запуск.")

        for _ in range(count):
            try:
                if is_business:
                    await bot.send_message(chat_id=chat_id, business_connection_id=conn_id, text=spam_text)
                else:
                    await bot.send_message(chat_id=chat_id, text=spam_text)
                await asyncio.sleep(0.2)  # Задержка против Flood Wait бана
            except Exception as e:
                logging.error(f"Ошибка отправки спам-сообщения: {e}")
                break
        return

    # --- КОМАНДЫ С REPLY ---
    if message.reply_to_message:
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.full_name

        if target_user_id == owner_id:
            await delete_cmd()
            await send_status("⚠️ **Ошибка:** Вы не можете замутить самого себя.")
            return

        if command == ".mute":
            key = (chat_id, target_user_id) if not is_business else (conn_id, target_user_id)
            muted_users[key] = True
            await delete_cmd()
            await send_status(f"🔇 **Пользователь {target_username} замучен**")
            return

        elif command == ".unmute":
            key = (chat_id, target_user_id) if not is_business else (conn_id, target_user_id)
            muted_users.pop(key, None)
            await delete_cmd()
            await send_status(f"🔊 **Мут с пользователя {target_username} снят**")
            return

        elif command == ".note":
            note_content = message.reply_to_message.text or "[Медиафайл]"
            try:
                await delete_cmd()
                await bot.send_message(chat_id=owner_id, text=f"📌 **Новая заметка из чата `{chat_id}`**:\n\n{note_content}", parse_mode="Markdown")
            except Exception:
                pass
            return

    # --- САМОСТОЯТЕЛЬНЫЕ КОМАНДЫ ---
    if command == ".afk":
        reason = args if args else "Отсутствую"
        afk_status[owner_id] = reason
        await delete_cmd()
        await send_status(f"💤 **Режим \"Не беспокоить\" активирован**\nПричина: _{reason}_")

    elif command == ".unafk":
        afk_status.pop(owner_id, None)
        await delete_cmd()
        await send_status("🟢 **Режим \"Не беспокоить\" деактивирован**")


# Регистрация роутеров
@dp.business_message(F.text.startswith("."))
async def handle_business_commands(message: Message):
    await process_dot_commands(message, is_business=True)

@dp.message(F.text.startswith("."))
async def handle_regular_commands(message: Message):
    await process_dot_commands(message, is_business=False)


# 5. Детекторы изменений
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
            if not owner_id: return
            try:
                log_text = f"🕵️‍♂️ **Изменено сообщение от {message.from_user.full_name}!**\n🌐 **Чат**: `{message.chat.full_name or message.chat.id}`\n\n**Было:** {old_text}\n**Стало:** {new_text}"
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception: pass


# 6. Детекторы удалений
@dp.deleted_business_messages()
async def handle_deleted_business_messages(deleted_messages: BusinessMessagesDeleted):
    conn_id = deleted_messages.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})
    owner_id = connection_owners.get(conn_id)
    if not owner_id: return
    for msg_id in deleted_messages.message_ids:
        if msg_id in user_msgs:
            old_text = user_msgs[msg_id]
            try:
                log_text = f"🗑 **Удалено сообщение в чате!**\n🌐 **ID чата**: `{deleted_messages.chat.id}`\n\n**Было:** {old_text}"
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception: pass
            del user_msgs[msg_id]

async def main():
    print("ArtefaktSpyBot запущен со всеми исправлениями!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
