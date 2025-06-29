# project/cogs/composition.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
import re
import asyncio
from collections import defaultdict

from utils.data_manager import db_manager
from utils.error_handler import BotErrorHandler
from config.settings import BATCH_UPDATE_DELAY

# --- Вспомогательные функции ---
def generate_message_content(db_list) -> str:
    """Генерирует контент сообщения на основе данных из БД."""
    content = f"**{db_list.title}**\n\n"
    sorted_sections = sorted(db_list.sections.items(), key=lambda item: item[1].get('position', 0), reverse=True)

    for role_id, section_data in sorted_sections:
        header = section_data['header']
        users_in_section = db_list.current_users.get(role_id, [])
        
        content += f"**{header}**:\n"
        if not users_in_section:
            content += "  *Пока никого нет.*\n"
        else:
            content += "\n".join(f"  • {user}" for user in users_in_section) + "\n"
        content += "\n"
    
    return content.strip()

# --- Основной Cog ---
class CompositionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.update_queue = defaultdict(set) # {guild_id: {member_id, ...}}
        self.batch_processor.start()

    def cog_unload(self):
        self.batch_processor.cancel()

    # --- Пакетная обработка обновлений для производительности ---
    @tasks.loop(seconds=BATCH_UPDATE_DELAY)
    async def batch_processor(self):
        if not self.update_queue:
            return

        # Копируем очередь, чтобы избежать проблем с асинхронностью
        current_queue = self.update_queue.copy()
        self.update_queue.clear()

        for guild_id, member_ids in current_queue.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            # Получаем все списки для данного сервера
            all_lists = db_manager.get_lists_for_guild(guild_id)
            if not all_lists:
                continue
            
            # Получаем объекты участников - исправленный код
            members = {}
            try:
                # Используем async for для итерации по async generator
                async for member in guild.fetch_members(limit=None):
                    if member.id in member_ids:
                        members[member.id] = member
            except discord.Forbidden:
                # Если нет прав на получение участников, используем кеш
                members = {m.id: m for m in guild.members if m.id in member_ids}
            except Exception as e:
                # В случае других ошибок логируем и пропускаем
                print(f"Ошибка при получении участников для гильдии {guild_id}: {e}")
                continue
            
            for db_list in all_lists:
                updated = False
                new_users_data = db_list.current_users.copy()
                
                for member_id in member_ids:
                    member = members.get(member_id)
                    if not member: continue

                    user_mention = member.mention
                    
                    # Удаляем пользователя из всех секций
                    for role_id in new_users_data:
                        if user_mention in new_users_data[role_id]:
                            new_users_data[role_id].remove(user_mention)
                            updated = True
                    
                    # Находим его наивысшую роль и добавляем
                    highest_role_id = None
                    highest_pos = -1
                    for role in member.roles:
                        if str(role.id) in db_list.sections and role.position > highest_pos:
                            highest_pos = role.position
                            highest_role_id = str(role.id)
                    
                    if highest_role_id:
                        if highest_role_id not in new_users_data:
                            new_users_data[highest_role_id] = []
                        new_users_data[highest_role_id].append(user_mention)
                        updated = True

                if updated:
                    db_manager.update_list_content(db_list.message_id, new_users=new_users_data)
                    try:
                        channel = await self.bot.fetch_channel(db_list.channel_id)
                        message = await channel.fetch_message(db_list.message_id)
                        await message.edit(content=generate_message_content(db_list))
                    except (discord.NotFound, discord.Forbidden):
                        # Если сообщение не найдено, оно будет удалено при следующей команде
                        pass
    
    # --- События ---
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles != after.roles:
            # Ставим обновление в очередь для пакетной обработки
            self.update_queue[after.guild.id].add(after.id)

    # --- Команды ---
    @app_commands.command(name="создатьсписоксостава", description="Создает новый список для отслеживания состава.")
    @app_commands.default_permissions(administrator=True)
    async def create_list(self, interaction: discord.Interaction, title: str, roles: str):
        # Оборачиваем в try-except, чтобы наш центральный обработчик ловил ошибки
        try:
            await interaction.response.defer(ephemeral=True) # Отвечаем сразу, но скрыто

            role_ids = re.findall(r'<@&(\d+)>', roles)
            if not role_ids or len(role_ids) != len(set(role_ids)):
                await interaction.followup.send("Ошибка: Укажите уникальные роли для отслеживания.", ephemeral=True)
                return
            
            sections = {}
            for role_id_str in role_ids:
                role = interaction.guild.get_role(int(role_id_str))
                if role:
                    sections[str(role.id)] = {
                        'header': role.name,
                        'role_name': role.name,
                        'position': role.position
                    }
            
            # Создаем "пустое" сообщение, чтобы получить ID
            message = await interaction.channel.send("Создание списка...")

            # ИЗМЕНЕНИЕ 1: Сохраняем запись в БД и получаем её ID
            new_list_id = db_manager.add_list(message.id, interaction.channel_id, interaction.guild_id, title, sections)
            
            # ИЗМЕНЕНИЕ 2: Используем ID, чтобы получить свежий, "живой" объект из БД
            db_list = db_manager.get_list(new_list_id)

            # Теперь db_list привязан к новой сессии и с ним можно безопасно работать
            content = generate_message_content(db_list)
            await message.edit(content=content)
            await interaction.followup.send(f"Список состава создан! ID: `{message.id}`", ephemeral=True)

        except Exception as e:
            await BotErrorHandler.handle(e, "создатьсписоксостава", interaction)
    
    # ... Другие команды (удалить, показать) и контекстные меню можно добавить сюда по аналогии ...


async def setup(bot: commands.Bot):
    await bot.add_cog(CompositionCog(bot))