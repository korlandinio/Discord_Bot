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

    # --- Слеш команды ---
    @app_commands.command(name="создатьсписоксостава", description="Создает новый список для отслеживания состава.")
    @app_commands.default_permissions(administrator=True)
    async def create_list(self, interaction: discord.Interaction, title: str, roles: str):
        try:
            await interaction.response.defer(ephemeral=True)

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

            # Сохраняем запись в БД и получаем её ID
            new_list_id = db_manager.add_list(message.id, interaction.channel_id, interaction.guild_id, title, sections)
            
            # Используем ID, чтобы получить свежий, "живой" объект из БД
            db_list = db_manager.get_list(new_list_id)

            # Теперь db_list привязан к новой сессии и с ним можно безопасно работать
            content = generate_message_content(db_list)
            await message.edit(content=content)
            await interaction.followup.send(f"Список состава создан! ID: `{message.id}`", ephemeral=True)

        except Exception as e:
            await BotErrorHandler.handle(e, "создатьсписоксостава", interaction)

    @app_commands.command(name="удалитьсписоксостава", description="Удаляет список состава по ID сообщения.")
    @app_commands.default_permissions(administrator=True)
    async def delete_list(self, interaction: discord.Interaction, message_id: str):
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Проверяем, что ID является числом
            try:
                msg_id = int(message_id)
            except ValueError:
                await interaction.followup.send("Ошибка: ID сообщения должен быть числом.", ephemeral=True)
                return
            
            # Проверяем существование списка
            db_list = db_manager.get_list(msg_id)
            if not db_list:
                await interaction.followup.send("Ошибка: Список с таким ID не найден.", ephemeral=True)
                return
            
            # Проверяем, что список принадлежит этому серверу
            if db_list.guild_id != interaction.guild_id:
                await interaction.followup.send("Ошибка: Этот список не принадлежит данному серверу.", ephemeral=True)
                return
            
            # Удаляем сообщение из Discord
            try:
                channel = await self.bot.fetch_channel(db_list.channel_id)
                message = await channel.fetch_message(msg_id)
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                # Сообщение уже удалено или нет прав - это нормально
                pass
            
            # Удаляем из базы данных
            if db_manager.delete_list(msg_id):
                await interaction.followup.send(f"Список '{db_list.title}' успешно удален.", ephemeral=True)
            else:
                await interaction.followup.send("Ошибка при удалении списка из базы данных.", ephemeral=True)

        except Exception as e:
            await BotErrorHandler.handle(e, "удалитьсписоксостава", interaction)

    @app_commands.command(name="показатьсписки", description="Показывает все списки состава на сервере.")
    @app_commands.default_permissions(administrator=True)
    async def show_lists(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            
            all_lists = db_manager.get_lists_for_guild(interaction.guild_id)
            
            if not all_lists:
                await interaction.followup.send("На этом сервере нет активных списков состава.", ephemeral=True)
                return
            
            embed = discord.Embed(
                title="📋 Списки состава на сервере",
                color=discord.Color.blue()
            )
            
            for db_list in all_lists:
                channel = self.bot.get_channel(db_list.channel_id)
                channel_name = channel.name if channel else "❌ Канал удален"
                
                embed.add_field(
                    name=f"📝 {db_list.title}",
                    value=f"**ID:** `{db_list.message_id}`\n"
                          f"**Канал:** #{channel_name}\n"
                          f"**Создан:** {db_list.created_at.strftime('%d.%m.%Y %H:%M') if db_list.created_at else 'Неизвестно'}",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await BotErrorHandler.handle(e, "показатьсписки", interaction)




# --- Вспомогательный класс для подтверждения удаления ---
class DeleteConfirmView(discord.ui.View):
    def __init__(self, message_id: int, list_title: str):
        super().__init__(timeout=30.0)
        self.message_id = message_id
        self.list_title = list_title

    @discord.ui.button(label='Да, удалить', style=discord.ButtonStyle.danger, emoji='🗑️')
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Удаляем сообщение из Discord
            try:
                message = await interaction.channel.fetch_message(self.message_id)
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                # Сообщение уже удалено или нет прав - это нормально
                pass
            
            # Удаляем из базы данных
            if db_manager.delete_list(self.message_id):
                embed = discord.Embed(
                    title="✅ Список удален",
                    description=f"Список **'{self.list_title}'** успешно удален.",
                    color=discord.Color.green()
                )
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                await interaction.response.send_message("Ошибка при удалении списка из базы данных.", ephemeral=True)
                
        except Exception as e:
            await BotErrorHandler.handle(e, "confirm_delete", interaction)

    @discord.ui.button(label='Отмена', style=discord.ButtonStyle.secondary, emoji='❌')
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="❌ Удаление отменено",
            description="Список остался без изменений.",
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self):
        # Убираем кнопки по истечении времени
        for item in self.children:
            item.disabled = True


# --- Контекстные меню (определяются вне класса) ---
@app_commands.context_menu(name="Удалить список состава")
@app_commands.default_permissions(administrator=True)
async def delete_list_context(interaction: discord.Interaction, message: discord.Message):
    try:
        await interaction.response.defer(ephemeral=True)
        
        # Проверяем, является ли это сообщение списком состава
        db_list = db_manager.get_list(message.id)
        if not db_list:
            await interaction.followup.send("Это сообщение не является списком состава.", ephemeral=True)
            return
        
        # Проверяем права (список должен быть на этом сервере)
        if db_list.guild_id != interaction.guild_id:
            await interaction.followup.send("Ошибка: Этот список не принадлежит данному серверу.", ephemeral=True)
            return
        
        # Создаем подтверждающее embed
        embed = discord.Embed(
            title="⚠️ Подтверждение удаления",
            description=f"Вы действительно хотите удалить список **'{db_list.title}'**?",
            color=discord.Color.red()
        )
        
        # Создаем кнопки подтверждения
        view = DeleteConfirmView(db_list.message_id, db_list.title)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    except Exception as e:
        await BotErrorHandler.handle(e, "delete_list_context", interaction)

@app_commands.context_menu(name="Информация о списке")
@app_commands.default_permissions(administrator=True)
async def list_info_context(interaction: discord.Interaction, message: discord.Message):
    try:
        await interaction.response.defer(ephemeral=True)
        
        # Проверяем, является ли это сообщение списком состава
        db_list = db_manager.get_list(message.id)
        if not db_list:
            await interaction.followup.send("Это сообщение не является списком состава.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"📋 Информация о списке",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Название", value=db_list.title, inline=False)
        embed.add_field(name="ID сообщения", value=f"`{db_list.message_id}`", inline=True)
        embed.add_field(name="ID канала", value=f"`{db_list.channel_id}`", inline=True)
        embed.add_field(name="Создан", value=db_list.created_at.strftime('%d.%m.%Y %H:%M') if db_list.created_at else 'Неизвестно', inline=True)
        embed.add_field(name="Обновлен", value=db_list.updated_at.strftime('%d.%m.%Y %H:%M') if db_list.updated_at else 'Неизвестно', inline=True)
        
        # Информация о отслеживаемых ролях
        roles_info = []
        for role_id, section_data in db_list.sections.items():
            role = interaction.guild.get_role(int(role_id))
            status = "✅" if role else "❌"
            roles_info.append(f"{status} {section_data['role_name']} (`{role_id}`)")
        
        embed.add_field(
            name="Отслеживаемые роли",
            value="\n".join(roles_info) if roles_info else "Нет ролей",
            inline=False
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await BotErrorHandler.handle(e, "list_info_context", interaction)


async def setup(bot: commands.Bot):
    # Добавляем Cog
    await bot.add_cog(CompositionCog(bot))
    
    # Добавляем контекстные меню
    bot.tree.add_command(delete_list_context)
    bot.tree.add_command(list_info_context)