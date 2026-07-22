import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessMessagesDeleted, BusinessConnection, FSInputFile
from aiogram.filters import CommandStart

# Токен вашего @ArtefaktSpyBot из @BotFather
BOT_TOKEN = "8689486048:AAFkgdmV4ZTtL8gAkfmEjWeXkrAufMM42kI"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Внутреннее хранилище в оперативной памяти сервера (RAM)
connection_owners = {}     # Карта соответствий { business_connection_id: user_id }
business_msg_history = {}  # Кэш истории текстовых сообщений { conn_id: { message_id: text } }

# Создаем папку на сервере для временного сохранения медиафайлов при пересылке
os.makedirs("media_cache", exist_ok=True)


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
                text="🚀 **ArtefaktSpyBot успешно подключен!**\n\nЯ начал мгновенный перехват. Все новые фотографии и видеоролики из ваших чатов будут сразу же дублироваться сюда. Удаленные и измененные тексты также фиксируются.",
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
            logging.error(f"Ошибка уведомления об отключении: {e}")


# 2. Приветственное сообщение при старте бота в ЛС
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я **ArtefaktSpyBot**.\n\n"
        "Я работаю полностью автоматически через функцию **Автоматизации чатов**.\n"
        "Просто подключите меня в настройках Telegram, и я буду присылать сюда:\n"
        "• ⚡️ **Мгновенные копии всех фото и видео** (сразу при отправке собеседником)\n"
        "• Удаленные текстовые сообщения\n"
        "• Измененные сообщения (Было / Стало)",
        parse_mode="Markdown"
    )


# 3. Мгновенный перехватчик входящего потока (Дублирование фото/видео + Сбор истории)
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

    # Запись обычного текста или описания медиафайла в кэш RAM для отслеживания изменений/удалений
    if conn_id not in business_msg_history:
        business_msg_history[conn_id] = {}
    
    if message.text:
        business_msg_history[conn_id][message.message_id] = message.text
    elif message.caption:
        business_msg_history[conn_id][message.message_id] = message.caption

    # МГНОВЕННЫЙ ПЕРЕХВАТ МЕДИА: Если сообщение содержит фото или видео, и оно пришло НЕ от вас
    if user_id != owner_id:
        file_to_download = None
        media_type = None

        if message.photo:
            media_type = "photo"
            file_to_download = message.photo[-1].file_id  # Максимальное качество
        elif message.video:
            media_type = "video"
            file_to_download = message.video.file_id

        # Если обнаружен медиафайл, бот скачивает и сразу пересылает его вам в ЛС
        if file_to_download and media_type:
            try:
                file_info = await bot.get_file(file_to_download)
                local_path = f"media_cache/{message.message_id}_{file_to_download}.jpg" if media_type == "photo" else f"media_cache/{message.message_id}_{file_to_download}.mp4"
                await bot.download_file(file_info.file_path, local_path)
                
                log_caption = (
                    f"⚡️ **Мгновенный перехват медиа!**\n"
                    f"👤 **От**: {message.from_user.full_name}\n"
                    f"🌐 **Чат**: `{message.chat.full_name or chat_id}`"
                )
                if message.caption:
                    log_caption += f"\n\n**Описание**: {message.caption}"

                # ИСПРАВЛЕНО: Используем правильный класс FSInputFile вместо F.InputFile
                if media_type == "photo":
                    await bot.send_photo(chat_id=owner_id, photo=FSInputFile(local_path), caption=log_caption, parse_mode="Markdown")
                elif media_type == "video":
                    await bot.send_video(chat_id=owner_id, video=FSInputFile(local_path), caption=log_caption, parse_mode="Markdown")

                # Мгновенно очищаем жесткий диск сервера от временного файла
                if os.path.exists(local_path):
                    os.remove(local_path)
            except Exception as e:
                logging.error(f"Ошибка мгновенного перехвата медиа: {e}")
# 4. Детектор модификации и редактирования сообщений от собеседников (Было / Стало)
@dp.edited_business_message()
async def handle_edited_business_message(message: Message):
    conn_id = message.business_connection_id
    user_msgs = business_msg_history.get(conn_id, {})
    
    if message.message_id in user_msgs:
        old_text = user_msgs[message.message_id]
        new_text = message.text or message.caption or "[Медиафайл]"
        
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
                logging.error(f"Ошибка收听 лога изменений: {e}")


# 5. Детектор безвозвратного удаления текстовых сообщений
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
                    f"🗑 **Удалено текстовое сообщение!**\n"
                    f"🌐 **ID чата**: `{deleted_messages.chat.id}`\n\n"
                    f"**Было:** {old_text}"
                )
                await bot.send_message(chat_id=owner_id, text=log_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка трансляции лога удаления: {e}")
                
            user_msgs.pop(msg_id, None)  # Очищаем память


async def main():
    print("ArtefaktSpyBot успешно запущен и полностью исправлен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
