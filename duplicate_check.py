#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Duplicate Check Script
Scans destination folders for duplicate media files within the same folder using hashes.
"""
import configparser
import hashlib
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple


@dataclass(frozen=True)
class FileHashResult:
    folder: str
    file_path: str
    file_hash: str
    md5_hash: str
    ctime: float
    size: int


class DuplicateChecker:
    """Checks for duplicate media files within the same folder."""

    def __init__(self, config_file: str = 'config.cfg'):
        self.config_file = config_file
        self.source_path: Optional[str] = None
        self.destination_path: Optional[str] = None
        self.move_duplicate_to_path: str = 'no'
        self.image_extensions: set[str] = set()
        self.video_extensions: set[str] = set()
        self.workers_used = 1

        self.stats_lock = threading.Lock()
        self.log_lock = threading.Lock()

        self.stats = {
            'folders_read': 0,
            'files_processed': 0,
            'unique_files': 0,
            'duplicate_files': 0,
            'files_moved': 0,
            'files_deleted': 0,
            'errors': [],
            'workers_used': 1,
            'start_time': None,
            'end_time': None,
        }

        self._load_config()
        self._setup_logging()

    def _load_config(self) -> None:
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"Config file '{self.config_file}' not found!")

        config = configparser.ConfigParser()
        config.read(self.config_file, encoding='utf-8')

        if 'PATHS' not in config:
            raise ValueError("Config file must contain [PATHS] section")

        self.source_path = config['PATHS'].get('source_path', '').strip()
        self.destination_path = config['PATHS'].get('destination_path', '').strip()
        self.move_duplicate_to_path = config['PATHS'].get('move_duplicate_to_path', 'no').strip()
        image_exts = config['PATHS'].get('image_extensions', '').strip()
        video_exts = config['PATHS'].get('video_extensions', '').strip()
        if not video_exts:
            video_exts = config['PATHS'].get('video_extensons', '').strip()

        self.image_extensions = self._parse_extensions(image_exts)
        self.video_extensions = self._parse_extensions(video_exts)

        if not self.image_extensions and not self.video_extensions:
            raise ValueError(
                "image_extensions/video_extensions must be defined in config.cfg; both are empty."
            )

        if not self.source_path:
            raise ValueError("source_path must be defined in config.cfg")

        if not os.path.exists(self.source_path):
            raise FileNotFoundError(f"Source path does not exist: {self.source_path}")

    def _setup_logging(self) -> None:
        log_dir = Path('LOG')
        log_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_file = log_dir / f'duplicate_check_{timestamp}.log'

        self._log("Duplicate Check Started")
        self._log(f"Source Path: {self.source_path}")
        if self.move_duplicate_to_path.lower() == 'no':
            self._log("Duplicate action: DELETE")
        else:
            self._log(f"Duplicate action: MOVE to {self.move_duplicate_to_path}")
        self._log(f"Supported Images: {', '.join(sorted(self.image_extensions))}")
        self._log(f"Supported Videos: {', '.join(sorted(self.video_extensions))}")
        self._log("Tie-breaker: oldest creation time; if equal, choose lexicographically smallest name.")
        self._log("-" * 80)

    def _parse_extensions(self, extensions: str) -> set[str]:
        parsed: set[str] = set()
        for item in extensions.split(','):
            cleaned = item.strip().lower()
            if cleaned:
                if not cleaned.startswith('.'):
                    cleaned = f'.{cleaned}'
                parsed.add(cleaned)
        return parsed

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self.log_lock:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {message}\n")

    def _calculate_hashes(self, file_path: str) -> Tuple[str, str]:
        sha256 = hashlib.sha256()
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                sha256.update(chunk)
                md5.update(chunk)
        return sha256.hexdigest(), md5.hexdigest()

    def _iter_folders(self, root_path: str) -> Iterator[Tuple[str, List[str]]]:
        supported_extensions = self.image_extensions | self.video_extensions

        for root, _, files in os.walk(root_path):
            with self.stats_lock:
                self.stats['folders_read'] += 1
            files_to_process: List[str] = []
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext in supported_extensions:
                    files_to_process.append(os.path.join(root, filename))
            if files_to_process:
                yield root, files_to_process

    def _hash_worker(self, file_path: str) -> Optional[FileHashResult]:
        try:
            sha256_hash, md5_hash = self._calculate_hashes(file_path)
            ctime = os.path.getctime(file_path)
            size = os.path.getsize(file_path)
            folder = str(Path(file_path).parent)

            with self.stats_lock:
                self.stats['files_processed'] += 1
            self._print_progress()

            return FileHashResult(
                folder=folder,
                file_path=file_path,
                file_hash=sha256_hash,
                md5_hash=md5_hash,
                ctime=ctime,
                size=size,
            )
        except Exception as exc:
            with self.stats_lock:
                self.stats['errors'].append(f"Error hashing {file_path}: {exc}")
                self.stats['files_processed'] += 1
            self._print_progress()
            return None

    def _print_progress(self) -> None:
        with self.stats_lock:
            processed = self.stats['files_processed']
            unique_files = self.stats['unique_files']
            duplicates = self.stats['duplicate_files']
        print(
            f"\rFiles processed: {processed} | Unique: {unique_files} | Duplicates: {duplicates}",
            end='',
            flush=True,
        )

    def _select_keep_file(self, results: List[FileHashResult]) -> FileHashResult:
        results_sorted = sorted(
            results,
            key=lambda item: (item.ctime, os.path.basename(item.file_path).lower()),
        )
        return results_sorted[0]

    def _resolve_move_path(self, file_path: str) -> Path:
        move_root = Path(self.move_duplicate_to_path)
        source_root = Path(self.source_path or '')
        try:
            relative_path = Path(file_path).relative_to(source_root)
        except ValueError:
            relative_path = Path(os.path.basename(file_path))
        target_path = move_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if target_path.exists():
            stem = target_path.stem
            suffix = target_path.suffix
            counter = 1
            while True:
                candidate = target_path.with_name(f"{stem}_dup{counter}{suffix}")
                if not candidate.exists():
                    target_path = candidate
                    break
                counter += 1
        return target_path

    def _handle_duplicate(self, file_path: str) -> Optional[Path]:
        if self.move_duplicate_to_path.lower() == 'no':
            os.remove(file_path)
            with self.stats_lock:
                self.stats['files_deleted'] += 1
            return None
        else:
            target_path = self._resolve_move_path(file_path)
            shutil.move(file_path, target_path)
            with self.stats_lock:
                self.stats['files_moved'] += 1
            return target_path

    def _log_duplicate_group(
        self,
        keep_item: FileHashResult,
        duplicates: List[FileHashResult],
    ) -> None:
        self._log("Duplicate group detected")
        self._log(f"Original kept: {keep_item.file_path}")
        for duplicate_item in duplicates:
            self._log(f"Duplicate found: {duplicate_item.file_path}")

    def _format_duration(self, seconds: float) -> str:
        total_seconds = max(0, int(round(seconds)))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        remaining_seconds = total_seconds % 60

        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}min")
        if remaining_seconds or not parts:
            parts.append(f"{remaining_seconds}s")

        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return " e ".join(parts)
        return ", ".join(parts[:-1]) + f" e {parts[-1]}"

    def run(self) -> None:
        print("=" * 80)
        print("Duplicate Check - Starting...")
        print("=" * 80)
        print()

        start_time = datetime.now()
        with self.stats_lock:
            self.stats['start_time'] = start_time

        self.workers_used = min(32, os.cpu_count() or 1)
        with self.stats_lock:
            self.stats['workers_used'] = self.workers_used
        self._log(f"Workers in use: {self.workers_used}")
        print(f"Workers in use: {self.workers_used}")

        for folder_path, files_to_process in self._iter_folders(self.source_path or ''):
            self._log(f"Hashing folder: {folder_path} ({len(files_to_process)} files)")

            results: List[FileHashResult] = []
            with ThreadPoolExecutor(max_workers=self.workers_used) as executor:
                for result in executor.map(self._hash_worker, files_to_process):
                    if result:
                        results.append(result)

            grouped: Dict[str, List[FileHashResult]] = {}
            for item in results:
                grouped.setdefault(item.file_hash, []).append(item)

            for group_items in grouped.values():
                if len(group_items) == 1:
                    with self.stats_lock:
                        self.stats['unique_files'] += 1
                    continue

                keep_item = self._select_keep_file(group_items)
                duplicates = [
                    item for item in group_items if item.file_path != keep_item.file_path
                ]
                duplicates_sorted = sorted(
                    duplicates,
                    key=lambda item: (item.ctime, os.path.basename(item.file_path).lower()),
                )

                with self.stats_lock:
                    self.stats['unique_files'] += 1
                    self.stats['duplicate_files'] += len(duplicates_sorted)

                self._log_duplicate_group(keep_item, duplicates_sorted)

                for duplicate_item in duplicates_sorted:
                    try:
                        target_path = self._handle_duplicate(duplicate_item.file_path)
                        if target_path is None:
                            self._log(f"Action: DELETE | {duplicate_item.file_path}")
                        else:
                            self._log(
                                "Action: MOVE | "
                                f"{duplicate_item.file_path} -> {target_path}"
                            )
                    except Exception as exc:
                        with self.stats_lock:
                            self.stats['errors'].append(
                                f"Error handling duplicate {duplicate_item.file_path}: {exc}"
                            )

                self._print_progress()

        print()
        self._log("Hashing completed.")

        print()
        end_time = datetime.now()
        with self.stats_lock:
            self.stats['end_time'] = end_time

        duration = (end_time - start_time).total_seconds()
        files_per_minute = 0.0
        if duration > 0:
            files_per_minute = (self.stats['files_processed'] / duration) * 60

        self._log("-" * 80)
        self._log(f"Processing completed in {duration:.2f} seconds")
        self._log(f"Total folders read: {self.stats['folders_read']}")
        self._log(f"Total files processed: {self.stats['files_processed']}")
        self._log(f"Unique files: {self.stats['unique_files']}")
        self._log(f"Duplicate files: {self.stats['duplicate_files']}")
        self._log(f"Files moved: {self.stats['files_moved']}")
        self._log(f"Files deleted: {self.stats['files_deleted']}")
        self._log(f"Workers used: {self.stats['workers_used']}")

        if self.stats['errors']:
            self._log(f"Total errors: {len(self.stats['errors'])}")

        self._generate_html_report(duration, files_per_minute)

        print("=" * 80)
        print("Duplicate Check Complete!")
        print("=" * 80)
        print(f"Log file created: {self.log_file}")
        print(f"HTML report created: {self.log_file.with_suffix('.html')}")

    def _generate_html_report(self, duration: float, files_per_minute: float) -> None:
        html_file = self.log_file.with_suffix('.html')
        duplicate_action = 'DELETE' if self.move_duplicate_to_path.lower() == 'no' else 'MOVE'
        formatted_duration = self._format_duration(duration)
        start_timestamp = self.stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')
        end_timestamp = self.stats['end_time'].strftime('%Y-%m-%d %H:%M:%S')

        html_content = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Duplicate Check Report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #43cea2 0%, #185a9d 100%);
            padding: 20px;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 32px;
            margin-bottom: 10px;
        }}

        .content {{
            padding: 30px;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: #f7f9fc;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
            box-shadow: inset 0 0 0 1px #e5eaf2;
        }}

        .stat-card .number {{
            font-size: 28px;
            font-weight: 700;
            color: #11998e;
        }}

        .stat-card .label {{
            margin-top: 8px;
            color: #4a5568;
        }}

        .section {{
            margin-bottom: 30px;
        }}

        .section h2 {{
            font-size: 20px;
            margin-bottom: 10px;
            color: #2d3748;
        }}

        .error-list {{
            background: #fff5f5;
            padding: 15px;
            border-radius: 10px;
            max-height: 250px;
            overflow-y: auto;
            font-family: monospace;
            color: #c53030;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Duplicate Check Report</h1>
            <p>Relatório de varredura de duplicados</p>
        </div>
        <div class="content">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="number">{self.stats['folders_read']}</div>
                    <div class="label">Pastas lidas</div>
                </div>
                <div class="stat-card">
                    <div class="number">{self.stats['files_processed']}</div>
                    <div class="label">Arquivos processados</div>
                </div>
                <div class="stat-card">
                    <div class="number">{self.stats['unique_files']}</div>
                    <div class="label">Arquivos únicos</div>
                </div>
                <div class="stat-card">
                    <div class="number">{self.stats['duplicate_files']}</div>
                    <div class="label">Arquivos duplicados</div>
                </div>
                <div class="stat-card">
                    <div class="number">{self.stats['files_moved']}</div>
                    <div class="label">Arquivos movidos</div>
                </div>
                <div class="stat-card">
                    <div class="number">{self.stats['files_deleted']}</div>
                    <div class="label">Arquivos excluídos</div>
                </div>
                <div class="stat-card">
                    <div class="number">{files_per_minute:.2f}</div>
                    <div class="label">Arquivos por minuto</div>
                </div>
                <div class="stat-card">
                    <div class="number">{self.stats['workers_used']}</div>
                    <div class="label">Workers</div>
                </div>
                <div class="stat-card">
                    <div class="number">{formatted_duration}</div>
                    <div class="label">Tempo total</div>
                </div>
            </div>

            <div class="section">
                <h2>Configuração</h2>
                <p><strong>Origem:</strong> {self.source_path}</p>
                <p><strong>Início:</strong> {start_timestamp}</p>
                <p><strong>Conclusão:</strong> {end_timestamp}</p>
                <p><strong>Ação de duplicados:</strong> {duplicate_action}</p>
                <p><strong>Move path:</strong> {self.move_duplicate_to_path}</p>
                <p><strong>Extensões suportadas:</strong> {', '.join(sorted(self.image_extensions | self.video_extensions))}</p>
                <p><strong>Critério de seleção:</strong> mais antigo; empate por nome (ordem alfabética)</p>
            </div>

            <div class="section">
                <h2>Erros</h2>
                <div class="error-list">
                    {"<br>".join(self.stats['errors']) if self.stats['errors'] else 'Nenhum erro encontrado'}
                </div>
            </div>
        </div>
    </div>
</body>
</html>"""

        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)


if __name__ == '__main__':
    checker = DuplicateChecker()
    checker.run()
