import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message

# Официальные вшитые ключи Telegram Desktop — работают автоматически для всех аккаунтов
API_ID = 4
API_HASH = "014b35b6184100b085b0b05726cf5508"

logging.basicConfig(level=logging.INFO)

# Инициализируем клиента Pyrogram, который создаст файл сессии artefakt.session
app = Client("artefakt_session", api_id=API_ID, api_hash=API_HASH)

# Базы данных в оперативной памяти сервера для хранения истории переписок
msg_history = {}    # { chat_id: { message_id: text } }
media_history = {}  # { chat_id: { message_id: {"file_id": ..., "type": ..., "caption": ...} } }

# Создаем скрытые папки для сохранения удаленного контента
os.makedirs("cached_media", exist_ok=True)


# 1. ОБРАБОТЧИК: Скачивание и сохранение абсолютно всех входящих сообщений (текст, фото, видео)
@app.on_message(filters.incoming & ~filters.bot, group=0)
async def cache_incoming_messages(client: Client, message: Message):
    chat_id = message.chat.id
    msg_id = message.id

    # Инициализируем кэш для текущего чата, если его еще нет
    if chat_id not in msg_history:
        msg_history[chat_id] = {}
    if chat_id not in media_history:
        media_history[chat_id] = {}

    # Перехват и кэширование обычного текста
    if message.text:
        msg_history[chat_id][msg_id] = message.text
    elif message.caption:
        msg_history[chat_id][msg_id] = message.caption

    # Перехват медиафайлов (включая фото, видео, голосовые и ОДНОРАЗОВЫЕ View Once)
    file_id = None
    media_type = None

    if message.photo:
        media_type = "photo"
        file_id = message.photo.file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    elif message.voice:
        media_type = "voice"
        file_id = message.voice.file_id
    elif message.video_note:
        media_type = "round"
        file_id = message.video_note.file_id
    elif message.document:
        media_type = "document"
        file_id = message.document.file_id

    # Если в сообщении есть медиа, юзербот скачивает его на компьютер до того, как его удалят
    if file_id and media_type:
        try:
            # Скачиваем файл во временную защищенную папку
            local_path = await client.download_media(message, file_name=f"cached_media/{file_id}")
            media_history[chat_id][msg_id] = {
                "path": local_path,
                "type": media_type,
                "caption": message.caption or ""
            }
        except Exception as e:
            logging.error(f"Не удалось закэшировать входящий файл: {e}")
# 2. ДЕТЕКТОР ИЗМЕНЕНИЙ: Срабатывает, когда собеседник отредактировал текст
@app.on_edited_message(filters.incoming & ~filters.bot)
async def handle_edited_messages(client: Client, message: Message):
    chat_id = message.chat.id
    msg_id = message.id
    
    user_chat_history = msg_history.get(chat_id, {})
    if msg_id in user_chat_history:
        old_text = user_chat_history[msg_id]
        new_text = message.text or message.caption or "[Медиафайл]"
        
        if old_text != new_text:
            user_chat_history[msg_id] = new_text  # Обновляем историю в памяти
            
            sender_name = message.from_user.first_name if message.from_user else "Пользователь"
            log_text = (
                f"🕵️‍♂️ **Изменено сообщение от {sender_name}!**\n"
                f"🌐 **Чат**: `{message.chat.title or message.chat.first_name}`\n\n"
                f"**Было:** {old_text}\n"
                f"**Стало:** {new_text}"
            )
            # Отправляет отчет вам в Избранное (Saved Messages)
            await client.send_message(chat_id="me", text=log_text)


# 3. ДЕТЕКТОР УДАЛЕНИЙ: Перехватывает удаление сообщений (включая фото, видео и View Once)
@app.on_deleted_messages(group=2)
async def handle_deleted_messages(client: Client, messages: list):
    for deleted_msg in messages:
        chat_id = deleted_msg.chat.id
        msg_id = deleted_msg.id
        
        # 1. Проверяем, было ли удалено текстовое сообщение
        user_chat_history = msg_history.get(chat_id, {})
        if msg_id in user_chat_history:
            old_text = user_chat_history[msg_id]
            log_text = (
                f"🗑 **Удалено текстовое сообщение!**\n"
                f"🌐 **Чат**: `{deleted_msg.chat.title or deleted_msg.chat.first_name}`\n\n"
                f"**Было:** {old_text}"
            )
            await client.send_message(chat_id="me", text=log_text)
            del user_chat_history[msg_id]

        # 2. Проверяем, было ли удалено медиасообщение (фото, видео, одноразовое View Once)
        user_media_history = media_history.get(chat_id, {})
        if msg_id in user_media_history:
            media_data = user_media_history[msg_id]
            local_path = media_data["path"]
            m_type = media_data["type"]
            caption = media_data["caption"]

            log_caption = (
                f"🗑 **Перехвачено удаленное медиа!**\n"
                f"🌐 **Чат**: `{deleted_messages_chat_name_stub(deleted_msg)}`"
            )
            if caption:
                log_caption += f"\n\n**Описание к файлу было:** {caption}"

            try:
                # Отправляем сохраненный файл вам в Избранное в зависимости от его типа
                if os.path.exists(local_path):
                    if m_type == "photo":
                        await client.send_photo(chat_id="me", photo=local_path, caption=log_caption)
                    elif m_type == "video":
                        await client.send_video(chat_id="me", video=local_path, caption=log_caption)
                    elif m_type == "voice":
                        await client.send_voice(chat_id="me", voice=local_path, caption=log_caption)
                    elif m_type == "round":
                        await client.send_video_note(chat_id="me", video_note=local_path)
                    elif m_type == "document":
                        await client.send_document(chat_id="me", document=local_path, caption=log_caption)
                    
                    # Полностью стираем временный файл с диска компьютера, чтобы освободить место
                    os.remove(local_path)
            except Exception as e:
                logging.error(f"Не удалось переслать удаленный медиафайл: {e}")
                
            del user_media_history[msg_id]

def deleted_messages_chat_name_stub(msg):
    return msg.chat.title or msg.chat.first_name or f"ID: {msg.chat.id}"


if __name__ == "__main__":
    print("Юзербот запущен и начал скрытый перехват текстов, фото, видео и одноразовых медиа...")
    app.run()
