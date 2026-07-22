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

# Внутреннее хранилище данных в оперативной памяти (RAM)
connection_owners = {}     # Карта соответствий { business_connection_id: user_id }
business_msg_history = {}  # Кэш истории переписок { conn_id: { message_id: text } }
muted_users = {}           # Списки замученных людей { (conn_id, chat_id): target_user_id }
afk_status = {}            # Статусы режима AFK { user_id: reason_text }


# 1. Отслеживание подключения и отключения бота от бизнес-аккаунта (Вход в 1 клик)
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    user_id = connection.user.id
    conn_id = connection.id
    
    if connection.is_enabled:
        connection_owners[conn_id] = user_id
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🚀 **ArtefaktSpyBot успешно подключен автоматически!**\n\nЯ интегрирован в ваши чаты. Теперь вы можете использовать команды `.spam`, `.mute` и `.afk`.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Ошибка уведомления: {e}")
    else:
        try:
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ **Бот отключен от вашего аккаунта.**",
                parse_mode="Markdown"
            )
            connection_owners.pop(conn_id, None)
            business_msg_history.pop(conn_id, None)
        except Exception as e:
            logging.error(f"Ошибка уведомления: {e}")


# 2. Приветственное сообщение при старте бота в ЛС
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot** в режиме Автоматизации.\n\n"
        "📜 **Доступные команды в ваших бизнес-чатах:**\n"
        "• ` .spam [текст] [кол-во] ` — запустить массовую отправку.\n"
        "• ` .mute ` *(ответом на сообщение человека)* — удалять его новые реплики.\n"
        "• ` .unmute ` *(ответом на сообщение человека)* — снять мут.\n"
        "• ` .afk [причина] ` — включить автоответчик.\n",
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

    # Мгновенное автоматическое удаление входящих сообщений от замученного пользователя
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

    if conn_id not in business_msg_history:
        business_msg_history[conn_id] = {}
    if message.text:
        business_msg_history[conn_id][message.message_id] = message.text
# 4. Модуль обработки префиксных точечных команд управления
@dp.business_message(F.text.startswith("."))
async def handle_business_commands(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    
    # Безопасный разбор текста на команду и аргументы
    raw_text = message.text.strip()
    text_parts = raw_text.split()
    if not text_parts:
        return
        
    command = text_parts.lower()

    owner_id = connection_owners.get(conn_id)
    if not owner_id:
        return

    # Функция удаления самой команды из чата
    async def delete_cmd():
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        except Exception:
            pass

    # --- КОМАНДА: .spam [текст] [количество] ---
    if command == ".spam":
        await delete_cmd()
        
        if len(text_parts) < 3:
            return
            
        count_str = text_parts[-1]
        if not count_str.isdigit():
            return
            
        count = int(count_str)
        spam_text = " ".join(text_parts[1:-1])
        
        if count > 50:  # Лимит безопасности для предотвращения блокировок
            count = 50

        for _ in range(count):
            try:
                # Отправка сообщений в текущий бизнес-чат
                await bot.send_message(chat_id=chat_id, business_connection_id=conn_id, text=spam_text)
                await asyncio.sleep(0.4)  # Интервал безопасности
            except Exception as e:
                logging.error(f"Ошибка отправки спама: {e}")
                break
        return

    # --- КОМАНДЫ С REPLY (ОТВЕТОМ НА СООБЩЕНИЕ ЧЕЛОВЕКА) ---
    if message.reply_to_message:
        target_user_id = message.reply_to_message.from_user.id
        target_username = message.reply_to_message.from_user.full_name

        if target_user_id == owner_id:
            await delete_cmd()
            return

        if command == ".mute":
            muted_users[(conn_id, chat_id)] = target_user_id
            await delete_cmd()
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    business_connection_id=conn_id,
                    text=f"🔇 **Пользователь {target_username} замучен**",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            return

        elif command == ".unmute":
            muted_users.pop((conn_id, chat_id), None)
            await delete_cmd()
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    business_connection_id=conn_id,
                    text=f"🔊 **Мут с пользователя {target_username} снят**",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            return

    # --- САМОСТОЯТЕЛЬНЫЕ КОМАНДЫ БЕЗ REPLY ---
    if command == ".afk":
        reason = " ".join(text_parts[1:]) if len(text_parts) > 1 else "Отсутствую"
        afk_status[owner_id] = reason
        await delete_cmd()
        try:
            await bot.send_message(
                chat_id=chat_id,
                business_connection_id=conn_id,
                text=f"💤 **Режим \"Не беспокоить\" активирован**\nПричина: _{reason}_",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    elif command == ".unafk":
        afk_status.pop(owner_id, None)
        await delete_cmd()
        try:
            await bot.send_message(
                chat_id=chat_id,
                business_connection_id=conn_id,
                text="🟢 **Режим \"Не беспокоить\" деактивирован**",
                parse_mode="Markdown"
            )
        except Exception:
            pass


# 5. Детектор изменений сообщений от собеседников
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


# 6. Детектор удалений сообщений от собеседников
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
    print("ArtefaktSpyBot успешно запущен в автоматическом бизнес-режиме!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
