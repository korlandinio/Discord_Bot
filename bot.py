# project/bot.py

import discord
from discord.ext import commands
import asyncio
import logging

from config.settings import TOKEN
from utils.data_manager import db_manager
from utils.error_handler import BotErrorHandler

# --- Настройка ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

intents = discord.Intents.default()
intents.members = True
intents.guilds = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        # Подключаем менеджеры к боту для доступа из Cogs
        self.db = db_manager

    async def setup_hook(self):
        # Загружаем все коги из папки cogs
        initial_extensions = ['cogs.composition']
        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
                logging.info(f"Загружен Cog: {extension}")
            except Exception as e:
                logging.error(f"Не удалось загрузить Cog {extension}: {e}", exc_info=True)
        
        # Синхронизируем команды
        synced = await self.tree.sync()
        logging.info(f"Синхронизировано {len(synced)} команд.")

    async def on_ready(self):
        logging.info(f'Бот {self.user} готов к работе!')

    # Глобальный обработчик ошибок для слеш-команд
    async def on_tree_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        await BotErrorHandler.handle(error, f"Глобальный обработчик для команды: {interaction.command.name if interaction.command else 'N/A'}", interaction)


async def main():
    bot = MyBot()
    if not TOKEN:
        logging.critical("Токен Discord не найден. Проверьте .env файл.")
        return
    
    try:
        await bot.start(TOKEN)
    except discord.LoginFailure:
        logging.critical("Неверный токен Discord. Не удалось войти.")
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную.")