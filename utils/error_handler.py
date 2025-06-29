# project/utils/error_handler.py

import discord
import logging
import uuid

logger = logging.getLogger(__name__)

class BotErrorHandler:
    """Централизованный класс для обработки ошибок."""
    @staticmethod
    async def handle(error: Exception, context: str, interaction: discord.Interaction = None):
        """
        Логирует ошибку и отправляет пользователю сообщение.
        """
        error_id = str(uuid.uuid4())[:8]
        logger.error(f"[Error ID: {error_id}] Контекст: '{context}'. Ошибка: {error}", exc_info=True)
        
        user_message = f"Произошла непредвиденная ошибка (ID: `{error_id}`). Администратор уже уведомлен."
        
        if interaction:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(user_message, ephemeral=True)
                else:
                    await interaction.response.send_message(user_message, ephemeral=True)
            except discord.HTTPException:
                logger.error(f"Не удалось отправить сообщение об ошибке {error_id} пользователю.")