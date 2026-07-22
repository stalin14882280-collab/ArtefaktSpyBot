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
business_msg_history = {}  # Кэш текстовой истории { conn_id: { message_id: text } }
business_photo_history = {}  # Кэш файлов фото { conn_id: { message_id: file_id } }

# Создаем папку на сервере для временного хранения фотографий
os.makedirs("photo_cache", exist_ok=True)


# 1. Отслеживание подключения бота к вашему аккаунту
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    user_id = connection.user.id
    conn_id = connection.id
    
    if connection.is_enabled:
        connection_owners[conn_id] = user_id
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🚀 **ArtefaktSpyBot успешно подключен!**\n\nЯ начал фоновый мониторинг чатов. Все удаленные/измененные тексты, а также удаленные фотографии будут приходить сюда.",
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
            business_photo_history.pop(conn_id, None)
        except Exception as e:
            logging.error(f"Ошибка уведомления об отключении: {e}")


# 2. Приветственное сообщение при старте бота в ЛС
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot**.\n\n"
        "Я работаю полностью в фоновом режиме через функцию **Автоматизации чатов**.\n"
        "Просто подключите меня в настройках Telegram, и я буду присылать сюда:\n"
        "• Удаленные текстовые сообщения\n"
        "• Измененные сообщения (Было / Стало)\n"
        "• Удаленные собеседником фотографии",
        parse_mode="Markdown"
    )


# 3. Базовый перехватчик сообщений (Сбор текстовой истории и скачивание фото на сервер)
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

    # Инициализируем локальные словари под текущее соединение в RAM
    if conn_id not in business_msg_history:
        business_msg_history[conn_id] = {}
    if conn_id not in business_photo_history:
        business_photo_history[conn_id] = {}

    # Если пришел обычный текст или описание к фото — запоминаем его
    if message.text:
        business_msg_history[conn_id][message.message_id] = message.text
    elif message.caption:
        business_msg_history[conn_id][message.message_id] = message.caption

    # Если в сообщении есть ФОТОГРАФИЯ, и она пришла от собеседника (не от вас)
    if message.photo and owner_id and user_id != owner_id:
        try:
            # Берем самый максимальный размер фотографии (последний элемент массива)
            photo_file_id = message.photo[-1].file_id
            file_info = await bot.get_file(photo_file_id)
            
            # Скачиваем файл в локальный кэш сервера под именем ID этого сообщения
            local_path = f"photo_cache/{message.message_id}.jpg"
            await bot.download_file(file_info.file_path, local_path)
            
            # Запоминаем путь к сохраненному файлу в кэш-таблицу
            business_photo_history[conn_id][message.message_id] = local_path
        except Exception as e:
            logging.error(f"Ошибка фонового сохранения фотографии: {e}")
# 4. Детектор модификации и редактирования сообщений от собеседников
@dp.edited_business_message()
async def handle_edited_business_message(message: Message):
    conn_id = message.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})
    
    if message.message_id in user_msgs:
        old_text = user_msgs[message.message_id]
        new_text = message.text or message.caption or "[Фотография/Файл]"
        
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
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка трансляции лога изменений: {e}")


# 5. Детектор безвозвратного удаления (Текст + Пересылка сохраненных фото из кэша)
@dp.deleted_business_messages()
async def handle_deleted_business_messages(deleted_messages: BusinessMessagesDeleted):
    conn_id = deleted_messages.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})
    user_photos = business_photo_history.get(conn_id, {})
    
    owner_id = connection_owners.get(conn_id)
    if not owner_id: 
        return

    for msg_id in deleted_messages.message_ids:
        # Проверяем, было ли у этого сообщения сохраненное ФОТО в кэше
        if msg_id in user_photos:
            local_path = user_photos[msg_id]
            caption_text = user_msgs.get(msg_id, "") # Проверяем, было ли описание к фото
            
            try:
                if os.path.exists(local_path):
                    log_caption = f"🗑 **Перехвачена удаленная фотография!**\n🌐 **ID чата**: `{deleted_messages.chat.id}`"
                    if caption_text:
                        log_caption += f"\n\n**Описание к фото было:** {caption_text}"
                        
                    # Отправляем сохраненный файл фотографии владельцу в ЛС
                    await bot.send_photo(
                        chat_id=owner_id,
                        photo=F.InputFile(local_path),
                        caption=log_caption,
                        parse_mode="Markdown"
                    )
                    
                    # Полностью удаляем временную фотографию с диска сервера, чтобы не забивать память
                    os.remove(local_path)
            except Exception as e:
                logging.error(f"Ошибка отправки удаленного фото: {e}")
                
            user_photos.pop(msg_id, None)
            user_msgs.pop(msg_id, None)
            continue

        # Если фото не было, проверяем стандартное текстовое сообщение
        if msg_id in user_msgs:
            old_text = user_msgs[msg_id]
            try:
                log_text = (
                    f"🗑 **Удалено текстовое сообщение!**\n"
                    f"🌐 **ID чата**: `{deleted_messages.chat.id}`\n\n"
                    f"**Было:** {old_text}"
                )
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка трансляции лога удаления: {e}")
                
            user_msgs.pop(msg_id, None)


async def main():
    print("ArtefaktSpyBot успешно запущен в режиме сохранения текстов и фотографий!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
