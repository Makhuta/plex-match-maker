import os
import logging
import pytz
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import urljoin
from config import TZ

logger = logging.getLogger(__name__)

class PlexClient:
    def __init__(self):
        self.plex_url = os.getenv('PLEX_URL', 'http://localhost:32400')
        self.plex_token = os.getenv('PLEX_TOKEN', '')
        self.server_info = None
        self.server_timezone = None
        self.session = requests.Session()
        
    def _make_request(self, endpoint, params=None):
        """Make authenticated request to Plex API"""
        if not self.plex_token:
            raise Exception("PLEX_TOKEN environment variable is required")
            
        url = urljoin(self.plex_url, endpoint)
        headers = {'X-Plex-Token': self.plex_token}
        
        if params is None:
            params = {}
            
        response = self.session.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return ET.fromstring(response.content)
        
    def connect(self):
        """Connect to Plex server and cache timezone info"""
        try:
            # Test connection and get server info
            root = self._make_request('/')
            self.server_info = {
                'friendlyName': root.get('friendlyName', 'Unknown'),
                'version': root.get('version', 'Unknown'),
                'platform': root.get('platform', 'Unknown')
            }
            
            # Get server timezone from preferences
            try:
                prefs_root = self._make_request('/:/prefs')
                timezone_pref = None
                
                for setting in prefs_root.findall('.//Setting'):
                    if setting.get('id') == 'TimezoneName':
                        timezone_pref = setting.get('value')
                        break
                
                if timezone_pref:
                    self.server_timezone = pytz.timezone(timezone_pref)
                else:
                    # Fallback to UTC if timezone not found
                    self.server_timezone = TZ
                    logger.warning(f"Could not detect server timezone, using {TZ.zone}")
                    
            except Exception as e:
                logger.warning(f"Could not get server timezone: {e}, using {TZ.zone}")
                self.server_timezone = TZ
            
            logger.info(f"Connected to Plex server: {self.server_info['friendlyName']}")
            logger.info(f"Server timezone: {self.server_timezone}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to connect to Plex server: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to Plex server: {e}")
            return False
    
    def get_libraries(self):
        """Get all libraries from Plex server"""
        if not self.server_info:
            if not self.connect():
                return []
        
        try:
            root = self._make_request('/library/sections')
            libraries = []
            
            for directory in root.findall('.//Directory'):
                libraries.append({
                    'key': directory.get('key'),
                    'title': directory.get('title'),
                    'type': directory.get('type'),
                    'agent': directory.get('agent', 'Unknown')
                })
            
            logger.info(f"Found {len(libraries)} libraries")
            return libraries
            
        except Exception as e:
            logger.error(f"Failed to get libraries: {e}")
            return []
    
    def get_recent_media(self, library_key, hours=24):
        """Get media added within the specified hours for a library"""
        if not self.server_info:
            if not self.connect():
                return []
        
        try:
            # Calculate cutoff time in server timezone
            now_server = datetime.now(self.server_timezone)
            cutoff_time = now_server - timedelta(hours=hours)
            cutoff_timestamp = int(cutoff_time.timestamp())
            
            # Get all media from library
            root = self._make_request(f'/library/sections/{library_key}/all')
            recent_media = []
            
            library_title = root.get('title1', f'Library {library_key}')
            
            # Process all media items
            for item in root.findall('.//Video') + root.findall('.//Track') + root.findall('.//Photo'):
                added_at_str = item.get('addedAt')
                if added_at_str:
                    try:
                        added_at_timestamp = int(added_at_str)
                        added_at_dt = datetime.fromtimestamp(added_at_timestamp)
                        
                        # If addedAt is within our window
                        if added_at_timestamp >= cutoff_timestamp:
                            # Convert to server timezone for display
                            if added_at_dt.tzinfo is None:
                                added_at_server = self.server_timezone.localize(added_at_dt)
                            else:
                                added_at_server = added_at_dt.astimezone(self.server_timezone)
                            
                            summary = item.get('summary', '')
                            if summary and len(summary) > 200:
                                summary = summary[:200] + '...'
                            
                            media_info = {
                                'key': item.get('key'),
                                'title': item.get('title'),
                                'type': item.get('type'),
                                'addedAt': added_at_dt,
                                'addedAt_server': added_at_server,
                                'year': item.get('year'),
                                'summary': summary
                            }
                            recent_media.append(media_info)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Could not parse addedAt timestamp {added_at_str}: {e}")
                        continue
            
            # Sort by addedAt descending
            recent_media.sort(key=lambda x: x['addedAt'], reverse=True)
            logger.info(f"Found {len(recent_media)} recent media items in library {library_title}")
            return recent_media
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.error(f"Library with ID {library_key} not found on Plex server. Please check your library configuration.")
            else:
                logger.error(f"HTTP error getting recent media for library {library_key}: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to get recent media for library {library_key}: {e}")
            return []
    
    
    def match_with_agent(self, library_key, media_key, agent_name):
        """Attempt to match media with specified agent - refresh if matched, match first available if unmatched"""
        if not self.server_info:
            if not self.connect():
                return False, "Not connected to Plex server"
        
        try:
            # Get media info first
            media_root = self._make_request(f'{media_key}')
            media_title = "Unknown"
            media_guid = None
            
            for item in media_root.findall('.//Video') + media_root.findall('.//Track') + media_root.findall('.//Photo'):
                if item.get('key') == media_key:
                    media_title = item.get('title', 'Unknown')
                    media_guid = item.get('guid', '')
                    break
            

            def is_guid_matched(media_guid: str) -> bool:
                if not media_guid:
                    return False
                # Unmatched cases
                if media_guid.startswith("plex://") or media_guid.startswith("local://"):
                    return False
                # Otherwise assume matched (imdb://, tmdb://, tvdb://, etc.)
                return True
            
            # Check if media is already matched (has a meaningful guid)
            is_matched = is_guid_matched(media_guid)
            
            if is_matched:
                # Media is already matched, just refresh it
                url = urljoin(self.plex_url, f'{media_key}/refresh')
                headers = {'X-Plex-Token': self.plex_token}
                
                response = self.session.put(url, headers=headers, timeout=30)
                response.raise_for_status()
                
                logger.info(f"Refreshed already matched media: {media_title} (guid: {media_guid})")
                return True, f"Refreshed matched media: {media_title}"
            
            else:
                # Media is unmatched, try to find and apply first available match
                try:
                    matches_root = self._make_request(f'{media_key}/matches')
                    matches = matches_root.findall('.//SearchResult')
                    
                    if not matches:
                        logger.info(f"No matches found for unmatched media: {media_title}")
                        return True, f"No matches available for: {media_title}"
                    
                    # Get the first match
                    first_match = matches[0]
                    match_guid = first_match.get('guid')
                    match_name = first_match.get('name', 'Unknown')
                    
                    if match_guid:
                        # Apply the first match
                        url = urljoin(self.plex_url, f'{media_key}/match')
                        headers = {'X-Plex-Token': self.plex_token}
                        params = {'guid': match_guid}
                        
                        response = self.session.put(url, headers=headers, params=params, timeout=30)
                        response.raise_for_status()
                        
                        logger.info(f"Matched unmatched media: {media_title} → {match_name} ({match_guid})")
                        return True, f"Matched: {media_title} → {match_name}"
                    else:
                        logger.warning(f"First match for {media_title} has no guid")
                        return True, f"Match found but no guid available for: {media_title}"
                        
                except requests.exceptions.HTTPError as matches_error:
                    if matches_error.response.status_code == 404:
                        logger.info(f"No matches endpoint available for: {media_title}")
                        return True, f"No matches available for: {media_title}"
                    else:
                        logger.error(f"Error getting matches for {media_title}: {matches_error}")
                        return False, f"Error getting matches: {matches_error}"
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_msg = f"Library {library_key} or media {media_key} not found"
            else:
                error_msg = f"HTTP error processing media {media_key}: {e}"
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Failed to process media {media_key} with agent {agent_name}: {e}"
            logger.error(error_msg)
            return False, error_msg
    
    def validate_library_config(self, library_key):
        """Validate if a library configuration is valid"""
        if not self.server_info:
            if not self.connect():
                return False, "Unable to connect to Plex server"
        
        try:
            root = self._make_request(f'/library/sections/{library_key}')
            library_title = root.get('title1', f'Library {library_key}')
            return True, f"Library '{library_title}' is valid"
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return False, f"Library with ID {library_key} not found on Plex server"
            else:
                return False, f"HTTP error validating library {library_key}: {e}"
        except Exception as e:
            return False, f"Error validating library {library_key}: {e}"
