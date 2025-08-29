import logging
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify
from app import app, db
from models import LibraryConfig, MediaItem, ScanLog
from plex_client import PlexClient
from config import TZ
from scheduler import scheduler_next_run

logger = logging.getLogger(__name__)

@app.route('/')
def index():
    """Main dashboard showing overview of libraries and recent scans"""
    configs = LibraryConfig.query.all()
    recent_scan = ScanLog.query.order_by(ScanLog.scan_started_at.desc()).first()
    
    # Get recent media for each library
    library_stats = []
    for config in configs:
        recent_media = MediaItem.query.filter_by(library_config_id=config.id)\
                                     .order_by(MediaItem.processed_at.desc())\
                                     .limit(10).all()
        library_stats.append({
            'config': config,
            'recent_media': recent_media,
            'total_media': MediaItem.query.filter_by(library_config_id=config.id).count()
        })
    
    return render_template('index.html', 
                         library_stats=library_stats, 
                         recent_scan=recent_scan,
                         scheduler_next_run=scheduler_next_run())

@app.route('/config')
def config():
    """Library configuration management page"""
    configs = LibraryConfig.query.all()
    return render_template('config.html', configs=configs, scheduler_next_run=scheduler_next_run())

@app.route('/config/add', methods=['GET', 'POST'])
def add_config():
    """Add new library configuration"""
    if request.method == 'GET':
        # Get available libraries from Plex
        plex_client = PlexClient()
        if not plex_client.connect():
            flash('Failed to connect to Plex server. Please check your PLEX_URL and PLEX_TOKEN environment variables.', 'danger')
            return redirect(url_for('config'))
        
        libraries = plex_client.get_libraries()
        if not libraries:
            flash('No libraries found on Plex server. Please check your Plex server configuration.', 'warning')
            return redirect(url_for('config'))
            
        return render_template('config.html', libraries=libraries, mode='add', scheduler_next_run=scheduler_next_run())
    
    # POST request - create new configuration
    library_key = request.form.get('library_key')
    library_name = request.form.get('library_name')
    library_type = request.form.get('library_type')
    agent_name = request.form.get('agent_name')
    
    logger.info(f"Form data received - Library Key: {library_key}, Name: {library_name}, Type: {library_type}, Agent: {agent_name}")
    
    if not all([library_key, library_name, library_type, agent_name]):
        missing_fields = []
        if not library_key: missing_fields.append('Library Key')
        if not library_name: missing_fields.append('Library Name') 
        if not library_type: missing_fields.append('Library Type')
        if not agent_name: missing_fields.append('Agent Name')
        flash(f'Missing required fields: {", ".join(missing_fields)}', 'danger')
        return redirect(url_for('add_config'))
    
    # Check if configuration already exists
    existing = LibraryConfig.query.filter_by(library_key=library_key).first()
    if existing:
        flash('Configuration for this library already exists.', 'warning')
        return redirect(url_for('config'))
    
    # Create new configuration
    new_config = LibraryConfig(
        library_key=library_key,
        library_name=library_name,
        library_type=library_type,
        agent_name=agent_name
    )
    
    try:
        db.session.add(new_config)
        db.session.commit()
        logger.info(f"Successfully saved configuration for library: {library_name} (ID: {library_key})")
        flash(f'Configuration added for library: {library_name}', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding configuration: {e}")
        flash(f'Error adding configuration: {str(e)}', 'danger')
    
    return redirect(url_for('config'))

@app.route('/config/edit/<int:config_id>', methods=['GET', 'POST'])
def edit_config(config_id):
    """Edit existing library configuration"""
    config = LibraryConfig.query.get_or_404(config_id)
    
    if request.method == 'GET':
        return render_template('config.html', config=config, mode='edit', scheduler_next_run=scheduler_next_run())
    
    # POST request - update configuration
    config.library_name = request.form.get('library_name', config.library_name)
    config.agent_name = request.form.get('agent_name', config.agent_name)
    config.enabled = 'enabled' in request.form
    config.updated_at = datetime.now(tz=TZ)
    
    try:
        db.session.commit()
        flash(f'Configuration updated for library: {config.library_name}', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating configuration: {e}")
        flash('Error updating configuration. Please try again.', 'danger')
    
    return redirect(url_for('config'))

@app.route('/config/delete/<int:config_id>', methods=['POST'])
def delete_config(config_id):
    """Delete library configuration"""
    config = LibraryConfig.query.get_or_404(config_id)
    library_name = config.library_name
    
    try:
        db.session.delete(config)
        db.session.commit()
        flash(f'Configuration deleted for library: {library_name}', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting configuration: {e}")
        flash('Error deleting configuration. Please try again.', 'danger')
    
    return redirect(url_for('config'))

@app.route('/library/<int:config_id>')
def library_detail(config_id):
    """Detailed view of a specific library"""
    config = LibraryConfig.query.get_or_404(config_id)
    
    # Get recent media for this library
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    media_items = MediaItem.query.filter_by(library_config_id=config_id)\
                                .order_by(MediaItem.processed_at.desc())\
                                .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('library_detail.html', config=config, media_items=media_items, scheduler_next_run=scheduler_next_run())


@app.route('/scan/manual')
def manual_scan():
    """Trigger a manual scan"""
    from scheduler import scan_libraries
    
    try:
        # Run scan in background
        import threading
        thread = threading.Thread(target=scan_libraries)
        thread.start()
        
        flash('Manual scan started. Check back in a few minutes for results.', 'info')
    except Exception as e:
        logger.error(f"Error starting manual scan: {e}")
        flash('Error starting manual scan. Please try again.', 'danger')
    
    return redirect(url_for('index'))

@app.route('/logs')
def scan_logs():
    """View scan logs"""
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    logs = ScanLog.query.order_by(ScanLog.scan_started_at.desc())\
                       .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('index.html', logs=logs, show_logs=True, scheduler_next_run=scheduler_next_run())

@app.route('/debug/libraries')
def debug_libraries():
    """Debug endpoint to show available Plex libraries"""
    plex_client = PlexClient()
    
    if not plex_client.connect():
        return jsonify({
            "error": "Failed to connect to Plex server",
            "plex_url": plex_client.plex_url,
            "has_token": bool(plex_client.plex_token)
        })
    
    libraries = plex_client.get_libraries()
    return jsonify({
        "connected": True,
        "server_name": plex_client.server_info.get('friendlyName', 'Unknown') if plex_client.server_info else "Unknown",
        "timezone": str(plex_client.server_timezone),
        "libraries": libraries,
        "library_count": len(libraries)
    })

@app.route('/config/validate')
def validate_configs():
    """Validate all library configurations"""
    configs = LibraryConfig.query.all()
    plex_client = PlexClient()
    
    if not plex_client.connect():
        flash('Failed to connect to Plex server. Please check your PLEX_URL and PLEX_TOKEN.', 'danger')
        return redirect(url_for('config'))
    
    validation_results = []
    for config in configs:
        is_valid, message = plex_client.validate_library_config(config.library_key)
        validation_results.append({
            'config': config,
            'is_valid': is_valid,
            'message': message
        })
    
    return render_template('config.html', validation_results=validation_results, mode='validate', scheduler_next_run=scheduler_next_run())
