import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted
from aiogram.filters import CommandStart

# Токен вашего @ArtefaktSpyBot из @BotFather
BOT_TOKEN = "8689486048:AAFkgdmV4ZTtL8gAkfmEjWeXkrAufMM42kI"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Временная база данных в памяти
# 1. История сообщений: { business_connection_id: { message_id: text } }
business_msg_history = {}

# 2. Список замученных пользователей в конкретных чатах:
# Структура: { (business_connection_id, chat_id): target_user_id }
muted_users = {}


@dp.message(CommandStart())
async def cmd_start(message: Message):
    payload = message.text.split()[1] if len(message.text.split()) > 1 else None
    
    # Проверяем, пришел ли пользователь из меню управления конкретным чатом
    if payload and payload.startswith("bizChat"):
        # Telegram передает ID чата, в котором была нажата кнопка управления
        await message.answer(
            "🎛 **Управление текущим чатом запущено!**\n\n"
            "Вы можете управлять этим диалогом прямо отсюда:\n"
            "• Отправьте `.mute` в ответ на любое сообщение человека, чтобы включить автоудаление его реплик.\n"
            "• Отправьте `.unmute` в ответ на его сообщение, чтобы снять блокировку."
        )
        return

    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot**.\n\n"
        "Подключите меня через `Настройки -> Автоматизация чатов`.\n"
        "Я буду присылать логи удалений/изменений, а также позволю вам мутить людей с помощью команд `.mute` и `.unmute`."
    )


# 1. Обработка входящих бизнес-сообщений (Слежка + Мут)
@dp.business_message()
async def handle_business_message(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    user_id = message.from_user.id

    # Проверяем, находится ли пользователь в муте в этом конкретном чате
    if muted_users.get((conn_id, chat_id)) == user_id:
        try:
            # Метод удаления сообщений через бизнес-аккаунт
            await bot.delete_business_messages(
                business_connection_id=conn_id,
                message_ids=[message.message_id]
            )
            return  # Сообщение удалено, дальше код выполнять не нужно
        except Exception as e:
            logging.error(f"Не удалось удалить сообщение в муте: {e}")

    # Сохраняем текст сообщения в историю для отслеживания изменений/удалений
    if conn_id not in business_msg_history:
        business_msg_history[conn_id] = {}
        
    if message.text:
        business_msg_history[conn_id][message.message_id] = message.text


# 2. Обработка команд управления (.mute / .unmute), отправленных ВАМИ в бизнес-чате
@dp.business_message(F.text.startswith("."))
async def handle_business_commands(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    command = message.text.strip().lower()

    # Проверяем, что команда отправлена как ответ (reply) на сообщение нарушителя
    if not message.reply_to_message:
        return

    target_user_id = message.reply_to_message.from_user.id
    target_username = message.reply_to_message.from_user.full_name

    if command == ".mute":
        muted_users[(conn_id, chat_id)] = target_user_id
        
        # Удаляем саму команду .mute, чтобы не засорять чат
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        
        # Отправляем уведомление вам в личку с ботом, что мут активирован
        await bot.send_message(
            chat_id=message.from_user.id, 
            text=f"🔇 Пользователь **{target_username}** замучен в чате `{chat_id}`. Все его новые сообщения будут удаляться."
        )

    elif command == ".unmute":
        if (conn_id, chat_id) in muted_users:
            del muted_users[(conn_id, chat_id)]
            
        # Удаляем саму команду .unmute
        await bot.delete_business_messages(business_connection_id=conn_id, message_ids=[message.message_id])
        
        # Отправляем уведомление вам в личку с ботом
        await bot.send_message(
            chat_id=message.from_user.id, 
            text=f"🔊 Мут с пользователя **{target_username}** в чате `{chat_id}` успешно снят."
        )


# 3. Ловим ИЗМЕНЕННЫЕ сообщения
@dp.edited_business_message()
async def handle_edited_business_message(message: Message):
    conn_id = message.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})
    
    if message.message_id in user_msgs:
        old_text = user_msgs[message.message_id]
        new_text = message.text
        
        if old_text != new_text:
            user_msgs[message.message_id] = new_text
            log_text = (
                f"🕵️‍♂️ **Изменено сообщение от {message.from_user.full_name}!**\n\n"
                f"**Было:** {old_text}\n"
                f"**Шаг:** {new_text}"
            )
            # Отправляем уведомление владельцу бизнес-аккаунта в ЛС бота
            # Поле message.business_connection_id совпадает с логикой роутинга, но надежнее слать по ID
            # В данном примере шлем в чат инициатора
            await bot.send_message(chat_id=message.chat.id, text=log_text, parse_mode="Markdown")


# 4. Ловим УДАЛЕННЫЕ сообщения
@dp.deleted_business_messages()
async def handle_deleted_business_messages(deleted_messages: BusinessMessagesDeleted):
    conn_id = deleted_messages.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})
    
    for msg_id in deleted_messages.message_ids:
        if msg_id in user_msgs:
            old_text = user_msgs[msg_id]
            log_text = (
                f"🗑 **Удалено сообщение в чате!**\n\n"
                f"**Было:** {old_text}"
            )
            await bot.send_message(chat_id=deleted_messages.chat.id, text=log_text, parse_mode="Markdown")
            del user_msgs[msg_id]


async def main():
    print("ArtefaktSpyBot запущен. Модуль мута активен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
