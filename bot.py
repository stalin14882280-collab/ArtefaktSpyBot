import os
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
business_msg_history = {}  # Кэш истории текстовых сообщений { conn_id: { message_id: text } }

# Создаем временную папку для сохранения одноразовых медиафайлов
os.makedirs("downloads", exist_ok=True)


# 1. Отслеживание подключения бота к бизнес-аккаунту (Вход в 1 клик)
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    user_id = connection.user.id
    conn_id = connection.id
    
    if connection.is_enabled:
        connection_owners[conn_id] = user_id
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🚀 **ArtefaktSpyBot успешно подключен!**\n\nЯ начал скрытый мониторинг ваших чатов. Теперь все удаленные сообщения, изменения текста и одноразовые медиафайлы (фото/видео) будут пересылаться сюда.",
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
        "👋 Привет! Я **ArtefaktSpyBot**.\n\n"
        "Я работаю полностью в фоновом режиме через функцию **Автоматизации чатов**.\n"
        "Просто подключите меня в настройках Telegram, и я буду присылать сюда:\n"
        "• Удаленные сообщения\n"
        "• Измененные сообщения (Было / Стало)\n"
        "• Скрытые одноразовые фото и видео, которые отправляют собеседники",
        parse_mode="Markdown"
    )


# 3. Базовый перехватчик входящего бизнес-потока (Сбор истории + Перехват одноразовых медиа)
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
            logging.error(f"Не удалось восстановить сессию владельца: {e}")

    owner_id = connection_owners.get(conn_id)
    if not owner_id:
        return

    # --- ФУНКЦИЯ ПЕРЕХВАТА ОДНОРАЗОВЫХ ФОТО И ВИДЕО (VIEW ONCE) ---
    # Проверяем, содержит ли сообщение скрытый таймер самоликвидации (has_media_spoiler или специальный флаг)
    is_one_time = False
    file_to_download = None
    media_type = None

    if message.photo and message.has_media_spoiler:  # Telegram часто помечает одноразовые медиа спойлером в API
        is_one_time = True
        file_to_download = message.photo[-1].file_id
        media_type = "photo"
    elif message.video and message.video.ttl_seconds:  # Проверка по TTL (Time To Live) таймеру
        is_one_time = True
        file_to_download = message.video.file_id
        media_type = "video"

    if is_one_time and file_to_download and user_id != owner_id:
        try:
            # Скачиваем исчезающий файл на сервер
            file_info = await bot.get_file(file_to_download)
            destination = f"downloads/{file_to_download}"
            await bot.download_file(file_info.file_path, destination)
            
            # Пересылаем файл владельцу бота как обычное сообщение
            log_caption = f"👁‍🗨 **Перехвачено одноразовое медиа!**\n👤 **От**: {message.from_user.full_name}\n🌐 **Чат**: `{message.chat.full_name or chat_id}`"
            
            if media_type == "photo":
                await bot.send_photo(chat_id=owner_id, photo=F.InputFile(destination), caption=log_caption, parse_mode="Markdown")
            elif media_type == "video":
                await bot.send_video(chat_id=owner_id, video=F.InputFile(destination), caption=log_caption, parse_mode="Markdown")
                
            # Удаляем временный файл с диска сервера
            if os.path.exists(destination):
                os.remove(destination)
        except Exception as e:
            logging.error(f"Ошибка перехвата одноразового медиафайла: {e}")

    # Архивация обычного текста в память RAM для проверки изменений
    if conn_id not in business_msg_history:
        business_msg_history[conn_id] = {}
    if message.text:
        business_msg_history[conn_id][message.message_id] = message.text
# 4. Детектор модификации и редактирования текстовых сообщений (Было / Стало)
@dp.edited_business_message()
async def handle_edited_business_message(message: Message):
    conn_id = message.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})
    
    if message.message_id in user_msgs:
        old_text = user_msgs[message.message_id]
        new_text = message.text
        
        if old_text != new_text:
            user_msgs[message.message_id] = new_text  # Обновляем кэш истории
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
                # Отправляем лог строго владельцу в ЛС с ботом
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка трансляции лога изменений: {e}")


# 5. Детектор безвозвратного удаления сообщений собеседниками
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
                # Отправляем лог строго владельцу в ЛС с ботом
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка трансляции лога удаления: {e}")
                
            del user_msgs[msg_id]  # Очищаем кэш сообщения


async def main():
    print("ArtefaktSpyBot успешно запущен в чистом режиме шпионажа!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
