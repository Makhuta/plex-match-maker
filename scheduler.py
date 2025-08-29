import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app import app, db
from models import LibraryConfig, MediaItem, ScanLog
from plex_client import PlexClient
from config import TZ

logger = logging.getLogger(__name__)

def scan_libraries():
    """Periodic task to scan all configured libraries"""
    with app.app_context():
        logger.info("Starting periodic library scan")
        
        scan_log = ScanLog()
        db.session.add(scan_log)
        db.session.commit()
        
        try:
            plex_client = PlexClient()
            if not plex_client.connect():
                scan_log.status = 'failed'
                scan_log.error_message = 'Failed to connect to Plex server'
                scan_log.scan_completed_at = datetime.now(tz=TZ)
                db.session.commit()
                return
            
            # Get all enabled library configurations
            configs = LibraryConfig.query.filter_by(enabled=True).all()
            scan_log.total_libraries = len(configs)
            
            total_media_found = 0
            total_matched = 0
            total_errors = 0
            
            for config in configs:
                try:
                    logger.info(f"Scanning library: {config.library_name}")
                    
                    # Get recent media from this library
                    recent_media = plex_client.get_recent_media(config.library_key, hours=24)
                    total_media_found += len(recent_media)
                    
                    for media in recent_media:
                        try:
                            # Check if we've already processed this media
                            existing = MediaItem.query.filter_by(
                                library_config_id=config.id,
                                plex_key=media['key']
                            ).first()
                            
                            if existing:
                                logger.debug(f"Media {media['title']} already processed")
                                continue
                            
                            # Create new media item record
                            media_item = MediaItem(
                                library_config_id=config.id,
                                plex_key=media['key'],
                                title=media['title'],
                                media_type=media['type'],
                                added_at=media['addedAt']
                            )
                            
                            # Attempt to match with configured agent
                            success, message = plex_client.match_with_agent(
                                config.library_key,
                                media['key'],
                                config.agent_name
                            )
                            
                            media_item.agent_matched = config.agent_name
                            media_item.match_successful = success
                            if not success:
                                media_item.error_message = message
                                total_errors += 1
                            else:
                                total_matched += 1
                            
                            db.session.add(media_item)
                            logger.info(f"Processed media: {media['title']} - Success: {success}")
                            
                        except Exception as e:
                            logger.error(f"Error processing media {media.get('title', 'Unknown')}: {e}")
                            total_errors += 1
                
                except Exception as e:
                    logger.error(f"Error scanning library {config.library_name}: {e}")
                    total_errors += 1
            
            # Update scan log
            scan_log.total_media_found = total_media_found
            scan_log.total_matched = total_matched
            scan_log.total_errors = total_errors
            scan_log.status = 'completed'
            scan_log.scan_completed_at = datetime.now(tz=TZ)
            
            db.session.commit()
            logger.info(f"Scan completed. Found: {total_media_found}, Matched: {total_matched}, Errors: {total_errors}")
            
        except Exception as e:
            logger.error(f"Fatal error during scan: {e}")
            scan_log.status = 'failed'
            scan_log.error_message = str(e)
            scan_log.scan_completed_at = datetime.now(tz=TZ)
            db.session.commit()

def init_scheduler(app):
    """Initialize the background scheduler"""
    scheduler = BackgroundScheduler()
    
    # Schedule library scan every 12 hours
    scheduler.add_job(
        func=scan_libraries,
        trigger=CronTrigger(hour='0,12', minute=0, timezone=TZ),
        id='library_scan',
        name='Scan Plex libraries for new media',
        replace_existing=True
    )

    # Initial scan
    scan_libraries()
    logger.info("Libraries initially scanned")
    
    # Start the scheduler
    scheduler.start()
    logger.info("Background scheduler started - scanning at 0 and 12 o'clock")
    
    # Register shutdown handler
    import atexit
    atexit.register(lambda: scheduler.shutdown())
