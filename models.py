from datetime import datetime
from app import db
from config import TZ

class LibraryConfig(db.Model):
    """Configuration for each Plex library"""
    __tablename__ = 'library_configs'
    
    id = db.Column(db.Integer, primary_key=True)
    library_key = db.Column(db.String(100), unique=True, nullable=False)
    library_name = db.Column(db.String(200), nullable=False)
    library_type = db.Column(db.String(50), nullable=False)  # movie, show, artist
    agent_name = db.Column(db.String(100), nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(tz=TZ))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(tz=TZ), onupdate=lambda: datetime.now(tz=TZ))
    
    # Relationship to media items
    media_items = db.relationship('MediaItem', backref='library_config', lazy=True, cascade='all, delete-orphan')

class MediaItem(db.Model):
    """Tracks media items that have been processed"""
    __tablename__ = 'media_items'
    
    id = db.Column(db.Integer, primary_key=True)
    library_config_id = db.Column(db.Integer, db.ForeignKey('library_configs.id'), nullable=False)
    plex_key = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(500), nullable=False)
    media_type = db.Column(db.String(50), nullable=False)
    added_at = db.Column(db.DateTime, nullable=False)
    processed_at = db.Column(db.DateTime, default=lambda: datetime.now(tz=TZ))
    agent_matched = db.Column(db.String(100))
    match_successful = db.Column(db.Boolean, default=False)
    error_message = db.Column(db.Text)
    
    __table_args__ = (db.UniqueConstraint('library_config_id', 'plex_key', name='_library_media_uc'),)

class ScanLog(db.Model):
    """Logs of scanning operations"""
    __tablename__ = 'scan_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    scan_started_at = db.Column(db.DateTime, default=lambda: datetime.now(tz=TZ))
    scan_completed_at = db.Column(db.DateTime)
    total_libraries = db.Column(db.Integer, default=0)
    total_media_found = db.Column(db.Integer, default=0)
    total_matched = db.Column(db.Integer, default=0)
    total_errors = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default='running')  # running, completed, failed
    error_message = db.Column(db.Text)
