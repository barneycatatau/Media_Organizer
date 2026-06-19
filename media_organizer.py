#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Photo & Video Organizer Script
Organizes photos and videos into year-based folders (and optionally by country) based on metadata
"""
import hashlib
import os
import shutil
import configparser
from datetime import datetime
from pathlib import Path
from typing import Tuple, Dict, List, Optional
import PIL.Image
import PIL.ExifTags
from collections import defaultdict
import subprocess
import json
import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty

# Register HEIC support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False


class MediaOrganizer:
    """Main class for organizing photos and videos by year and optionally by country"""
    
    def __init__(self, config_file: str = 'config.cfg'):
        """Initialize the organizer with configuration"""
        self.config_file = config_file
        self.source_path = None
        self.destination_path = None
        self.gps_enabled = False
        self.verify_before_copy = False
        self.rename_prefix = "no"
        self.rename_counter = 1
        self.rename_lock = threading.Lock()
        self.log_file = None
        self.geocoder = None
        self.location_cache = {}  # Cache for coordinates to avoid repeated API calls
        self.location_cache_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.hash_lock = threading.Lock()
        self.copy_lock = threading.Lock()
        self.gps_cache_db = Path('gps_cache.sqlite')
        self.geocode_queue: "Queue[Dict[str, object]]" = Queue()
        self.geocode_thread = None
        self.stop_geocode_thread = threading.Event()
        self.workers_used = 1
        self.IMAGE_EXTENSIONS: set[str] = set()
        self.VIDEO_EXTENSIONS: set[str] = set()
        self.SUPPORTED_EXTENSIONS: set[str] = set()
        self.heic_requested = False
        self.year_wise = True
        
        # Statistics
        self.stats = {
            'folders_read': 0,
            'files_processed': 0,
            'images_processed': 0,
            'videos_processed': 0,
            'folders_created': set(),
            'files_copied': 0,
            'files_with_gps': 0,
            'files_without_gps': 0,        # nenhuma coordenada GPS encontrada
            'files_gps_failed': 0,         # tinha coordenadas mas o geocoding falhou
            'files_skipped': 0,
            'duplicates_skipped': 0,          # duplicados reais (hash igual)
            'files_renamed': 0,               # mesmo nome, conteúdo diferente
            'identical_files_found': 0,       # arquivos idênticos encontrados no destino
            'skipped_files_list': [],  # Track skipped files with their extensions
            'gps_cache_hits': 0,
            'gps_cache_writes': 0,
            'workers_used': 1,
            'errors': []
        }
        
        # Track files per year/country folder
        self.files_per_location = defaultdict(lambda: defaultdict(list))  # year -> country -> files
        self.files_per_country = defaultdict(int)  # country -> count
        
        self.hash_index = {}  # hash -> destination path
        self.destination_hash_index = {}  # folder -> hash -> destination path(s)

        self._load_config()
        self._setup_logging()
        self._check_ffprobe()
        if self.gps_enabled:
            self._setup_gps_cache()
            self._setup_geocoding()

    def _parse_extension_list(self, raw_extensions: str) -> set[str]:
        """Normalize a comma-separated list of extensions (no dots required)."""
        entries = [entry.strip().lower() for entry in raw_extensions.split(',')]
        normalized = {
            f".{entry}" if entry and not entry.startswith('.') else entry
            for entry in entries
            if entry
        }
        return normalized
    
    def _load_config(self):
        """Load configuration from config.cfg file"""
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"Config file '{self.config_file}' not found!")
        
        config = configparser.ConfigParser()
        config.read(self.config_file, encoding='utf-8')
        
        if 'PATHS' not in config:
            raise ValueError("Config file must contain [PATHS] section")
        
        self.source_path = config['PATHS'].get('source_path', '').strip()
        self.destination_path = config['PATHS'].get('destination_path', '').strip()

        # Tipos de mídia suportados para leitura de metadata:
        # imagens via EXIF (jpg, jpeg, png, heic*) e vídeos via ffprobe (mov, avi, mp4, mkv).
        # Outros formatos podem não expor GPS/DateTime e cair para datas do filesystem.
        raw_image_extensions = config['PATHS'].get('organize_image_extensions', '').strip()
        raw_video_extensions = config['PATHS'].get('organize_video_extensions', '').strip()
        image_extensions = self._parse_extension_list(raw_image_extensions)
        video_extensions = self._parse_extension_list(raw_video_extensions)
        if not image_extensions or not video_extensions:
            raise ValueError(
                "organize_image_extensions and organize_video_extensions must be defined and non-empty in config.cfg"
            )

        self.heic_requested = '.heic' in image_extensions
        self.IMAGE_EXTENSIONS = image_extensions
        self.VIDEO_EXTENSIONS = video_extensions
        if not HEIC_SUPPORTED and '.heic' in self.IMAGE_EXTENSIONS:
            self.IMAGE_EXTENSIONS.remove('.heic')
        self.SUPPORTED_EXTENSIONS = self.IMAGE_EXTENSIONS | self.VIDEO_EXTENSIONS
        
        # Read GPS configuration
        gps_setting = config['PATHS'].get('GPS', 'no').strip().lower()
        self.gps_enabled = gps_setting in ['yes', 'true', '1', 'on']

        verify_setting = config['PATHS'].get('verify_before_copy', 'no').strip().lower()
        self.verify_before_copy = verify_setting in ['yes', 'true', '1', 'on']

        self.rename_prefix = config['PATHS'].get('rename_prefix', 'no').strip()
        if self.rename_prefix.lower() == 'no':
            self.rename_prefix = "no"

        year_wise_setting = config['PATHS'].get('year_wise', 'yes').strip().lower()
        self.year_wise = year_wise_setting in ['yes', 'true', '1', 'on']
        
        if not self.source_path or not self.destination_path:
            raise ValueError("source_path and destination_path must be defined in config.cfg")
        
        if not os.path.exists(self.source_path):
            raise FileNotFoundError(f"Source path does not exist: {self.source_path}")
    
    def _calculate_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash for a file"""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _build_destination_hash_index(self, folder_path: Path) -> Dict[str, List[str]]:
        """Build hash index for existing files in a specific destination folder"""
        folder_key = str(folder_path)
        if folder_key in self.destination_hash_index:
            return self.destination_hash_index[folder_key]

        if not folder_path.exists():
            self.destination_hash_index[folder_key] = {}
            return self.destination_hash_index[folder_key]

        self._log(f"Verifying destination for duplicates: {folder_path}")

        folder_index: Dict[str, List[str]] = {}
        for root, _, files in os.walk(folder_path):
            for filename in files:
                file_path = os.path.join(root, filename)
                try:
                    file_hash = self._calculate_hash(file_path)
                    if file_hash not in folder_index:
                        folder_index[file_hash] = [file_path]
                    else:
                        folder_index[file_hash].append(file_path)
                except Exception as e:
                    self._log(f"  Error hashing destination file {file_path}: {e}")

        self.destination_hash_index[folder_key] = folder_index
        return folder_index

    def _setup_logging(self):
        """Setup logging directory and file"""
        log_dir = Path('LOG')
        log_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_file = log_dir / f'media_organizer_{timestamp}.log'
        
        self._log(f"Media Organizer Started")
        self._log(f"Source Path: {self.source_path}")
        self._log(f"Destination Path: {self.destination_path}")
        self._log(f"GPS Grouping: {'ENABLED' if self.gps_enabled else 'DISABLED'}")
        self._log(f"Year-wise Grouping: {'ENABLED' if self.year_wise else 'DISABLED'}")
        self._log(f"Verify Before Copy: {'ENABLED' if self.verify_before_copy else 'DISABLED'}")
        if self.rename_prefix == "no":
            self._log("Rename Prefix: DISABLED")
        else:
            self._log(f"Rename Prefix: {self.rename_prefix}")
        self._log(f"Supported Images: {', '.join(sorted(self.IMAGE_EXTENSIONS))}")
        self._log(f"Supported Videos: {', '.join(sorted(self.VIDEO_EXTENSIONS))}")
        
        if not HEIC_SUPPORTED and self.heic_requested:
            self._log("WARNING: pillow-heif not found. HEIC files will not be processed.")
            self._log("  Install with: pip install pillow-heif")
        else:
            if self.heic_requested:
                self._log("HEIC support enabled")
        
        self._log("-" * 80)
    
    def _setup_geocoding(self):
        """Setup geocoding service for GPS coordinates"""
        try:
            from geopy.geocoders import Nominatim
            from geopy.exc import GeocoderTimedOut, GeocoderServiceError
            
            self.geocoder = Nominatim(user_agent="media_organizer_v2", timeout=10)
            self._log("Geocoding service initialized (Nominatim/OpenStreetMap)")
            self._log("NOTE: Geocoding requests are rate-limited (1 request/second)")
            
        except ImportError:
            self._log("ERROR: geopy library not found. GPS grouping disabled.")
            self._log("  Install with: pip install geopy")
            self.gps_enabled = False
            self.geocoder = None
    
    def _check_ffprobe(self):
        """Check if ffprobe is available for video metadata extraction"""
        try:
            subprocess.run(['ffprobe', '-version'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL,
                         check=True)
            self.ffprobe_available = True
            self._log("ffprobe detected: Video metadata extraction enabled")
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.ffprobe_available = False
            self._log("WARNING: ffprobe not found. Video dates will use file modification time.")
            self._log("  To enable video metadata extraction, install ffmpeg:")
            self._log("  - Windows: Download from https://ffmpeg.org/download.html")
            self._log("  - Linux: sudo apt-get install ffmpeg")
            self._log("  - macOS: brew install ffmpeg")
    
    def _log(self, message: str):
        """Write message to log file with timestamp"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_message = f"[{timestamp}] {message}\n"

        with self.log_lock:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(log_message)

    def _setup_gps_cache(self):
        """Initialize SQLite cache for GPS geocoding"""
        try:
            with sqlite3.connect(self.gps_cache_db) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS gps_cache (
                        lat REAL NOT NULL,
                        lon REAL NOT NULL,
                        country TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (lat, lon)
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_gps_cache_country ON gps_cache(country);")
            self._log(f"GPS SQLite cache initialized: {self.gps_cache_db}")
        except Exception as e:
            self._log(f"ERROR initializing GPS SQLite cache: {e}")
            self.gps_enabled = False

    def _get_cache_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.gps_cache_db, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _get_country_from_cache(self, lat: float, lon: float) -> Optional[str]:
        """Fetch country from cache (memory -> SQLite)"""
        cache_key = f"{lat:.4f},{lon:.4f}"
        with self.location_cache_lock:
            if cache_key in self.location_cache:
                with self.stats_lock:
                    self.stats['gps_cache_hits'] += 1
                self._log(f"  GPS cache hit (memory): {cache_key}")
                return self.location_cache[cache_key]

        try:
            with self._get_cache_connection() as conn:
                cursor = conn.execute(
                    "SELECT country FROM gps_cache WHERE lat = ? AND lon = ?",
                    (round(lat, 4), round(lon, 4))
                )
                row = cursor.fetchone()
                if row:
                    country = row[0]
                    with self.location_cache_lock:
                        self.location_cache[cache_key] = country
                    with self.stats_lock:
                        self.stats['gps_cache_hits'] += 1
                    self._log(f"  GPS cache hit (SQLite): {cache_key} -> {country}")
                    return country
        except Exception as e:
            self._log(f"  GPS cache read error for {cache_key}: {e}")

        return None

    def _write_country_to_cache(self, lat: float, lon: float, country: str):
        """Persist country to cache (SQLite + memory)"""
        cache_key = f"{lat:.4f},{lon:.4f}"
        try:
            with self._get_cache_connection() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO gps_cache (lat, lon, country, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (round(lat, 4), round(lon, 4), country, datetime.now().isoformat())
                )
            with self.location_cache_lock:
                self.location_cache[cache_key] = country
            with self.stats_lock:
                self.stats['gps_cache_writes'] += 1
            self._log(f"  GPS cache write: {cache_key} -> {country}")
        except Exception as e:
            self._log(f"  GPS cache write error for {cache_key}: {e}")

    def _geocode_worker(self):
        """Serial geocoding worker to respect rate limits"""
        self._log("Geocoding worker thread started")
        while not self.stop_geocode_thread.is_set() or not self.geocode_queue.empty():
            try:
                request = self.geocode_queue.get(timeout=0.2)
            except Empty:
                continue

            lat = request['lat']
            lon = request['lon']
            event = request['event']
            container = request['container']

            country = self._get_country_from_gps(lat, lon)
            if country:
                self._write_country_to_cache(lat, lon, country)
            container['country'] = country
            event.set()
            self.geocode_queue.task_done()

        self._log("Geocoding worker thread stopped")
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format (hours, minutes, seconds)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"
    
    def _get_country_from_gps(self, lat: float, lon: float) -> Optional[str]:
        """Convert GPS coordinates to country name using reverse geocoding"""
        if not self.geocoder:
            return None

        try:
            # Rate limiting: 1 request per second for Nominatim
            time.sleep(1.1)
            
            location = self.geocoder.reverse(f"{lat}, {lon}", language='pt')
            
            if location and location.raw.get('address'):
                country = location.raw['address'].get('country')
                if country:
                    self._log(f"  GPS coordinates ({lat:.6f}, {lon:.6f}) -> {country}")
                    return country
            
            self._log(f"  GPS coordinates ({lat:.6f}, {lon:.6f}) -> Country not found")
            return None
            
        except Exception as e:
            self._log(f"  Geocoding error for ({lat:.6f}, {lon:.6f}): {e}")
            return None
    
    def _convert_gps_to_degrees(self, gps_coords, gps_ref) -> Optional[float]:
        """Convert GPS coordinates from EXIF format to decimal degrees"""
        try:
            if isinstance(gps_coords, (tuple, list)) and len(gps_coords) == 3:
                degrees = float(gps_coords[0])
                minutes = float(gps_coords[1])
                seconds = float(gps_coords[2])
                
                decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
                
                # Apply direction (N/S for latitude, E/W for longitude)
                if gps_ref in ['S', 'W']:
                    decimal = -decimal
                
                return decimal
            return None
        except (ValueError, TypeError, IndexError):
            return None
    
    def _get_gps_from_image(self, image_path: str) -> Optional[Tuple[float, float]]:
        """Extract GPS coordinates from image EXIF data"""
        try:
            image = PIL.Image.open(image_path)
            
            # Get EXIF data - use getexif() which is more reliable
            exif_data = image.getexif()
            
            if not exif_data:
                return None
            
            # Look for GPS info in EXIF
            gps_info = None
            for tag_id in exif_data:
                tag = PIL.ExifTags.TAGS.get(tag_id, tag_id)
                if tag == 'GPSInfo':
                    gps_info = exif_data.get_ifd(tag_id)
                    break
            
            if not gps_info:
                return None
            
            # Extract GPS coordinates using GPS tag IDs
            gps_latitude = gps_info.get(2)  # GPSLatitude
            gps_latitude_ref = gps_info.get(1)  # GPSLatitudeRef (N/S)
            gps_longitude = gps_info.get(4)  # GPSLongitude
            gps_longitude_ref = gps_info.get(3)  # GPSLongitudeRef (E/W)
            
            if gps_latitude and gps_latitude_ref and gps_longitude and gps_longitude_ref:
                lat = self._convert_gps_to_degrees(gps_latitude, gps_latitude_ref)
                lon = self._convert_gps_to_degrees(gps_longitude, gps_longitude_ref)
                
                if lat is not None and lon is not None:
                    self._log(f"  Image GPS found: Lat={lat:.6f}, Lon={lon:.6f}")
                    return (lat, lon)
            
            return None
            
        except Exception as e:
            self._log(f"  Error reading GPS from image {image_path}: {e}")
            return None
    
    def _get_gps_from_video(self, video_path: str) -> Optional[Tuple[float, float]]:
        """Extract GPS coordinates from video metadata using ffprobe"""
        if not self.ffprobe_available:
            return None
        
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                metadata = json.loads(result.stdout)
                
                # Try to get GPS from format tags
                if 'format' in metadata and 'tags' in metadata['format']:
                    tags = metadata['format']['tags']
                    
                    # Look for location string (common in MOV files from phones)
                    # Format examples: "+37.7749-122.4194/" or "37.7749N122.4194W"
                    location = tags.get('location') or tags.get('com.apple.quicktime.location.ISO6709')
                    
                    if location:
                        # Parse ISO 6709 format: +37.7749-122.4194/
                        try:
                            location = location.strip('/')
                            parts = location.replace('+', ' +').replace('-', ' -').split()
                            
                            if len(parts) >= 2:
                                lat = float(parts[0])
                                lon = float(parts[1])
                                self._log(f"  Video GPS found: Lat={lat:.6f}, Lon={lon:.6f}")
                                return (lat, lon)
                        except ValueError:
                            pass
            
            return None
            
        except Exception as e:
            self._log(f"  Error reading GPS from video {video_path}: {e}")
            return None
    
    def _get_gps_coordinates(self, file_path: str) -> Optional[Tuple[float, float]]:
        """Get GPS coordinates for any supported file type"""
        ext = Path(file_path).suffix.lower()
        
        if ext in self.VIDEO_EXTENSIONS:
            return self._get_gps_from_video(file_path)
        elif ext in self.IMAGE_EXTENSIONS:
            return self._get_gps_from_image(file_path)
        else:
            return None
    
    def _get_video_creation_date(self, video_path: str) -> str:
        """
        Video date strategy (espelhada das imagens):

        - DateTimeOriginal (Tirada Em): metadata do vídeo (ffprobe)
        - DateTimeDigitized (Criado Em): criação do arquivo (filesystem)
        - DateTime (Modificado Em): modificação do arquivo (filesystem)

        Usa a data mais antiga encontrada para organização.
        """
        try:
            dt_original = None
            dt_digitized = None
            dt_modified = None

            # -------------------------------------------------
            # 1️⃣ DateTimeOriginal (metadata real do vídeo)
            # -------------------------------------------------
            if self.ffprobe_available:
                try:
                    cmd = [
                        'ffprobe',
                        '-v', 'quiet',
                        '-print_format', 'json',
                        '-show_format',
                        '-show_streams',
                        video_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    metadata = json.loads(result.stdout)

                    # Prioridade de campos confiáveis
                    tags_sources = []

                    # format tags
                    tags_sources.append(metadata.get('format', {}).get('tags', {}))

                    # stream tags (alguns vídeos guardam aqui)
                    for stream in metadata.get('streams', []):
                        if 'tags' in stream:
                            tags_sources.append(stream['tags'])

                    for tags in tags_sources:
                        dt_original = (
                            tags.get('creation_time') or
                            tags.get('com.apple.quicktime.creationdate') or
                            tags.get('date')
                        )
                        if dt_original:
                            break
                except Exception:
                    pass

            # -------------------------------------------------
            # 2️⃣ DateTimeDigitized = criação do arquivo (FS)
            # -------------------------------------------------
            ctime = os.path.getctime(video_path)
            dt_digitized = datetime.fromtimestamp(ctime).strftime('%Y:%m:%d %H:%M:%S')

            # -------------------------------------------------
            # 3️⃣ DateTime (Modificado) = modificação do arquivo (FS)
            # -------------------------------------------------
            mtime = os.path.getmtime(video_path)
            dt_modified = datetime.fromtimestamp(mtime).strftime('%Y:%m:%d %H:%M:%S')

            # Normaliza metadata do vídeo (ISO → EXIF-like)
            if dt_original:
                dt_original = dt_original.replace('-', ':').replace('T', ' ')[:19]

            # -------------------------------------------------
            # 🔍 LOG CLARO
            # -------------------------------------------------
            self._log(f"  Video DateTimeOriginal (Tirada Em): {dt_original or 'NÃO ENCONTRADO'}")
            self._log(f"  DateTimeDigitized (Criado Em - FS): {dt_digitized}")
            self._log(f"  DateTime (Modificado Em - FS): {dt_modified}")

            # -------------------------------------------------
            # Escolha da data (mais antiga)
            # -------------------------------------------------
            dates_found = []

            def extract_year(date_str):
                try:
                    year = date_str.split(':')[0]
                    if year.isdigit():
                        return int(year)
                except Exception:
                    pass
                return None

            if dt_original:
                year = extract_year(dt_original)
                if year:
                    dates_found.append(('DateTimeOriginal (Tirada Em)', year))

            year = extract_year(dt_digitized)
            if year:
                dates_found.append(('DateTimeDigitized (Criado Em - FS)', year))

            year = extract_year(dt_modified)
            if year:
                dates_found.append(('DateTime (Modificado Em - FS)', year))

            if dates_found:
                dates_found.sort(key=lambda x: x[1])
                chosen_field, chosen_year = dates_found[0]
                self._log(f"  👉 Data escolhida: {chosen_year} ({chosen_field})")
                return str(chosen_year)

            # Fallback extremo
            year = datetime.now().strftime('%Y')
            self._log(f"  Nenhuma data válida encontrada, usando ano atual: {year}")
            return year

        except Exception as e:
            mtime = os.path.getmtime(video_path)
            year = datetime.fromtimestamp(mtime).strftime('%Y')
            self._log(f"  Erro ao processar vídeo ({e}), usando mtime: {year}")
            return year
    
    def _get_image_creation_date(self, image_path: str) -> str:
        """
        Image date strategy (definitiva):

        - DateTimeOriginal (Tirada Em): EXIF da câmera
        - DateTimeDigitized (Criado Em): data de CRIAÇÃO do arquivo (filesystem)
        - DateTime (Modificado Em): data de MODIFICAÇÃO do arquivo (filesystem)

        Usa a data mais antiga encontrada para organização.
        """
        try:
            image = PIL.Image.open(image_path)
            exif = image.getexif()

            TAG_DATETIME_ORIGINAL = 0x9003   # DateTimeOriginal
            TAG_EXIF_IFD = 0x8769            # EXIF IFD pointer

            dt_original = None
            dt_digitized = None
            dt_modified = None

            # -------------------------------------------------
            # 1️⃣ DateTimeOriginal (EXIF real da câmera)
            # -------------------------------------------------
            if TAG_EXIF_IFD in exif:
                try:
                    exif_ifd = exif.get_ifd(TAG_EXIF_IFD)
                    if TAG_DATETIME_ORIGINAL in exif_ifd:
                        dt_original = str(exif_ifd.get(TAG_DATETIME_ORIGINAL))
                except Exception:
                    pass

            # -------------------------------------------------
            # 2️⃣ DateTimeDigitized = criação do arquivo (FS)
            # -------------------------------------------------
            ctime = os.path.getctime(image_path)
            dt_digitized = datetime.fromtimestamp(ctime).strftime('%Y:%m:%d %H:%M:%S')

            # -------------------------------------------------
            # 3️⃣ DateTime (Modificado) = modificação do arquivo (FS)
            # -------------------------------------------------
            mtime = os.path.getmtime(image_path)
            dt_modified = datetime.fromtimestamp(mtime).strftime('%Y:%m:%d %H:%M:%S')

            # -------------------------------------------------
            # 🔍 LOG CLARO E CONFIÁVEL
            # -------------------------------------------------
            self._log(f"  EXIF DateTimeOriginal (Tirada Em): {dt_original or 'NÃO ENCONTRADO'}")
            self._log(f"  DateTimeDigitized (Criado Em - FS): {dt_digitized}")
            self._log(f"  DateTime (Modificado Em - FS): {dt_modified}")

            # -------------------------------------------------
            # Escolha da data (mais antiga)
            # -------------------------------------------------
            dates_found = []

            def extract_year(date_str):
                try:
                    year = date_str.split(':')[0]
                    if year.isdigit():
                        return int(year)
                except Exception:
                    pass
                return None

            if dt_original:
                year = extract_year(dt_original)
                if year:
                    dates_found.append(('DateTimeOriginal (Tirada Em)', year))

            year = extract_year(dt_digitized)
            if year:
                dates_found.append(('DateTimeDigitized (Criado Em - FS)', year))

            year = extract_year(dt_modified)
            if year:
                dates_found.append(('DateTime (Modificado Em - FS)', year))

            if dates_found:
                dates_found.sort(key=lambda x: x[1])
                chosen_field, chosen_year = dates_found[0]
                self._log(f"  👉 Data escolhida: {chosen_year} ({chosen_field})")
                return str(chosen_year)

            # Fallback extremo (não deve acontecer)
            year = datetime.now().strftime('%Y')
            self._log(f"  Nenhuma data válida encontrada, usando ano atual: {year}")
            return year

        except Exception as e:
            mtime = os.path.getmtime(image_path)
            year = datetime.fromtimestamp(mtime).strftime('%Y')
            self._log(f"  Erro ao processar datas ({e}), usando mtime: {year}")
            return year
    
    def _get_file_creation_date(self, file_path: str) -> str:
        """Get creation date for any supported file type"""
        ext = Path(file_path).suffix.lower()
        
        if ext in self.VIDEO_EXTENSIONS:
            return self._get_video_creation_date(file_path)
        elif ext in self.IMAGE_EXTENSIONS:
            return self._get_image_creation_date(file_path)
        else:
            mtime = os.path.getmtime(file_path)
            return datetime.fromtimestamp(mtime).strftime('%Y')
    
    def _is_supported_file(self, filename: str) -> bool:
        """Check if file has supported extension"""
        ext = Path(filename).suffix.lower()
        return ext in self.SUPPORTED_EXTENSIONS
    
    def _is_video_file(self, filename: str) -> bool:
        """Check if file is a video"""
        ext = Path(filename).suffix.lower()
        return ext in self.VIDEO_EXTENSIONS
    
    def _resolve_destination_folder(
        self,
        year: str,
        country: Optional[str],
        relative_dir: Optional[Path]
    ) -> Path:
        destination_folder = Path(self.destination_path)

        if self.year_wise:
            if self.gps_enabled and country:
                return destination_folder / year / country
            return destination_folder / year

        if self.gps_enabled and country:
            destination_folder = destination_folder / country

        if relative_dir and relative_dir != Path('.'):
            destination_folder = destination_folder / relative_dir

        return destination_folder

    def _copy_file(
        self,
        source_file: str,
        year: str,
        country: Optional[str] = None,
        relative_dir: Optional[Path] = None
    ) -> Optional[str]:
        """Copy file with hash-based duplicate detection.

        Returns the final destination path on success, or None when the file
        was skipped (duplicate/identical) or an error occurred.
        """
        reservation_made = False
        file_hash = None
        try:
            file_hash = self._calculate_hash(source_file)

            # DUPLICADO REAL (conteúdo idêntico)
            wait_started_at = None
            while True:
                with self.hash_lock:
                    existing_destination = self.hash_index.get(file_hash, "missing")
                    if existing_destination == "missing":
                        self.hash_index[file_hash] = None
                        reservation_made = True
                        break
                    if existing_destination is not None:
                        with self.stats_lock:
                            self.stats['duplicates_skipped'] += 1
                        self._log(f"⛔ DUPLICADO REAL (hash): {source_file}")
                        return
                if wait_started_at is None:
                    wait_started_at = time.monotonic()
                    self._log(f"⏳ Aguardando reserva de hash em progresso: {source_file}")
                time.sleep(0.05)

            # Destination folder
            year_folder = self._resolve_destination_folder(year, country, relative_dir)

            # Verifica duplicidade no destino antes de copiar
            with self.copy_lock:
                if self.verify_before_copy:
                    destination_index = self._build_destination_hash_index(year_folder)
                    if file_hash in destination_index:
                        with self.stats_lock:
                            self.stats['identical_files_found'] += 1
                        existing_paths = destination_index[file_hash]
                        self._log(
                            "🔎 IDENTICO NO DESTINO (hash): "
                            f"{source_file} -> {existing_paths[0]}"
                        )
                        with self.hash_lock:
                            if self.hash_index.get(file_hash) is None:
                                self.hash_index.pop(file_hash, None)
                        return

                if not year_folder.exists():
                    year_folder.mkdir(parents=True, exist_ok=True)
                    with self.stats_lock:
                        self.stats['folders_created'].add(str(year_folder))
                    self._log(f"Created folder: {year_folder}")

                if self.rename_prefix == "no":
                    filename = Path(source_file).name
                    destination_file = year_folder / filename

                    # Mesmo nome, conteúdo diferente → renomear
                    counter = 1
                    renamed = False
                    while destination_file.exists():
                        stem = Path(source_file).stem
                        ext = Path(source_file).suffix
                        destination_file = year_folder / f"{stem}_{counter}{ext}"
                        counter += 1
                        renamed = True

                    if renamed:
                        with self.stats_lock:
                            self.stats['files_renamed'] += 1
                        self._log(f"🔁 RENOMEADO (conteúdo diferente): {destination_file.name}")
                else:
                    ext = Path(source_file).suffix
                    with self.rename_lock:
                        counter = self.rename_counter

                    while True:

                        candidate_name = f"{self.rename_prefix}{counter:04d}{ext}"
                        destination_file = year_folder / candidate_name

                        if destination_file.exists():
                            try:
                                existing_hash = self._calculate_hash(destination_file)
                            except Exception as e:
                                self._log(f"  Error hashing destination file {destination_file}: {e}")
                                existing_hash = None

                            if existing_hash and existing_hash == file_hash:
                                with self.stats_lock:
                                    self.stats['identical_files_found'] += 1
                                self._log(
                                    "🔎 IDENTICO NO DESTINO (hash): "
                                    f"{source_file} -> {destination_file}"
                                )
                                with self.hash_lock:
                                    if self.hash_index.get(file_hash) is None:
                                        self.hash_index.pop(file_hash, None)
                                return

                            counter += 1
                            continue

                        break

                shutil.copy2(source_file, destination_file)
                with self.stats_lock:
                    self.stats['files_copied'] += 1

                with self.hash_lock:
                    self.hash_index[file_hash] = str(destination_file)
                    if self.verify_before_copy:
                        folder_key = str(year_folder)
                        self.destination_hash_index.setdefault(folder_key, {}).setdefault(
                            file_hash,
                            []
                        ).append(str(destination_file))
                    if self.rename_prefix != "no":
                        with self.rename_lock:
                            if counter >= self.rename_counter:
                                self.rename_counter = counter + 1

                country_key = country if country else "Unknown"
                self.files_per_location[year][country_key].append(str(destination_file))
                self.files_per_country[country_key] += 1

                self._log(f"Copied: {source_file} -> {destination_file}")

                return str(destination_file)

        except Exception as e:
            if reservation_made and file_hash is not None:
                with self.hash_lock:
                    if self.hash_index.get(file_hash) is None:
                        self.hash_index.pop(file_hash, None)
            error_msg = f"Error copying {source_file}: {e}"
            self.stats['errors'].append(error_msg)
            self._log(f"ERROR: {error_msg}")
    
    def _scan_directory(self, directory: str) -> List[str]:
        """Recursively scan directory for media files"""
        files_to_process: List[str] = []
        try:
            self._log(f"Scanning directory: {directory}")
            for root, _, files in os.walk(directory):
                with self.stats_lock:
                    self.stats['folders_read'] += 1

                for filename in files:
                    item_path = os.path.join(root, filename)
                    ext = Path(item_path).suffix.lower()

                    if self._is_supported_file(filename):
                        files_to_process.append(item_path)
                    else:
                        if ext:
                            with self.stats_lock:
                                self.stats['files_skipped'] += 1
                                self.stats['skipped_files_list'].append({
                                    'path': item_path,
                                    'extension': ext,
                                    'filename': Path(item_path).name
                                })
                            self._log(f"  Skipped (unsupported extension): {item_path}")
        except PermissionError:
            error_msg = f"Permission denied: {directory}"
            with self.stats_lock:
                self.stats['errors'].append(error_msg)
            self._log(f"ERROR: {error_msg}")
        except Exception as e:
            error_msg = f"Error scanning {directory}: {e}"
            with self.stats_lock:
                self.stats['errors'].append(error_msg)
            self._log(f"ERROR: {error_msg}")

        return files_to_process

    def _process_file(self, item_path: str):
        """Process a single file: metadata, GPS, and copy"""
        file_type = "video" if self._is_video_file(item_path) else "image"
        self._log(f"  Found {file_type}: {item_path}")
        with self.stats_lock:
            self.stats['files_processed'] += 1
            if self._is_video_file(item_path):
                self.stats['videos_processed'] += 1
            else:
                self.stats['images_processed'] += 1

        year = self._get_file_creation_date(item_path)

        country = None
        geocode_failed = False
        if self.gps_enabled:
            gps_coords = self._get_gps_coordinates(item_path)
            if gps_coords:
                lat, lon = gps_coords
                cached_country = self._get_country_from_cache(lat, lon)
                if cached_country:
                    country = cached_country
                else:
                    event = threading.Event()
                    container: Dict[str, Optional[str]] = {'country': None}
                    self.geocode_queue.put({
                        'lat': lat,
                        'lon': lon,
                        'event': event,
                        'container': container
                    })
                    event.wait()
                    country = container['country']

                with self.stats_lock:
                    if country:
                        self.stats['files_with_gps'] += 1
                    else:
                        # Tinha coordenadas, mas o geocoding não retornou país
                        self.stats['files_gps_failed'] += 1
                        geocode_failed = True
            else:
                # Nenhuma coordenada GPS encontrada no arquivo
                with self.stats_lock:
                    self.stats['files_without_gps'] += 1
        else:
            with self.stats_lock:
                self.stats['files_without_gps'] += 1

        relative_dir = Path(os.path.relpath(item_path, self.source_path)).parent
        destination_file = self._copy_file(item_path, year, country, relative_dir)

        if geocode_failed:
            dest_display = destination_file if destination_file else "(não copiado)"
            self._log(
                "⚠️ FALHA GPS (geocoding sem país) | "
                f"origem: {item_path} | destino: {dest_display}"
            )

        with self.stats_lock:
            folders_read = self.stats['folders_read']
            files_processed = self.stats['files_processed']
            images_processed = self.stats['images_processed']
            videos_processed = self.stats['videos_processed']
            files_with_gps = self.stats['files_with_gps']
            files_copied = self.stats['files_copied']
            identical_files_found = self.stats['identical_files_found']

        if self.gps_enabled:
            print(f"\rFolders: {folders_read} | "
                  f"Files: {files_processed} "
                  f"(Images: {images_processed}, "
                  f"Videos: {videos_processed}) | "
                  f"GPS: {files_with_gps} | "
                  f"Copied: {files_copied} | "
                  f"Identical: {identical_files_found}", end='', flush=True)
        else:
            print(f"\rFolders: {folders_read} | "
                  f"Files: {files_processed} "
                  f"(Images: {images_processed}, "
                  f"Videos: {videos_processed}) | "
                  f"Copied: {files_copied} | "
                  f"Identical: {identical_files_found}", end='', flush=True)
    
    def run(self):
        """Main execution method"""
        print("=" * 80)
        print("Photo & Video Organizer - Starting...")
        if self.gps_enabled:
            print("GPS Grouping: ENABLED")
        print("=" * 80)
        print()

        start_time = datetime.now()
        self._log("Starting directory scan...")

        self.workers_used = min(32, os.cpu_count() or 1)
        with self.stats_lock:
            self.stats['workers_used'] = self.workers_used
        self._log(f"Workers in use: {self.workers_used}")
        print(f"Workers in use: {self.workers_used}")

        # -------------------------------------------------
        # Scan and organize (progresso acontece aqui)
        # -------------------------------------------------
        files_to_process = self._scan_directory(self.source_path)

        if self.gps_enabled:
            self.geocode_thread = threading.Thread(target=self._geocode_worker, daemon=True)
            self.geocode_thread.start()

        with ThreadPoolExecutor(max_workers=self.workers_used) as executor:
            executor.map(self._process_file, files_to_process)

        if self.gps_enabled:
            self.geocode_queue.join()
            self.stop_geocode_thread.set()
            if self.geocode_thread:
                self.geocode_thread.join()

        print()  # New line after progress
        print()
        print("=" * 80)
        print("Processing Complete!")
        print("=" * 80)

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        self._log("-" * 80)
        self._log(f"Processing completed in {duration:.2f} seconds")
        self._log(f"Total folders read: {self.stats['folders_read']}")
        self._log(f"Total files processed: {self.stats['files_processed']}")
        self._log(f"  - Images: {self.stats['images_processed']}")
        self._log(f"  - Videos: {self.stats['videos_processed']}")
        self._log(f"Total files copied: {self.stats['files_copied']}")
        self._log(f"Identical files found in destination: {self.stats['identical_files_found']}")
        self._log(f"Total folders created: {len(self.stats['folders_created'])}")
        self._log(f"Workers used: {self.stats['workers_used']}")
        self._log(f"GPS cache hits: {self.stats['gps_cache_hits']}")
        self._log(f"GPS cache writes: {self.stats['gps_cache_writes']}")

        if self.gps_enabled:
            self._log(f"Files with GPS data: {self.stats['files_with_gps']}")
            self._log(f"Files without GPS data: {self.stats['files_without_gps']}")
            self._log(f"Files with GPS but geocoding failed: {self.stats['files_gps_failed']}")

        if self.stats['errors']:
            self._log(f"Total errors: {len(self.stats['errors'])}")

        # -------------------------------------------------
        # Calculate files per minute
        # -------------------------------------------------
        files_per_minute = 0.0
        if duration > 0:
            files_per_minute = (self.stats['files_processed'] / duration) * 60

        # -------------------------------------------------
        # HTML Report
        # -------------------------------------------------
        self._generate_html_report(duration, files_per_minute)

        print(f"\nLog file created: {self.log_file}")
        print(f"HTML report created: {self.log_file.with_suffix('.html')}")

    
    def _generate_html_report(self, duration: float, files_per_minute: float):
        """Generate elegant HTML report with statistics"""
        html_file = self.log_file.with_suffix('.html')
        
        # Sort years and countries
        sorted_years = sorted(self.files_per_location.keys(), reverse=True)
        sorted_countries = sorted(self.files_per_country.items(), key=lambda x: x[1], reverse=True)
        
        html_content = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Media Organizer Report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}
        
        .header .timestamp {{
            font-size: 1.1em;
            opacity: 0.9;
        }}
        
        .gps-badge {{
            display: inline-block;
            margin-top: 10px;
            padding: 8px 20px;
            background: rgba(255,255,255,0.2);
            border-radius: 20px;
            font-weight: 600;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
            padding: 40px;
            background: #f8f9fa;
        }}
        
        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            text-align: center;
        }}
        
        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0,0,0,0.15);
        }}
        
        .stat-card .icon {{
            font-size: 2.5em;
            margin-bottom: 10px;
        }}
        
        .stat-card .number {{
            font-size: 2.5em;
            font-weight: bold;
            color: #667eea;
            margin-bottom: 10px;
        }}
        
        .stat-card .label {{
            font-size: 0.9em;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        
        .details {{
            padding: 40px;
        }}
        
        .section {{
            margin-bottom: 40px;
        }}
        
        .section h2 {{
            font-size: 1.8em;
            color: #333;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #667eea;
        }}
        
        .country-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        
        .country-card {{
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            padding: 20px;
            border-radius: 10px;
            border-left: 5px solid #667eea;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .country-name {{
            font-size: 1.2em;
            font-weight: 600;
            color: #333;
        }}
        
        .country-count {{
            font-size: 1.5em;
            font-weight: bold;
            color: #667eea;
        }}
        
        .year-folder {{
            background: #f8f9fa;
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 10px;
            border-left: 5px solid #667eea;
        }}
        
        .year-folder h3 {{
            color: #667eea;
            font-size: 1.4em;
            margin-bottom: 15px;
        }}
        
        .country-subfolder {{
            background: white;
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 8px;
            border-left: 3px solid #764ba2;
        }}
        
        .country-subfolder h4 {{
            color: #764ba2;
            font-size: 1.1em;
            margin-bottom: 10px;
        }}
        
        .path {{
            font-family: 'Courier New', monospace;
            color: #666;
            margin-bottom: 10px;
            word-break: break-all;
            font-size: 0.9em;
        }}
        
        .file-count {{
            color: #888;
            font-weight: 600;
            margin-bottom: 10px;
        }}
        
        .file-list {{
            max-height: 200px;
            overflow-y: auto;
            background: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            margin-top: 10px;
            font-family: 'Courier New', monospace;
            font-size: 0.85em;
        }}
        
        .file-list div {{
            padding: 5px 0;
            border-bottom: 1px solid #e9ecef;
        }}
        
        .file-list div:last-child {{
            border-bottom: none;
        }}
        
        .file-video {{
            color: #c2185b;
        }}
        
        .file-image {{
            color: #1976d2;
        }}
        
        .error-section {{
            background: #fff3cd;
            border-left: 5px solid #ffc107;
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
        }}
        
        .error-section h3 {{
            color: #856404;
            margin-bottom: 10px;
        }}
        
        .error-list {{
            color: #856404;
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
        }}
        
        .footer {{
            background: #333;
            color: white;
            padding: 20px;
            text-align: center;
        }}
        
        .warning-box {{
            background: #fff3cd;
            border-left: 5px solid #ffc107;
            padding: 15px;
            margin: 20px 40px;
            border-radius: 5px;
        }}
        
        .warning-box h3 {{
            color: #856404;
            margin-bottom: 10px;
        }}
        
        .warning-box p {{
            color: #856404;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📸🎥 Media Organizer Report</h1>
            <div class="timestamp">{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</div>
"""
        
        if self.gps_enabled:
            html_content += """            <div class="gps-badge">🌍 GPS Grouping Enabled</div>
"""
        
        html_content += """        </div>
"""
        
        # Add warning if ffprobe not available
        if not self.ffprobe_available:
            html_content += """
        <div class="warning-box">
            <h3>⚠️ Aviso: ffprobe não encontrado</h3>
            <p>As datas dos vídeos foram extraídas do timestamp de modificação do arquivo.</p>
            <p>Para extrair metadados precisos dos vídeos, instale o ffmpeg.</p>
        </div>
"""
        
        # Statistics cards
        stats_cards = f"""        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="icon">📁</div>
                <div class="number">{self.stats['folders_read']}</div>
                <div class="label">Pastas Lidas</div>
            </div>
            <div class="stat-card">
                <div class="icon">📄</div>
                <div class="number">{self.stats['files_processed']}</div>
                <div class="label">Arquivos Processados</div>
            </div>
            <div class="stat-card">
                <div class="icon">📸</div>
                <div class="number">{self.stats['images_processed']}</div>
                <div class="label">Imagens</div>
            </div>
            <div class="stat-card">
                <div class="icon">🎥</div>
                <div class="number">{self.stats['videos_processed']}</div>
                <div class="label">Vídeos</div>
            </div>
            <div class="stat-card">
                <div class="icon">⛔</div>
                <div class="number">{self.stats['duplicates_skipped']}</div>
                <div class="label">Duplicados Reais (Hash)</div>
            </div>
            <div class="stat-card">
                <div class="icon">🔎</div>
                <div class="number">{self.stats['identical_files_found']}</div>
                <div class="label">Idênticos no Destino</div>
            </div>
            <div class="stat-card">
                <div class="icon">🔁</div>
                <div class="number">{self.stats['files_renamed']}</div>
                <div class="label">Arquivos Renomeados</div>
            </div>
            <div class="stat-card">
                <div class="icon">⚡</div>
                <div class="number">{files_per_minute:.2f}</div>
                <div class="label">Arquivos / Minuto</div>
            </div>
            <div class="stat-card">
                <div class="icon">🧵</div>
                <div class="number">{self.stats['workers_used']}</div>
                <div class="label">Workers Utilizados</div>
            </div>
            <div class="stat-card">
                <div class="icon">🗄️</div>
                <div class="number">{self.stats['gps_cache_hits']}</div>
                <div class="label">GPS do Cache</div>
            </div>
            <div class="stat-card">
                <div class="icon">💾</div>
                <div class="number">{self.stats['gps_cache_writes']}</div>
                <div class="label">GPS Gravados no SQLite</div>
            </div>
"""
        
        if self.gps_enabled:
            stats_cards += f"""            <div class="stat-card">
                <div class="icon">🌍</div>
                <div class="number">{self.stats['files_with_gps']}</div>
                <div class="label">Com GPS</div>
            </div>
            <div class="stat-card">
                <div class="icon">❓</div>
                <div class="number">{self.stats['files_without_gps']}</div>
                <div class="label">Sem GPS</div>
            </div>
            <div class="stat-card">
                <div class="icon">📍</div>
                <div class="number">{self.stats['files_gps_failed']}</div>
                <div class="label">Falha GPS</div>
            </div>
"""
        
        stats_cards += f"""            <div class="stat-card">
                <div class="icon">📂</div>
                <div class="number">{len(self.stats['folders_created'])}</div>
                <div class="label">Pastas Criadas</div>
            </div>
            <div class="stat-card">
                <div class="icon">✅</div>
                <div class="number">{self.stats['files_copied']}</div>
                <div class="label">Arquivos Copiados</div>
            </div>
            <div class="stat-card">
                <div class="icon">🚫</div>
                <div class="number">{self.stats['files_skipped']}</div>
                <div class="label">Arquivos Ignorados</div>
            </div>
            <div class="stat-card">
                <div class="icon">⏱️</div>
                <div class="number">{self._format_duration(duration)}</div>
                <div class="label">Tempo de Execução</div>
            </div>
        </div>
"""
        
        html_content += stats_cards
        
        # Country statistics (if GPS enabled)
        if self.gps_enabled and sorted_countries:
            html_content += """
        <div class="details">
            <div class="section">
                <h2>🌍 Arquivos por País</h2>
                <div class="country-stats">
"""
            
            for country, count in sorted_countries:
                flag = "🌍" if country == "Unknown" else "🏳️"
                country_display = "Sem GPS" if country == "Unknown" else country
                html_content += f"""                    <div class="country-card">
                        <div class="country-name">{flag} {country_display}</div>
                        <div class="country-count">{count}</div>
                    </div>
"""
            
            html_content += """                </div>
            </div>
"""
        
        # Year folders details
        html_content += """
            <div class="section">
                <h2>📁 Estrutura de Pastas por Ano</h2>
"""
        
        for year in sorted_years:
            countries_in_year = self.files_per_location[year]
            
            html_content += f"""
                <div class="year-folder">
                    <h3>📅 Ano {year}</h3>
"""
            
            # Sort countries for this year
            sorted_countries_year = sorted(countries_in_year.items(), 
                                          key=lambda x: (x[0] == "Unknown", x[0]))
            
            for country, files in sorted_countries_year:
                if self.gps_enabled:
                    folder_path = Path(self.destination_path) / year / country
                    country_display = "Sem GPS" if country == "Unknown" else country
                else:
                    folder_path = Path(self.destination_path) / year
                    country_display = ""
                
                file_count = len(files)
                
                # Count file types
                images_count = sum(1 for f in files if Path(f).suffix.lower() in self.IMAGE_EXTENSIONS)
                videos_count = sum(1 for f in files if Path(f).suffix.lower() in self.VIDEO_EXTENSIONS)
                
                if self.gps_enabled:
                    html_content += f"""
                    <div class="country-subfolder">
                        <h4>{"❓ " if country == "Unknown" else "🏳️ "}{country_display}</h4>
                        <div class="path">📂 {folder_path}</div>
                        <div class="file-count">
                            📸 {images_count} imagens | 🎥 {videos_count} vídeos | <strong>Total: {file_count}</strong>
                        </div>
                        <div class="file-list">
"""
                else:
                    html_content += f"""
                    <div class="path">📂 {folder_path}</div>
                    <div class="file-count">
                        📸 {images_count} imagens | 🎥 {videos_count} vídeos | <strong>Total: {file_count}</strong>
                    </div>
                    <div class="file-list">
"""
                
                for file_path in sorted(files)[:50]:  # Limit to 50 files per folder for HTML size
                    filename = Path(file_path).name
                    ext = Path(file_path).suffix.lower()
                    file_class = 'file-video' if ext in self.VIDEO_EXTENSIONS else 'file-image'
                    icon = '🎥' if ext in self.VIDEO_EXTENSIONS else '📸'
                    html_content += f"                            <div class='{file_class}'>{icon} {filename}</div>\n"
                
                if len(files) > 50:
                    html_content += f"                            <div style='color: #888; font-style: italic;'>... e mais {len(files) - 50} arquivos</div>\n"
                
                if self.gps_enabled:
                    html_content += """                        </div>
                    </div>
"""
                else:
                    html_content += """                    </div>
"""
            
            html_content += """                </div>
"""
        
        # Add skipped files section if any
        if self.stats['files_skipped'] > 0:
            # Group skipped files by extension
            skipped_by_ext = defaultdict(list)
            for file_info in self.stats['skipped_files_list']:
                skipped_by_ext[file_info['extension']].append(file_info)
            
            html_content += """
            <div class="section">
                <h2>🚫 Arquivos Ignorados (Extensões Não Suportadas)</h2>
                <div style="background: #f8f9fa; padding: 20px; border-radius: 10px; margin-bottom: 20px;">
                    <p style="color: #666; margin-bottom: 15px;">
                        Estes arquivos foram encontrados mas não processados pois suas extensões não estão na lista de formatos suportados.
                    </p>
                    <p style="color: #666; font-weight: 600;">
                        Total de arquivos ignorados: """ + str(self.stats['files_skipped']) + """
                    </p>
                </div>
"""
            
            # Show files grouped by extension
            for ext in sorted(skipped_by_ext.keys()):
                files = skipped_by_ext[ext]
                html_content += f"""
                <div style="background: white; padding: 15px; margin-bottom: 10px; border-radius: 8px; border-left: 3px solid #ffc107;">
                    <h4 style="color: #856404; margin-bottom: 10px;">
                        Extensão: {ext.upper()} ({len(files)} arquivo{'s' if len(files) > 1 else ''})
                    </h4>
                    <div class="file-list">
"""
                
                # Limit to first 100 files per extension
                for file_info in files[:100]:
                    html_content += f"                        <div style='color: #856404;'>📄 {file_info['path']}</div>\n"
                
                if len(files) > 100:
                    html_content += f"                        <div style='color: #888; font-style: italic;'>... e mais {len(files) - 100} arquivos</div>\n"
                
                html_content += """                    </div>
                </div>
"""
            
            html_content += """            </div>
"""
        
        # Add errors if any
        if self.stats['errors']:
            html_content += """
            <div class="section">
                <div class="error-section">
                    <h3>⚠️ Erros Encontrados</h3>
                    <div class="error-list">
"""
            for error in self.stats['errors']:
                html_content += f"                        <div>{error}</div>\n"
            
            html_content += """                    </div>
                </div>
            </div>
"""
        
        html_content += """
        </div>
"""
        
        html_content += f"""
        <div class="footer">
            <p>Media Organizer v3.0 | Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}</p>
        </div>
    </div>
</body>
</html>
"""
        
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)


if __name__ == "__main__":
    try:
        organizer = MediaOrganizer()
        organizer.run()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
