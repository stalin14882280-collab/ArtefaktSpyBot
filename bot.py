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
muted_users = {}           # Списки ограничений { (conn_id, chat_id): target_user_id }
afk_status = {}            # Статусы отсутствия { user_id: reason_text }


# 1. Мониторинг интеграции: Подключение и отключение автоматизации чатов
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    user_id = connection.user.id
    conn_id = connection.id
    
    if connection.is_enabled:
        connection_owners[conn_id] = user_id
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🚀 **Бот подключен!**\n\n"
                     "Теперь я успешно интегрирован в ваши чаты. Я буду отслеживать "
                     "удаления, изменения сообщений и выполнять команды вроде `.mute` и `.afk`.",
                parse_mode="Markdown"
            )
            logging.info(f"Пользователь {user_id} подключил соединение {conn_id}")
        except Exception as e:
            logging.error(f"Ошибка уведомления о подключении: {e}")
            
    else:
        try:
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ **Бот отключен**\n\n"
                     "Вы отключили автоматизацию чатов для этого аккаунта. Я больше не "
                     "вижу изменения в диалогах.",
                parse_mode="Markdown"
            )
            logging.info(f"Пользователь {user_id} отключил соединение {conn_id}")
            
            # Принудительная очистка кэша для оптимизации памяти RAM
            connection_owners.pop(conn_id, None)
            business_msg_history.pop(conn_id, None)
            keys_to_remove = [k for k in muted_users.keys() if k == conn_id]
            for key in keys_to_remove:
                muted_users.pop(key, None)
                
        except Exception as e:
            logging.error(f"Ошибка уведомления об отключении: {e}")


# 2. Обработка приветственного системного сообщения
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot** в режиме Автоматизации.\n\n"
        "📜 **Доступные команды в ваших бизнес-чатах:**\n"
        "• Ответом на сообщение человека:\n"
        "  ` .mute ` — включить автоудаление его реплик.\n"
        "  ` .unmute ` — снять блокировку реплик.\n"
        "  ` .note ` — сохранить это сообщение в Избранное (в ЛС бота).\n\n"
        "• Обычным текстом в любом месте чата:\n"
        "  ` .afk [причина] ` — автоответчик (например: `.afk Сплю`).\n"
        "  ` .unafk ` — выключить режим автоответчика.\n"
        "  ` .read ` — принудительно пометить текущий чат прочитанным.\n",
        parse_mode="Markdown"
    )


# 3. Базовый перехватчик сообщений (Слежка, исполнение Мута и логика AFK)
@dp.business_message()
async def handle_business_message(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    user_id = message.from_user.id

    # Восстановление карты соответствий в оперативной памяти после перезапуска кода
    if conn_id not in connection_owners:
        try:
            conn_info = await bot.get_business_connection(business_connection_id=conn_id)
            connection_owners[conn_id] = conn_info.user.id
        except Exception as e:
            logging.error(f"Не удалось восстановить сессию: {e}")

    # Мгновенная фильтрация и уничтожение реплик замученного пользователя
    if muted_users.get((conn_id, chat_id)) == user_id:
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            return
        except Exception as e:
            logging.error(f"Ошибка автоудаления в муте: {e}")

    # Исполнение автоответа в режиме AFK
    owner_id = connection_owners.get(conn_id)
    if owner_id and user_id != owner_id and owner_id in afk_status:
        if message.text and not message.from_user.is_bot:
            reason = afk_status[owner_id]
            afk_reply = f"🤖 [Автоответ] Сейчас я занят. Причина: *{reason}*"
            await bot.send_message(chat_id=chat_id, text=afk_reply, business_connection_id=conn_id, parse_mode="Markdown")

    # Архивация сообщений для проведения сверки детекторами модификаций
    if conn_id not in business_msg_history:
        business_msg_history[conn_id] = {}
    if message.text:
        business_msg_history[conn_id][message.message_id] = message.text
# 4. Модуль обработки префиксных точечных команд управления
@dp.business_message(F.text.startswith("."))
async def handle_business_commands(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    # Стабильный парсинг командных аргументов
    text_parts = message.text.strip().split(maxsplit=1)
    command = text_parts.lower()
    args = text_parts if len(text_parts) > 1 else ""

    owner_id = connection_owners.get(conn_id)
    if not owner_id:
        return

    # --- ОПЕРАЦИИ, ТРЕБУЮЩИЕ REPLY (ОТВЕТА НА СООБЩЕНИЕ СОБЕСЕДНИКА) ---
    if message.reply_to_message:
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.full_name

        if command == ".mute":
            muted_users[(conn_id, chat_id)] = target_user_id
            try:
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
                await bot.send_message(
                    chat_id=owner_id,
                    text=f"🔇 **Пользователь {target_username} замучен в чате {chat_id}**\nВсе новые реплики будут стираться автоматически.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Критический сбой .mute: {e}")
            return

        elif command == ".unmute":
            muted_users.pop((conn_id, chat_id), None)
            try:
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
                await bot.send_message(
                    chat_id=owner_id,
                    text=f"🔊 **Мут с пользователя {target_username} в чате {chat_id} успешно снят**",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Критический сбой .unmute: {e}")
            return

        elif command == ".note":
            note_content = message.reply_to_message.text or "[Медиафайл]"
            try:
                await bot.send_message(
                    chat_id=owner_id,
                    text=f"📌 **Новая заметка из чата `{chat_id}`**:\n\n{note_content}",
                    parse_mode="Markdown"
                )
                await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            except Exception as e:
                logging.error(f"Критический сбой .note: {e}")
            return

    # --- САМОСТОЯТЕЛЬНЫЕ ИЗОЛИРОВАННЫЕ КОМАНДЫ ---
    if command == ".afk":
        reason = args if args else "Отсутствую"
        afk_status[owner_id] = reason
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            await bot.send_message(
                chat_id=owner_id,
                text=f"💤 **Режим \"Не беспокоить\" успешно активирован**\nПричина: _{reason}_",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Критический сбой .afk: {e}")

    elif command == ".unafk":
        afk_status.pop(owner_id, None)
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
            await bot.send_message(
                chat_id=owner_id,
                text="🟢 **Режим \"Не беспокоить\" успешно деактивирован**",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Критический сбой .unafk: {e}")

    elif command == ".read":
        try:
            await bot.read_business_message(business_connection_id=conn_id, chat_id=chat_id, message_id=message.message_id)
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        except Exception as e:
            logging.error(f"Не удалось принудительно прочесть чат: {e}")


# 5. Детектор модификации и редактирования текстовых сообщений
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
                    f"🕵️‍♂️ **Изменено сообщение от {message.from_user.full_name}!**\n"
                    f"🌐 **Чат**: `{message.chat.full_name or message.chat.id}`\n\n"
                    f"**Было:** {old_text}\n"
                    f"**Стало:** {new_text}"
                )
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка трансляции лога изменений: {e}")


# 6. Детектор безвозвратного удаления сообщений корреспондентами
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
                logging.error(f"Ошибка трансляции лога удаления: {e}")
                
            del user_msgs[msg_id]


async def main():
    print("ArtefaktSpyBot запущен со всеми исправлениями!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
