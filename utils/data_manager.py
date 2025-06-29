# project/utils/data_manager.py

import datetime
from sqlalchemy import create_engine, Column, Integer, String, JSON, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from contextlib import contextmanager

from config.settings import DATABASE_URL

Base = declarative_base()

class CompositionList(Base):
    """Модель данных для списка состава в базе данных."""
    __tablename__ = 'composition_lists'
    
    message_id = Column(Integer, primary_key=True)
    channel_id = Column(Integer, nullable=False)
    guild_id = Column(Integer, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    sections = Column(JSON, nullable=False) # {'role_id': {'header': '...', 'role_name': '...'}}
    current_users = Column(JSON, nullable=False) # {'role_id': ['user_mention', ...]}
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class CompositionListData:
    """Простой класс для хранения данных без привязки к SQLAlchemy сессии."""
    def __init__(self, message_id, channel_id, guild_id, title, sections, current_users, created_at=None, updated_at=None):
        self.message_id = message_id
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.title = title
        self.sections = sections
        self.current_users = current_users
        self.created_at = created_at
        self.updated_at = updated_at
    
    @classmethod
    def from_db_object(cls, db_obj):
        """Создает экземпляр из объекта SQLAlchemy."""
        return cls(
            message_id=db_obj.message_id,
            channel_id=db_obj.channel_id,
            guild_id=db_obj.guild_id,
            title=db_obj.title,
            sections=db_obj.sections,
            current_users=db_obj.current_users,
            created_at=db_obj.created_at,
            updated_at=db_obj.updated_at
        )

class DatabaseManager:
    """Класс для централизованного управления сессиями и операциями с БД."""
    def __init__(self, db_url):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        """Обеспечивает корректное управление сессиями."""
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_list(self, message_id: int):
        """Возвращает CompositionListData объект, не привязанный к сессии."""
        with self.session_scope() as session:
            db_obj = session.query(CompositionList).filter_by(message_id=message_id).first()
            if db_obj:
                return CompositionListData.from_db_object(db_obj)
            return None

    def get_lists_for_guild(self, guild_id: int):
        """Возвращает список CompositionListData объектов, не привязанных к сессии."""
        with self.session_scope() as session:
            db_objects = session.query(CompositionList).filter_by(guild_id=guild_id).all()
            return [CompositionListData.from_db_object(db_obj) for db_obj in db_objects]

    def add_list(self, message_id, channel_id, guild_id, title, sections):
        """Добавляет новый список и возвращает его message_id."""
        with self.session_scope() as session:
            initial_users = {role_id: [] for role_id in sections.keys()}
            new_list = CompositionList(
                message_id=message_id,
                channel_id=channel_id,
                guild_id=guild_id,
                title=title,
                sections=sections,
                current_users=initial_users
            )
            session.add(new_list)
            session.flush()  # Получаем ID до коммита
            return new_list.message_id

    def update_list_content(self, message_id: int, new_sections=None, new_users=None, new_title=None):
        """Обновляет содержимое списка."""
        with self.session_scope() as session:
            db_list = session.query(CompositionList).filter_by(message_id=message_id).first()
            if db_list:
                if new_sections is not None:
                    db_list.sections = new_sections
                if new_users is not None:
                    db_list.current_users = new_users
                if new_title is not None:
                    db_list.title = new_title
                # updated_at обновится автоматически
                return True
            return False

    def delete_list(self, message_id: int):
        """Удаляет список из базы данных."""
        with self.session_scope() as session:
            db_list = session.query(CompositionList).filter_by(message_id=message_id).first()
            if db_list:
                session.delete(db_list)
                return True
            return False

# Создаем единственный экземпляр менеджера
db_manager = DatabaseManager(DATABASE_URL)