import logging
import os
import json
import asyncio
from typing import Optional, Dict, List, Any, Union
from urllib.parse import quote_plus
import httpx

from app.core.utils import proxy_cover_url
from app.core.platform_cache import cached_soundcloud_search, cached_soundcloud_stream
# Note: similar tracks are fetched using SoundCloud's /tracks/{id}/related endpoint
from app.core.http_client import get_async_client

logger = logging.getLogger(__name__)

SOUNDCLOUD_API_V2_URL = 'https://api-v2.soundcloud.com'
SOUNDCLOUD_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
SOUNDCLOUD_APP_VERSION = '1702458641'

def get_soundcloud_client_id():
    """Get a SoundCloud client ID from the environment variable."""
    client_ids_str = os.getenv('SOUNDCLOUD_CLIENT_IDS')
    if not client_ids_str:
        return None
    
    # Split by comma and take the first one
    client_ids = [cid.strip() for cid in client_ids_str.split(',') if cid.strip()]
    return client_ids[0] if client_ids else None

def get_soundcloud_client_ids():
    """Get all SoundCloud client IDs from the environment variable."""
    client_ids_str = os.getenv('SOUNDCLOUD_CLIENT_IDS')
    if not client_ids_str:
        return []
    
    # Split by comma and strip whitespace
    client_ids = [cid.strip() for cid in client_ids_str.split(',') if cid.strip()]
    return client_ids

# Cache for working client ID
_working_client_id = None
_client_id_lock = asyncio.Lock()

async def get_working_soundcloud_client_id():
    """Get a working SoundCloud client ID, trying all available keys with caching."""
    global _working_client_id
    
    async with _client_id_lock:
        # Return cached working ID if available
        if _working_client_id:
            return _working_client_id
        
        client_ids = get_soundcloud_client_ids()
        if not client_ids:
            logger.error('❌ No SoundCloud client IDs configured')
            return None
        
        client = await get_async_client()
        
        for client_id in client_ids:
            try:
                # Test the client ID with a lightweight search call which reliably returns 200 for valid keys
                test_url = f'{SOUNDCLOUD_API_V2_URL}/search/tracks'
                params = {
                    'q': 'a',
                    'limit': 1,
                    'client_id': client_id,
                    'app_version': SOUNDCLOUD_APP_VERSION,
                    'app_locale': 'en'
                }
                headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}

                response = await client.get(test_url, params=params, headers=headers, timeout=5.0)

                if response.status_code == 200:
                    _working_client_id = client_id
                    return client_id
                # treat 403 as forbidden and try next key; treat other codes as non-working for this test
                if response.status_code == 403:
                    continue
            except Exception:
                # ignore and try next key
                continue
        
        return None

def invalidate_soundcloud_client_cache():
    """Invalidate the cached working client ID to force re-testing on next request."""
    global _working_client_id
    logger.info('🔄 Invalidating SoundCloud client ID cache')
    _working_client_id = None

async def make_soundcloud_request(url: str, params: dict = None, headers: dict = None, method: str = 'GET', max_retries: int = 1):
    """
    Make a SoundCloud API request with automatic client ID rotation on 403 errors.
    
    Args:
        url: API endpoint URL
        params: Query parameters
        headers: Request headers
        method: HTTP method
        max_retries: Maximum number of retries with different client IDs
        
    Returns:
        Response object or None if all attempts failed
    """
    if params is None:
        params = {}
    if headers is None:
        headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    
    client = await get_async_client()
    
    for attempt in range(max_retries + 1):
        # Get current working client ID (may change between attempts)
        client_id = await get_working_soundcloud_client_id()
        if not client_id:
            return None
        
        # Add client_id to params if not already present
        request_params = params.copy()
        request_params['client_id'] = client_id
        request_params['app_version'] = SOUNDCLOUD_APP_VERSION
        request_params['app_locale'] = 'en'
        
        try:
            if method.upper() == 'GET':
                response = await client.get(url, params=request_params, headers=headers, timeout=10.0)
            else:
                # For other methods, add params to URL
                from urllib.parse import urlencode
                query_string = urlencode(request_params)
                full_url = f"{url}?{query_string}" if '?' not in url else f"{url}&{query_string}"
                response = await client.request(method, full_url, headers=headers, timeout=10.0)
            
            if response.status_code == 403:
                invalidate_soundcloud_client_cache()
                if attempt < max_retries:
                    continue
                else:
                    return None
            else:
                return response
                
        except Exception as e:
            if attempt < max_retries:
                invalidate_soundcloud_client_cache()
                continue
            else:
                return None
    
    return None

def _format_duration_soundcloud(duration_ms: Optional[int]) -> str:
    if not duration_ms or duration_ms <= 0:
        return "0:00"
    seconds = duration_ms // 1000
    minutes = seconds // 60
    seconds %= 60
    hours = minutes // 60
    minutes %= 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"

def _get_safe_artwork_url_soundcloud(url: Optional[str], preferred_size: str = 't500x500') -> Optional[str]:
    """Safely gets a larger artwork URL from SoundCloud, handling None and replacing size markers."""
    if not url or not isinstance(url, str):
        return None
    original_url = str(url)
    try:
        sizes_to_replace = ['badge', 'tiny', 'small', 't67x67', 'mini', 't120x120', 'large', 't300x300', 'crop']
        if f"-{preferred_size}." in original_url or "-original." in original_url:
            return original_url
        for size_marker in sizes_to_replace:
            if f"-{size_marker}." in original_url:
                new_url = original_url.replace(f"-{size_marker}.", f"-{preferred_size}.")
                return new_url
        if original_url.startswith('http'):
            return original_url
        logger.warning(f"_get_safe_artwork_url_soundcloud: URL '{original_url}' is not recognized after checks (no markers, not http). Returning None.")
        return None
    except Exception as e:
        logger.warning(f"_get_safe_artwork_url_soundcloud: Could not process SoundCloud artwork URL '{original_url}': {e}")
        return original_url

def format_soundcloud_track(track_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Formats SoundCloud track data into a common application format."""
    if not track_data or not isinstance(track_data, dict):
        return None
    try:
        track_id = track_data.get('id')
        if not track_id:
            logger.warning("SoundCloud track data missing ID.")
            return None
        has_progressive_stream = False
        is_streamable_api_flag = track_data.get('streamable', False)
        media = track_data.get('media', {})
        transcodings = media.get('transcodings', [])
        for transcoding in transcodings:
            if (transcoding.get('format', {}).get('protocol') == 'progressive' and transcoding.get('url')):
                has_progressive_stream = True
                break
        duration_ms = track_data.get('duration', 0)
        duration_seconds = duration_ms // 1000
        formatted_track = {
            'id': str(track_id),
            'title': track_data.get('title', 'Unknown Title'),
            'artist': track_data.get('user', {}).get('username', 'Unknown Artist'),
            'duration': duration_seconds,
            'durationString': _format_duration_soundcloud(duration_ms),
            'source': 'soundcloud',
            'streamable': has_progressive_stream or is_streamable_api_flag,
            'coverArt': None,
            'genre': track_data.get('genre'),
            'permalinkUrl': track_data.get('permalink_url')
        }
        artwork_url = track_data.get('artwork_url')
        user_avatar_url = track_data.get('user', {}).get('avatar_url')
        final_cover_art = _get_safe_artwork_url_soundcloud(artwork_url)
        if not final_cover_art:
            final_cover_art = _get_safe_artwork_url_soundcloud(user_avatar_url)
        formatted_track['coverArt'] = proxy_cover_url(final_cover_art)
        return formatted_track
    except Exception as e:
        logger.error(f"❌ Format track {track_data.get('id', 'n/a')}: {str(e)[:80]}")
        return None

async def expand_short_url(short_url: str) -> Optional[str]:
    """
    Expands a short SoundCloud URL (on.soundcloud.com) to the full URL.
    
    Args:
        short_url: Short URL like https://on.soundcloud.com/xxxxx
        
    Returns:
        Full SoundCloud URL or None if failed
    """
    client = await get_async_client()
    headers = {
        'User-Agent': SOUNDCLOUD_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    try:
        # Follow redirects to get final URL
        response = await client.get(short_url, headers=headers, follow_redirects=True, timeout=10.0)
        final_url = str(response.url)
        
        # Verify it's a valid soundcloud.com URL
        if 'soundcloud.com' in final_url and 'on.soundcloud.com' not in final_url:
            logger.info(f"expanded short url {short_url} -> {final_url}")
            return final_url
        else:
            logger.warning(f"short url {short_url} did not resolve to valid soundcloud.com URL: {final_url}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Short URL expand error: {str(e)[:80]}")
        return None

async def resolve_soundcloud_url(url: str) -> Optional[Dict[str, Any]]:
    """
    Resolves a SoundCloud URL to get the object data.
    Supports both regular soundcloud.com URLs and short on.soundcloud.com URLs.
    """
    # Handle short URLs (on.soundcloud.com)
    if 'on.soundcloud.com' in url:
        logger.info(f"detected short soundcloud url: {url}")
        expanded_url = await expand_short_url(url)
        if not expanded_url:
            logger.error(f"❌ failed to expand short url: {url}")
            return None
        url = expanded_url
    
    params = {'url': url}
    response = await make_soundcloud_request(f'{SOUNDCLOUD_API_V2_URL}/resolve', params=params)
    
    if not response:
        return None
    
    try:
        response.raise_for_status()
        data = response.json()
        return data
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code if hasattr(e, 'response') and e.response else 'error'
        logger.error(f"❌ SoundCloud resolve error {url[:50]}: HTTP {status_code}")
        # Return error info for better error messages
        return {"error": "resolve_failed", "status_code": status_code}
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json for soundcloud resolve {url}: {e}. response: {response.text[:200] if response else 'n/a'}")
        return None
    except Exception as e:
        logger.error(f"❌ SoundCloud resolve {url[:50]}: {str(e)[:100]}")
        return None

async def get_soundcloud_playlist_tracks(playlist_id: str) -> Dict[str, Any]:
    """Gets ALL tracks from a SoundCloud playlist using efficient batch ID fetching."""
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('❌ soundcloud client id not found, cannot get playlist tracks.')
        return {'name': 'Unknown Playlist', 'tracks': []}
    
    params = {
        'client_id': client_id,
        'app_version': SOUNDCLOUD_APP_VERSION,
        'app_locale': 'en'
    }
    headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    client = await get_async_client()
    
    try:
        # Get playlist - first ~5 tracks will have full data, rest will be ID-only
        response = await client.get(
            f'{SOUNDCLOUD_API_V2_URL}/playlists/{playlist_id}',
            params=params,
            headers=headers,
            timeout=30.0
        )
        response.raise_for_status()
        playlist_data = response.json()
        
        playlist_name = playlist_data.get('title', 'Unknown Playlist')
        track_count = playlist_data.get('track_count', 0)
        
        logger.info(f"soundcloud playlist {playlist_id}: '{playlist_name}' has {track_count} tracks")
        
        all_tracks = playlist_data.get('tracks', [])
        
        # Separate full tracks and ID-only tracks
        full_tracks = []
        id_only_tracks = []
        
        for track_data in all_tracks:
            # Check if track has full data (title, user, etc) or just ID
            if track_data.get('title') and track_data.get('user'):
                full_tracks.append(track_data)
            else:
                id_only_tracks.append(track_data)
        
        logger.debug(f"soundcloud playlist {playlist_id}: {len(full_tracks)} full tracks, {len(id_only_tracks)} ID-only tracks")
        
        # Format the full tracks we already have
        formatted_tracks = []
        for track_data in full_tracks:
            formatted_track = format_soundcloud_track(track_data)
            if formatted_track:
                formatted_tracks.append(formatted_track)
        
        # Batch fetch ID-only tracks if any
        if id_only_tracks:
            # Collect IDs
            track_ids = [str(track.get('id')) for track in id_only_tracks if track.get('id')]
            
            if track_ids:
                logger.debug(f"soundcloud playlist {playlist_id}: batch fetching {len(track_ids)} tracks by ID")
                
                # Batch size ~30 (SoundCloud seems to use this)
                batch_size = 30
                for i in range(0, len(track_ids), batch_size):
                    batch_ids = track_ids[i:i + batch_size]
                    ids_param = ','.join(batch_ids)
                    
                    try:
                        batch_params = {
                            'ids': ids_param,
                            'client_id': client_id,
                            'app_version': SOUNDCLOUD_APP_VERSION,
                            'app_locale': 'en'
                        }
                        
                        batch_response = await client.get(
                            f'{SOUNDCLOUD_API_V2_URL}/tracks',
                            params=batch_params,
                            headers=headers,
                            timeout=30.0
                        )
                        batch_response.raise_for_status()
                        batch_tracks = batch_response.json()
                        
                        # Format batch tracks
                        for track_data in batch_tracks:
                            formatted_track = format_soundcloud_track(track_data)
                            if formatted_track:
                                formatted_tracks.append(formatted_track)
                        
                        logger.debug(f"soundcloud playlist {playlist_id}: loaded batch {i//batch_size + 1}, total {len(formatted_tracks)}/{track_count}")
                        
                    except Exception as batch_error:
                        logger.error(f"❌ error batch fetching tracks for playlist {playlist_id}: {batch_error}")
                        # Continue with what we have
                        break
        
        logger.info(f"✅ soundcloud playlist {playlist_id} '{playlist_name}': loaded {len(formatted_tracks)}/{track_count} tracks")
        return {'name': playlist_name, 'tracks': formatted_tracks}
        
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Playlist {playlist_id}: HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return {'name': 'Unknown Playlist', 'tracks': []}
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json for soundcloud playlist tracks {playlist_id}: {e}. response: {response.text[:200] if response else 'n/a'}")
        return {'name': 'Unknown Playlist', 'tracks': []}
    except Exception as e:
        logger.error(f"❌ Playlist {playlist_id}: {str(e)[:100]}")
        return {'name': 'Unknown Playlist', 'tracks': []}


async def search_soundcloud_playlists(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Searches playlists on SoundCloud (not albums)."""
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('❌ soundcloud client id not found, cannot search playlists.')
        return []
    actual_limit = min(max(1, int(limit)), 50)
    params = {
        'q': query,
        'client_id': client_id,
        'limit': actual_limit,
        'offset': 0,
        'app_version': SOUNDCLOUD_APP_VERSION,
        'app_locale': 'en'
    }
    headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    client = await get_async_client()
    try:
        response = await client.get(f'{SOUNDCLOUD_API_V2_URL}/search/playlists', params=params, headers=headers, timeout=10.0)
        response.raise_for_status()
        search_data = response.json()
        playlists = []
        for item_data in search_data.get('collection', []):
            # только плейлисты, не альбомы
            if item_data.get('playlist_type') != 'album':
                formatted_playlist = {
                    'id': str(item_data.get('id', '')),
                    'title': item_data.get('title', 'Unknown Playlist'),
                    'artist': item_data.get('user', {}).get('username', 'Unknown Artist'),
                    'coverArt': proxy_cover_url(_get_safe_artwork_url_soundcloud(item_data.get('artwork_url'))),
                    'totalTracks': item_data.get('track_count', 0),
                    'source': 'soundcloud',
                    'permalinkUrl': item_data.get('permalink_url'),
                    'description': item_data.get('description')
                }
                playlists.append(formatted_playlist)
        return playlists
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Search playlists '{query[:30]}': HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json from soundcloud playlist search for '{query}': {e}. response text: {response.text[:200] if response else 'n/a'}")
        return []
    except Exception as e:
        logger.error(f"❌ Search playlists '{query[:30]}': {str(e)[:80]}")
        return []

async def search_soundcloud_albums(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Searches albums on SoundCloud."""
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('❌ soundcloud client id not found, cannot search albums.')
        return []
    actual_limit = min(max(1, int(limit)), 50)
    params = {
        'q': query,
        'client_id': client_id,
        'limit': actual_limit,
        'offset': 0,
        'app_version': SOUNDCLOUD_APP_VERSION,
        'app_locale': 'en'
    }
    headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    client = await get_async_client()
    try:
        response = await client.get(f'{SOUNDCLOUD_API_V2_URL}/search/albums', params=params, headers=headers, timeout=10.0)
        
        # если эндпоинт albums не работает, используем playlists с фильтром
        if response.status_code == 404:
            response = await client.get(f'{SOUNDCLOUD_API_V2_URL}/search/playlists', params=params, headers=headers, timeout=10.0)
        
        response.raise_for_status()
        search_data = response.json()
        albums = []
        for item_data in search_data.get('collection', []):
            # принимаем альбомы и плейлисты с малым количеством треков (обычно альбомы)
            playlist_type = item_data.get('playlist_type')
            track_count = item_data.get('track_count', 0)
            
            # альбом если: явно указан тип album, или если нет типа и мало треков
            is_album = (playlist_type == 'album' or 
                       (not playlist_type and track_count > 0 and track_count <= 30))
            
            if is_album:
                formatted_album = {
                    'id': str(item_data.get('id', '')),
                    'title': item_data.get('title', 'Unknown Album'),
                    'artist': item_data.get('user', {}).get('username', 'Unknown Artist'),
                    'coverArt': proxy_cover_url(_get_safe_artwork_url_soundcloud(item_data.get('artwork_url'))),
                    'totalTracks': track_count,
                    'source': 'soundcloud',
                    'permalinkUrl': item_data.get('permalink_url'),
                    'description': item_data.get('description')
                }
                albums.append(formatted_album)
        return albums
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Search albums '{query[:30]}': HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json from soundcloud album search for '{query}': {e}. response text: {response.text[:200] if response else 'n/a'}")
        return []
    except Exception as e:
        logger.error(f"❌ Search albums '{query[:30]}': {str(e)[:80]}")
        return []

@cached_soundcloud_search(ttl=3600)  # 1h cache
async def search_soundcloud(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Searches tracks on SoundCloud."""
    actual_limit = min(max(1, int(limit)), 50)
    params = {
        'q': query,
        'limit': actual_limit,
        'offset': 0
    }
    
    response = await make_soundcloud_request(f'{SOUNDCLOUD_API_V2_URL}/search/tracks', params=params)
    
    if not response:
        return []
    
    try:
        response.raise_for_status()
        search_data = response.json()
        tracks = []
        for item_data in search_data.get('collection', []):
            formatted_track = format_soundcloud_track(item_data)
            if formatted_track:
                tracks.append(formatted_track)
        return tracks
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Search tracks '{query[:30]}': HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json from soundcloud search for '{query}': {e}. response text: {response.text[:200] if response else 'n/a'}")
        return []
    except Exception as e:
        logger.error(f"❌ Search tracks '{query[:30]}': {str(e)[:80]}")
        return []

@cached_soundcloud_stream(ttl=600)  # 10min cache for resolve->stream
async def get_soundcloud_stream_url(track_id: Union[str, int], track_data: Optional[Dict[str, Any]] = None, preferred_format: Optional[str] = None) -> Optional[Dict[str, str]]:
    """
    Gets the direct stream URL for a SoundCloud track.
    
    Args:
        track_id: SoundCloud track ID
        track_data: Optional pre-fetched track data
        preferred_format: Optional format preference:
                         - None or 'mp3': progressive MP3 (backward compatible, 128 kbps)
                         - 'hls': best HLS format (opus > aac > mp3, higher quality)
    
    Returns:
        Dict with url, type ('mp3' or 'hls'), and source, or None if unavailable
    """
    # очищаем track_id от префикса platform если он там есть
    if isinstance(track_id, str) and ':' in track_id:
        track_id = track_id.split(':')[-1]
    
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('❌ soundcloud client id not found, cannot get stream url.')
        return None
    headers = {
        'User-Agent': SOUNDCLOUD_USER_AGENT,
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': 'https://soundcloud.com/'
    }
    client = await get_async_client()
    try:
        track_info_url = f'{SOUNDCLOUD_API_V2_URL}/tracks/{track_id}?client_id={client_id}&app_version={SOUNDCLOUD_APP_VERSION}'
        track_response = await client.get(track_info_url, headers=headers, timeout=10.0)
        track_response.raise_for_status()
        track_data = track_response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Track metadata {track_id}: HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json from soundcloud track metadata for id {track_id}: {e}. response: {track_response.text[:200] if track_response else 'n/a'}")
        return None
    if not track_data:
        logger.warning(f"get_soundcloud_stream_url called for track {track_id} but track_data is missing/empty after fetch attempt.")
        return None
    progressive_stream_info_url: Optional[str] = None
    hls_stream_info_url: Optional[str] = None
    transcodings = track_data.get('media', {}).get('transcodings', [])
    
    # логируем доступные форматы
    if transcodings:
        available_formats = [
            f"{t.get('format', {}).get('protocol', 'unknown')}:{t.get('preset', 'unknown')}" 
            for t in transcodings
        ]
        logger.debug(f"soundcloud track {track_id}: доступные форматы стрима: {', '.join(available_formats)}")
    else:
        logger.warning(f"soundcloud track {track_id}: transcodings пустой или отсутствует")
    
    # определяем какой формат искать
    use_hls = (preferred_format == 'hls')
    
    if use_hls:
        # ищем лучший hls формат (приоритет: opus > aac > mp3)
        logger.debug(f"soundcloud track {track_id}: запрошен hls формат")
        hls_priority = ['opus', 'aac', 'mp3']
        for preset_type in hls_priority:
            for transcoding in transcodings:
                if transcoding.get('format', {}).get('protocol') == 'hls':
                    preset = transcoding.get('preset', '')
                    if preset_type in preset.lower() and transcoding.get('url'):
                        hls_stream_info_url = transcoding['url']
                        logger.debug(f"soundcloud track {track_id}: используем hls формат {preset}")
                        break
            if hls_stream_info_url:
                break
        
        # fallback: если hls не найден, берем progressive
        if not hls_stream_info_url:
            logger.warning(f"soundcloud track {track_id}: hls не найден, fallback на progressive")
            for transcoding in transcodings:
                if transcoding.get('format', {}).get('protocol') == 'progressive' and transcoding.get('url'):
                    progressive_stream_info_url = transcoding['url']
                    break
    else:
        # ищем progressive (дефолт для обратной совместимости)
        logger.debug(f"soundcloud track {track_id}: запрошен mp3 формат")
        for transcoding in transcodings:
            if transcoding.get('format', {}).get('protocol') == 'progressive' and transcoding.get('url'):
                progressive_stream_info_url = transcoding['url']
                logger.debug(f"soundcloud track {track_id}: используем progressive формат")
                break
        
        # fallback: если progressive не найден, берем hls
        if not progressive_stream_info_url:
            logger.warning(f"soundcloud track {track_id}: progressive не найден, fallback на hls")
            hls_priority = ['mp3', 'aac', 'opus']
            for preset_type in hls_priority:
                for transcoding in transcodings:
                    if transcoding.get('format', {}).get('protocol') == 'hls':
                        preset = transcoding.get('preset', '')
                        if preset_type in preset.lower() and transcoding.get('url'):
                            hls_stream_info_url = transcoding['url']
                            logger.debug(f"soundcloud track {track_id}: используем hls формат {preset}")
                            break
                if hls_stream_info_url:
                    break
    
    stream_info_url = hls_stream_info_url or progressive_stream_info_url
    stream_type = 'hls' if hls_stream_info_url else 'mp3'
    
    if not stream_info_url:
        logger.warning(f"No stream transcoding URL found for SoundCloud track {track_id}.")
        return None
    try:
        final_request_url = stream_info_url
        if 'client_id=' not in final_request_url:
            final_request_url += ('&' if '?' in final_request_url else '?') + f'client_id={client_id}'
        if 'client_id=' not in final_request_url:
            final_request_url += ('&' if '?' in final_request_url else '?') + f'client_id={client_id}'
        stream_response = await client.get(final_request_url, headers=headers, timeout=10.0, follow_redirects=True)
        stream_response.raise_for_status()
        stream_data = stream_response.json()
        final_audio_url = stream_data.get('url')
        if not final_audio_url:
            logger.error(f"❌ final audio url missing in soundcloud stream data for track {track_id}. response: {json.dumps(stream_data, indent=2)}")
            return None

        # определяем тип стрима и проверяем ограничения
        stream_type = 'hls' if hls_stream_info_url else 'mp3'
        
        # проверяем на лимитированный трек (preview)
        is_limited = False
        if track_data:
            full_duration = track_data.get('full_duration', 0)
            duration = track_data.get('duration', 0)
            policy = track_data.get('policy', 'ALLOW')
            
            # трек лимитирован если duration меньше полного или policy ограничивает
            is_limited = (
                (full_duration > 0 and duration < full_duration) or
                policy in ['SNIP', 'BLOCK', 'MONETIZE']
            )
        
        # Apply proxy rewrite (out-of-the-box default + env override)
        # DISABLED: Returning direct URLs as requested
        # proxy_prefix = ...
        
        result = {
            'url': final_audio_url,
            'type': stream_type,
            'source': 'soundcloud'
        }
        
        # добавляем флаг limited если трек ограничен
        if is_limited:
            result['limited'] = True
        
        return result
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Stream URL {track_id}: HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json from final soundcloud stream response for track {track_id}: {e}. response: {stream_response.text[:500] if stream_response else 'n/a'}")
        return None
    except Exception as e:
        logger.error(f"❌ Stream {track_id}: {str(e)[:100]}")
        return None

async def get_soundcloud_similar_tracks(track_id: Union[str, int], limit: int = 20) -> List[Dict[str, Any]]:
    """Gets similar tracks to a SoundCloud track using SoundCloud's own related endpoint.

    This always queries: https://api-v2.soundcloud.com/tracks/{track_id}/related
    and returns formatted tracks limited by `limit`.
    """
    # Ensure numeric ID (strip platform prefix if present)
    if isinstance(track_id, str) and ':' in track_id:
        track_id = track_id.split(':')[-1]

    try:
        params = {'limit': int(limit)}
    except Exception:
        params = {'limit': 20}

    response = await make_soundcloud_request(f'{SOUNDCLOUD_API_V2_URL}/tracks/{track_id}/related', params=params)
    if not response:
        logger.warning(f"❌ could not fetch related tracks for SoundCloud track {track_id}")
        return []

    try:
        response.raise_for_status()
        data = response.json()

        # The related endpoint may return a collection dict or a list
        candidates = []
        if isinstance(data, dict):
            # common shape: {'collection': [...], ...}
            if 'collection' in data and isinstance(data['collection'], list):
                candidates = data['collection']
            # fallback: maybe direct 'tracks'
            elif 'tracks' in data and isinstance(data['tracks'], list):
                candidates = data['tracks']
            else:
                # If dict but not expected shape, try to interpret it as a single track
                # or as a mapping id->track
                # collect any list-valued items
                for v in data.values():
                    if isinstance(v, list):
                        candidates = v
                        break
        elif isinstance(data, list):
            candidates = data

        if not candidates:
            logger.debug(f"soundcloud related: no candidates for track {track_id}. response keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")
            return []

        tracks = []
        for item in candidates:
            formatted = format_soundcloud_track(item)
            if formatted and str(formatted.get('id')) != str(track_id):
                tracks.append(formatted)
            if len(tracks) >= limit:
                break

        return tracks[:limit]
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Related tracks {track_id}: HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json from soundcloud related for track {track_id}: {e}. response: {response.text[:200] if response else 'n/a'}")
        return []
    except Exception as e:
        logger.error(f"❌ Related tracks {track_id}: {str(e)[:100]}")
        return []
# Legacy fallback removed: similar tracks are retrieved directly from the SoundCloud related endpoint

async def get_soundcloud_liked_tracks(username: str) -> Dict[str, Any]:
    """Gets liked tracks for a SoundCloud user with pagination support."""
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('SoundCloud Client ID not found, cannot get liked tracks.')
        return {'name': f'Liked songs of {username}', 'tracks': []}

    # First resolve username to user ID
    try:
        user_data = await resolve_soundcloud_url(f'https://soundcloud.com/{username}')
        if not user_data:
            logger.error(f"❌ could not resolve soundcloud username {username}")
            return {'name': f'Liked songs of {username}', 'tracks': []}
        user_id = user_data.get('id')
        display_name = user_data.get('full_name') or user_data.get('username') or username
        likes_count = user_data.get('likes_count', 0)
        
        if not user_id:
            logger.error(f"❌ could not get user id for soundcloud username {username}")
            return {'name': f'Liked songs of {display_name}', 'tracks': []}
            
        logger.debug(f"soundcloud user {username} ({user_id}): has {likes_count} likes")
    except Exception as e:
        logger.error(f"❌ error resolving soundcloud username {username}: {e}")
        return {'name': f'Liked songs of {username}', 'tracks': []}

    # Now get liked tracks with pagination
    headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    client = await get_async_client()
    tracks = []
    next_href = None
    page_num = 1
    max_pages = 100  # Защита от бесконечного цикла (100 страниц * 50 = 5000 треков макс)
    
    try:
        # Первая страница
        params = {
            'client_id': client_id,
            'app_version': SOUNDCLOUD_APP_VERSION,
            'app_locale': 'en',
            'limit': 50
        }
        
        response = await client.get(
            f'{SOUNDCLOUD_API_V2_URL}/users/{user_id}/likes',
            params=params,
            headers=headers,
            timeout=30.0
        )
        response.raise_for_status()
        favorites_data = response.json()
        
        # Обрабатываем первую страницу
        for like_data in favorites_data.get('collection', []):
            track_data = like_data.get('track')
            if track_data:
                formatted_track = format_soundcloud_track(track_data)
                if formatted_track:
                    tracks.append(formatted_track)
        
        next_href = favorites_data.get('next_href')
        logger.debug(f"soundcloud likes for {username}: loaded page 1, got {len(tracks)} tracks")
        
        # Загружаем оставшиеся страницы если есть
        while next_href and page_num < max_pages:
            page_num += 1
            try:
                # next_href уже содержит client_id
                if 'client_id=' not in next_href:
                    next_href += ('&' if '?' in next_href else '?') + f'client_id={client_id}'
                
                page_response = await client.get(next_href, headers=headers, timeout=30.0)
                page_response.raise_for_status()
                page_data = page_response.json()
                
                page_tracks = 0
                for like_data in page_data.get('collection', []):
                    track_data = like_data.get('track')
                    if track_data:
                        formatted_track = format_soundcloud_track(track_data)
                        if formatted_track:
                            tracks.append(formatted_track)
                            page_tracks += 1
                
                next_href = page_data.get('next_href')
                logger.debug(f"soundcloud likes for {username}: loaded page {page_num}, got {page_tracks} tracks (total: {len(tracks)})")
                
                # Если на странице нет треков, прекращаем
                if page_tracks == 0:
                    break
                    
            except Exception as page_error:
                logger.error(f"❌ error loading page {page_num} of likes for user {username}: {page_error}")
                break
        
        playlist_name = f'Liked songs of {display_name}'
        logger.debug(f"soundcloud likes for {username}: final count = {len(tracks)} tracks")
        return {'name': playlist_name, 'tracks': tracks}
        
    except Exception as e:
        logger.error(f"❌ error getting soundcloud liked tracks for user {username}: {e}", exc_info=True)
        return {'name': f'Liked songs of {display_name}', 'tracks': []}

async def get_soundcloud_track_details(track_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Gets raw details for a single SoundCloud track from the API."""
    # очищаем track_id от префикса platform если он там есть
    if isinstance(track_id, str) and ':' in track_id:
        track_id = track_id.split(':')[-1]
    
    params = {}
    response = await make_soundcloud_request(f'{SOUNDCLOUD_API_V2_URL}/tracks/{track_id}', params=params)
    
    if not response:
        return None
    
    try:
        response.raise_for_status()
        track_data = response.json()
        
        # логируем доступность трека
        streamable = track_data.get('streamable', False)
        public = track_data.get('public', False)
        state = track_data.get('state', 'unknown')
        policy = track_data.get('policy', 'unknown')
        monetization_model = track_data.get('monetization_model', 'unknown')
        full_duration = track_data.get('full_duration', 0)
        duration = track_data.get('duration', 0)
        
        # проверяем на preview трек (разница больше 1 секунды = реальный preview)
        duration_diff_ms = abs(full_duration - duration)
        is_preview = (full_duration > 0 and duration < full_duration and duration_diff_ms > 1000)
        
        logger.debug(f"soundcloud track {track_id}: streamable={streamable}, public={public}, state={state}, policy={policy}, monetization={monetization_model}")
        
        if is_preview:
            logger.warning(f"soundcloud track {track_id}: PREVIEW трек! duration={duration/1000:.1f}s, full={full_duration/1000:.1f}s, причина: {policy}")
        
        if not streamable:
            logger.warning(f"soundcloud track {track_id} помечен как non-streamable")
        
        return track_data
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if hasattr(e, 'response') else 'error'
        # не логируем 404 и 429 (rate limit) - это ожидаемые ошибки
        if status not in [404, 429]:
            logger.warning(f"SC track {track_id}: HTTP {status}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json for soundcloud track details {track_id}: {e}. response: {response.text[:200] if response else 'n/a'}")
        return None
    except Exception as e:
        # не логируем пустые ошибки
        error_msg = str(e)[:100]
        if error_msg:
            logger.debug(f"Track {track_id}: {error_msg}")
        return None

async def search_soundcloud_artists(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Searches artists (users) on SoundCloud."""
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('❌ soundcloud client id not found, cannot search artists.')
        return []
    actual_limit = min(max(1, int(limit)), 50)
    params = {
        'q': query,
        'client_id': client_id,
        'limit': actual_limit,
        'offset': 0,
        'app_version': SOUNDCLOUD_APP_VERSION,
        'app_locale': 'en'
    }
    headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    client = await get_async_client()
    try:
        response = await client.get(f'{SOUNDCLOUD_API_V2_URL}/search/users', params=params, headers=headers, timeout=10.0)
        response.raise_for_status()
        search_data = response.json()
        artists = []
        for item_data in search_data.get('collection', []):
            formatted_artist = {
                'id': str(item_data.get('id', '')),
                'name': item_data.get('username', 'Unknown Artist'),
                'fullName': item_data.get('full_name'),
                'image': _get_safe_artwork_url_soundcloud(item_data.get('avatar_url')),
                'followers': item_data.get('followers_count', 0),
                'trackCount': item_data.get('track_count', 0),
                'source': 'soundcloud',
                'permalinkUrl': item_data.get('permalink_url'),
                'description': item_data.get('description')
            }
            artists.append(formatted_artist)
        return artists
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Search artists '{query[:30]}': HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json from soundcloud artist search for '{query}': {e}. response text: {response.text[:200] if response else 'n/a'}")
        return []
    except Exception as e:
        logger.error(f"❌ Search artists '{query[:30]}': {str(e)[:80]}")
        return []

async def get_soundcloud_artist_details(user_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Gets detailed information for a SoundCloud artist (user)."""
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('❌ soundcloud client id not found, cannot get artist details.')
        return None
    params = {'client_id': client_id, 'app_version': SOUNDCLOUD_APP_VERSION}
    headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    client = await get_async_client()
    try:
        response = await client.get(f'{SOUNDCLOUD_API_V2_URL}/users/{user_id}', params=params, headers=headers, timeout=10.0)
        response.raise_for_status()
        user_data = response.json()
        formatted_artist = {
            'id': str(user_data.get('id', '')),
            'name': user_data.get('username', 'Unknown Artist'),
            'fullName': user_data.get('full_name'),
            'image': _get_safe_artwork_url_soundcloud(user_data.get('avatar_url')),
            'followers': user_data.get('followers_count', 0),
            'trackCount': user_data.get('track_count', 0),
            'playlistCount': user_data.get('playlist_count', 0),
            'source': 'soundcloud',
            'permalinkUrl': user_data.get('permalink_url'),
            'description': user_data.get('description'),
            'city': user_data.get('city'),
            'country': user_data.get('country_code')
        }
        return formatted_artist
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Artist details {user_id}: HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json for soundcloud artist details {user_id}: {e}. response: {response.text[:200] if response else 'n/a'}")
        return None
    except Exception as e:
        logger.error(f"❌ Artist details {user_id}: {str(e)[:100]}")
        return None

async def get_soundcloud_artist_tracks(user_id: Union[str, int], limit: int = 20) -> List[Dict[str, Any]]:
    """Gets tracks for a SoundCloud artist (user)."""
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('❌ soundcloud client id not found, cannot get artist tracks.')
        return []
    actual_limit = min(max(1, int(limit)), 50)
    params = {
        'client_id': client_id,
        'limit': actual_limit,
        'offset': 0,
        'app_version': SOUNDCLOUD_APP_VERSION,
        'app_locale': 'en'
    }
    headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    client = await get_async_client()
    try:
        response = await client.get(f'{SOUNDCLOUD_API_V2_URL}/users/{user_id}/tracks', params=params, headers=headers, timeout=10.0)
        response.raise_for_status()
        tracks_data = response.json()
        tracks = []
        for track_data in tracks_data.get('collection', []):
            formatted_track = format_soundcloud_track(track_data)
            if formatted_track:
                tracks.append(formatted_track)
        return tracks
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Artist tracks {user_id}: HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json for soundcloud artist tracks {user_id}: {e}. response: {response.text[:200] if response else 'n/a'}")
        return []
    except Exception as e:
        logger.error(f"❌ Artist tracks {user_id}: {str(e)[:100]}")
        return []

async def get_soundcloud_artist_albums(user_id: Union[str, int], limit: int = 20) -> List[Dict[str, Any]]:
    """Gets albums (playlists marked as albums) for a SoundCloud artist (user)."""
    client_id = get_soundcloud_client_id()
    if not client_id:
        logger.error('❌ soundcloud client id not found, cannot get artist albums.')
        return []
    actual_limit = min(max(1, int(limit)), 50)
    params = {
        'client_id': client_id,
        'limit': actual_limit,
        'offset': 0,
        'app_version': SOUNDCLOUD_APP_VERSION,
        'app_locale': 'en'
    }
    headers = {'User-Agent': SOUNDCLOUD_USER_AGENT, 'Accept': 'application/json'}
    client = await get_async_client()
    try:
        response = await client.get(f'{SOUNDCLOUD_API_V2_URL}/users/{user_id}/albums', params=params, headers=headers, timeout=10.0)
        response.raise_for_status()
        albums_data = response.json()
        albums = []
        for album_data in albums_data.get('collection', []):
            if album_data.get('is_album') or album_data.get('playlist_type') == 'album':
                formatted_album = {
                    'id': str(album_data.get('id', '')),
                    'title': album_data.get('title', 'Unknown Album'),
                    'artist': album_data.get('user', {}).get('username', 'Unknown Artist'),
                    'coverArt': proxy_cover_url(_get_safe_artwork_url_soundcloud(album_data.get('artwork_url'))),
                    'totalTracks': album_data.get('track_count', 0),
                    'source': 'soundcloud',
                    'permalinkUrl': album_data.get('permalink_url'),
                    'description': album_data.get('description')
                }
                albums.append(formatted_album)
        return albums
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Artist albums {user_id}: HTTP {e.response.status_code if hasattr(e, 'response') else 'error'}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"❌ error decoding json for soundcloud artist albums {user_id}: {e}. response: {response.text[:200] if response else 'n/a'}")
        return []
    except Exception as e:
        logger.error(f"❌ Artist albums {user_id}: {str(e)[:100]}")
        return []
