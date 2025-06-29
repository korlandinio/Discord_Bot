# project/config/settings.py

import os
from dotenv import load_dotenv

load_dotenv()

# Токен Discord
TOKEN = os.getenv("DISCORD_TOKEN")

# Настройки базы данных (используем SQLite)
DATABASE_URL = "sqlite:///bot_data.db"

# Настройки безопасности и производительности
COMMAND_RATE_LIMIT = 10  # Команд в минуту на пользователя
BATCH_UPDATE_DELAY = 5   # Секунд задержки для пакетного обновления ролей