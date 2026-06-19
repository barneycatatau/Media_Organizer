#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Media Organizer - Undo Script
Este script desfaz as alterações feitas pelo Media Organizer,
removendo os arquivos copiados para o destino.
"""

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
import configparser
import platform
import sys
import time

# Imports para desabilitar lixeira no Windows
if platform.system() == 'Windows':
    try:
        import ctypes
        from ctypes import wintypes
        WINDOWS_DELETE_AVAILABLE = True
    except ImportError:
        WINDOWS_DELETE_AVAILABLE = False
else:
    WINDOWS_DELETE_AVAILABLE = False


class MediaOrganizerUndo:
    def __init__(self, config_file='config.cfg'):
        self.config_file = config_file
        self.config = None
        self.log_file = None
        self.undo_log_file = None
        self.stats = {
            'files_deleted': 0,
            'files_not_found': 0,
            'errors': 0,
            'countries': set(),
            'years': set(),
            'directories_removed': 0
        }
        self.deleted_files = []
        self.not_found_files = []
        self.error_files = []
        self.use_windows_delete = WINDOWS_DELETE_AVAILABLE
        
        # Estatísticas em tempo real
        self.total_files_to_delete = 0
        self.current_file_index = 0
        self.start_time = None
        self.last_update_time = 0
        
    def print_progress(self, force=False):
        """
        Exibe estatísticas em tempo real no console
        Atualiza a mesma linha para não poluir o console
        """
        current_time = time.time()
        
        # Atualizar no máximo a cada 0.1 segundos para não sobrecarregar
        if not force and (current_time - self.last_update_time) < 0.1:
            return
        
        self.last_update_time = current_time
        
        # Calcular estatísticas
        processed = self.current_file_index
        total = self.total_files_to_delete
        remaining = total - processed
        
        if total > 0:
            percentage = (processed / total) * 100
        else:
            percentage = 0
        
        # Calcular tempo decorrido e estimado
        if self.start_time and processed > 0:
            elapsed = current_time - self.start_time
            rate = processed / elapsed  # arquivos por segundo
            if rate > 0:
                eta = remaining / rate  # segundos restantes
                eta_str = self.format_time(eta)
            else:
                eta_str = "calculando..."
            elapsed_str = self.format_time(elapsed)
        else:
            elapsed_str = "0s"
            eta_str = "calculando..."
        
        # Barra de progresso
        bar_length = 40
        filled_length = int(bar_length * processed // total) if total > 0 else 0
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        # Montar linha de status
        status = (
            f"\r┃ Progresso: [{bar}] {percentage:6.2f}% ┃ "
            f"Removidos: {self.stats['files_deleted']:,} ┃ "
            f"Restantes: {remaining:,} ┃ "
            f"Erros: {self.stats['errors']} ┃ "
            f"Tempo: {elapsed_str} ┃ "
            f"ETA: {eta_str} ┃"
        )
        
        # Escrever no console (mesma linha)
        sys.stdout.write(status)
        sys.stdout.flush()
    
    def format_time(self, seconds):
        """Formata segundos em formato legível (HH:MM:SS ou MM:SS ou Xs)"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m{secs:02d}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            return f"{hours}h{minutes:02d}m{secs:02d}s"
        
    def delete_file_windows(self, file_path):
        """
        Deleta arquivo no Windows sem usar lixeira (remoção permanente)
        Usa a API SHFileOperation do Windows para deleção direta
        """
        if not self.use_windows_delete:
            # Fallback para método padrão
            os.remove(file_path)
            return True
        
        try:
            # Estruturas necessárias para SHFileOperation
            class SHFILEOPSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", wintypes.WORD),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", wintypes.LPVOID),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ]
            
            # Constantes do Windows
            FO_DELETE = 0x0003
            FOF_NOCONFIRMATION = 0x0010      # Não pedir confirmação
            FOF_NOERRORUI = 0x0400           # Não mostrar diálogos de erro
            FOF_SILENT = 0x0044              # Não mostrar barra de progresso
            FOF_ALLOWUNDO = 0x0040           # Permitir desfazer (lixeira)
            FOF_NORECURSION = 0x1000         # Não recursivo
            
            # Preparar estrutura
            file_op = SHFILEOPSTRUCT()
            file_op.hwnd = None
            file_op.wFunc = FO_DELETE
            # Adicionar duplo null terminator (necessário para SHFileOperation)
            file_op.pFrom = file_path + '\0'
            file_op.pTo = None
            # Flags: sem confirmação, sem UI de erro, silencioso, SEM lixeira
            file_op.fFlags = FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
            file_op.fAnyOperationsAborted = False
            file_op.hNameMappings = None
            file_op.lpszProgressTitle = None
            
            # Chamar SHFileOperation
            shell32 = ctypes.windll.shell32
            result = shell32.SHFileOperationW(ctypes.byref(file_op))
            
            if result != 0:
                # Se falhar, usar método padrão
                os.remove(file_path)
            
            return True
            
        except Exception as e:
            # Em caso de erro, usar método padrão
            os.remove(file_path)
            return True
        
    def load_config(self):
        """Carrega o arquivo de configuração"""
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"Arquivo de configuração não encontrado: {self.config_file}")
        
        self.config = configparser.ConfigParser()
        self.config.read(self.config_file, encoding='utf-8')
        
        if 'PATHS' not in self.config:
            raise ValueError("Seção [PATHS] não encontrada no arquivo de configuração")
        
        if 'undo' not in self.config['PATHS']:
            raise ValueError("Campo 'undo' não encontrado na seção [PATHS] do arquivo de configuração")
        
        # Verificar se undo está bloqueado
        undo_value = self.config['PATHS']['undo'].strip().lower()
        if undo_value == 'no':
            raise ValueError(
                "\n" + "=" * 80 + "\n" +
                "⛔ EXECUÇÃO BLOQUEADA!\n" +
                "=" * 80 + "\n" +
                "O campo 'undo' está configurado como 'no' no arquivo de configuração.\n" +
                "Para permitir a execução do script de undo, altere o valor para o caminho\n" +
                "do arquivo de log que deseja desfazer.\n" +
                "\nExemplo:\n" +
                "  undo=LOG\\media_organizer_20260131_064337.log\n" +
                "=" * 80
            )
        
        self.log_file = self.config['PATHS']['undo']
        
    def create_undo_log(self):
        """Cria o arquivo de log para o processo de undo"""
        log_dir = Path('LOG')
        log_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.undo_log_file = log_dir / f'undo_{timestamp}.log'
        
        self.log(f"Media Organizer Undo Started")
        self.log(f"Source Log File: {self.log_file}")
        
        # Informar modo de deleção
        if platform.system() == 'Windows' and self.use_windows_delete:
            self.log("Delete Mode: Windows Direct Delete (No Recycle Bin)")
        elif platform.system() == 'Windows':
            self.log("Delete Mode: Standard Delete (may use Recycle Bin)")
        else:
            self.log(f"Delete Mode: Standard Delete ({platform.system()})")
        
        self.log("-" * 80)
        
    def log(self, message):
        """Escreve mensagem no log"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_message = f"[{timestamp}] {message}"
        print(log_message)
        
        if self.undo_log_file:
            with open(self.undo_log_file, 'a', encoding='utf-8') as f:
                f.write(log_message + '\n')
    
    def parse_log_file(self):
        """Lê o arquivo de log e extrai os caminhos dos arquivos copiados"""
        if not os.path.exists(self.log_file):
            raise FileNotFoundError(f"Arquivo de log não encontrado: {self.log_file}")
        
        self.log(f"Parsing log file: {self.log_file}")
        self.log("Scanning log to count files...")
        
        # Padrão para identificar linhas de cópia
        # Formato: [TIMESTAMP] Copied: SOURCE -> DESTINATION
        pattern = r'\[.*?\] Copied: .+ -> (.+)'
        
        copied_files = []
        
        with open(self.log_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                match = re.search(pattern, line)
                if match:
                    destination_file = match.group(1).strip()
                    copied_files.append(destination_file)
        
        self.total_files_to_delete = len(copied_files)
        self.log(f"Found {self.total_files_to_delete:,} files to delete")
        
        return copied_files
    
    def delete_file(self, file_path):
        """Deleta um arquivo permanentemente (sem lixeira no Windows)"""
        try:
            # Incrementar índice do arquivo atual
            self.current_file_index += 1
            
            if os.path.exists(file_path):
                # Deletar arquivo usando método otimizado para Windows
                if platform.system() == 'Windows':
                    self.delete_file_windows(file_path)
                else:
                    os.remove(file_path)
                
                self.log(f"✓ Deleted: {file_path}")
                self.stats['files_deleted'] += 1
                self.deleted_files.append(file_path)
                
                # Extrair informações do caminho
                self.extract_path_info(file_path)
                
                # Atualizar progresso no console
                self.print_progress()
                
                return True
            else:
                self.log(f"✗ Not found: {file_path}")
                self.stats['files_not_found'] += 1
                self.not_found_files.append(file_path)
                
                # Atualizar progresso no console
                self.print_progress()
                
                return False
                
        except Exception as e:
            self.log(f"✗ Error deleting {file_path}: {str(e)}")
            self.stats['errors'] += 1
            self.error_files.append({'path': file_path, 'error': str(e)})
            
            # Atualizar progresso no console
            self.print_progress()
            
            return False
    
    def extract_path_info(self, file_path):
        """Extrai informações do caminho (ano, país)"""
        parts = Path(file_path).parts
        
        # Tentar identificar ano (4 dígitos)
        for part in parts:
            if re.match(r'^\d{4}$', part):
                self.stats['years'].add(part)
        
        # Tentar identificar país (após o ano, antes do arquivo)
        for i, part in enumerate(parts):
            if re.match(r'^\d{4}$', part) and i + 1 < len(parts) - 1:
                country = parts[i + 1]
                if not re.match(r'\.(jpg|jpeg|png|heic|mov|mp4|avi|mkv)$', country, re.IGNORECASE):
                    self.stats['countries'].add(country)
    
    def remove_empty_directories(self, base_path):
        """Remove diretórios vazios após deletar os arquivos"""
        self.log("-" * 80)
        self.log("Removing empty directories...")
        
        removed_dirs = []
        
        # Percorrer todos os diretórios de baixo para cima
        for root, dirs, files in os.walk(base_path, topdown=False):
            for dir_name in dirs:
                dir_path = os.path.join(root, dir_name)
                try:
                    # Verificar se o diretório está vazio
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                        self.log(f"✓ Removed empty directory: {dir_path}")
                        self.stats['directories_removed'] += 1
                        removed_dirs.append(dir_path)
                except Exception as e:
                    self.log(f"✗ Error removing directory {dir_path}: {str(e)}")
        
        return removed_dirs
    
    def format_size(self, size_bytes):
        """Formata o tamanho em bytes para formato legível"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"
    
    def run(self):
        """Executa o processo de undo"""
        try:
            # Carregar configuração
            self.load_config()
            
            # Criar log de undo
            self.create_undo_log()
            
            # Parse do arquivo de log original
            copied_files = self.parse_log_file()
            
            if not copied_files:
                self.log("No files found to delete. Exiting.")
                return
            
            # Deletar arquivos
            self.log("-" * 80)
            self.log("Starting file deletion...")
            self.log("-" * 80)
            
            # Exibir cabeçalho das estatísticas
            print("\n" + "═" * 120)
            print("┃" + " " * 48 + "ESTATÍSTICAS EM TEMPO REAL" + " " * 45 + "┃")
            print("═" * 120)
            
            # Iniciar timer
            self.start_time = time.time()
            
            # Processar arquivos
            for file_path in copied_files:
                self.delete_file(file_path)
            
            # Atualização final do progresso
            self.print_progress(force=True)
            print()  # Nova linha após a barra de progresso
            print("═" * 120)
            
            # Tentar remover diretórios vazios
            if self.config and 'destination_path' in self.config['PATHS']:
                destination_path = self.config['PATHS']['destination_path']
                if os.path.exists(destination_path):
                    self.remove_empty_directories(destination_path)
            
            # Resumo
            self.log("-" * 80)
            self.log("UNDO PROCESS COMPLETED")
            self.log("-" * 80)
            self.log(f"Files deleted: {self.stats['files_deleted']:,}")
            self.log(f"Files not found: {self.stats['files_not_found']:,}")
            self.log(f"Errors: {self.stats['errors']:,}")
            self.log(f"Directories removed: {self.stats['directories_removed']:,}")
            
            if self.start_time:
                total_time = time.time() - self.start_time
                self.log(f"Total execution time: {self.format_time(total_time)}")
            
            if self.stats['years']:
                self.log(f"Years affected: {', '.join(sorted(self.stats['years']))}")
            if self.stats['countries']:
                self.log(f"Countries affected: {', '.join(sorted(self.stats['countries']))}")
            
            # Gerar relatório HTML
            self.generate_html_report()
            
            self.log("-" * 80)
            self.log(f"Undo log saved to: {self.undo_log_file}")
            
        except Exception as e:
            self.log(f"FATAL ERROR: {str(e)}")
            raise
    
    def generate_html_report(self):
        """Gera o relatório HTML com estatísticas"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        html_content = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Media Organizer Undo Report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #ea5455 0%, #f07167 100%);
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
            background: linear-gradient(135deg, #ea5455 0%, #f07167 100%);
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
        
        .action-badge {{
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
            color: #ea5455;
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
            color: #333;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #ea5455;
            font-size: 1.8em;
        }}
        
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }}
        
        .info-item {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
            border-left: 4px solid #ea5455;
        }}
        
        .info-item strong {{
            color: #ea5455;
            display: block;
            margin-bottom: 5px;
        }}
        
        .file-list {{
            max-height: 400px;
            overflow-y: auto;
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin-top: 15px;
        }}
        
        .file-item {{
            padding: 10px;
            margin-bottom: 8px;
            background: white;
            border-radius: 5px;
            font-family: monospace;
            font-size: 0.85em;
            word-break: break-all;
            border-left: 3px solid #ea5455;
        }}
        
        .file-item.not-found {{
            border-left-color: #ffc107;
            opacity: 0.7;
        }}
        
        .file-item.error {{
            border-left-color: #dc3545;
        }}
        
        .footer {{
            background: #f8f9fa;
            padding: 20px;
            text-align: center;
            color: #666;
            font-size: 0.9em;
        }}
        
        .tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 15px;
        }}
        
        .tag {{
            background: #ea5455;
            color: white;
            padding: 8px 15px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }}
        
        .summary-box {{
            background: linear-gradient(135deg, #fff5f5 0%, #ffe5e5 100%);
            padding: 25px;
            border-radius: 15px;
            border: 2px solid #ea5455;
            margin-top: 20px;
        }}
        
        .summary-box h3 {{
            color: #ea5455;
            margin-bottom: 15px;
            font-size: 1.3em;
        }}
        
        .summary-item {{
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid rgba(234, 84, 85, 0.2);
        }}
        
        .summary-item:last-child {{
            border-bottom: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔄 Media Organizer - Undo Report</h1>
            <div class="timestamp">{timestamp}</div>
            <div class="action-badge">ARQUIVOS REMOVIDOS</div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="icon">🗑️</div>
                <div class="number">{self.stats['files_deleted']}</div>
                <div class="label">Arquivos Deletados</div>
            </div>
            
            <div class="stat-card">
                <div class="icon">❌</div>
                <div class="number">{self.stats['files_not_found']}</div>
                <div class="label">Não Encontrados</div>
            </div>
            
            <div class="stat-card">
                <div class="icon">⚠️</div>
                <div class="number">{self.stats['errors']}</div>
                <div class="label">Erros</div>
            </div>
            
            <div class="stat-card">
                <div class="icon">📁</div>
                <div class="number">{self.stats['directories_removed']}</div>
                <div class="label">Diretórios Removidos</div>
            </div>
            
            <div class="stat-card">
                <div class="icon">📅</div>
                <div class="number">{len(self.stats['years'])}</div>
                <div class="label">Anos Afetados</div>
            </div>
            
            <div class="stat-card">
                <div class="icon">🌍</div>
                <div class="number">{len(self.stats['countries'])}</div>
                <div class="label">Países Afetados</div>
            </div>
        </div>
        
        <div class="details">
            <div class="section">
                <h2>📋 Informações do Processo</h2>
                <div class="info-grid">
                    <div class="info-item">
                        <strong>Log de Origem</strong>
                        {self.log_file}
                    </div>
                    <div class="info-item">
                        <strong>Log de Undo</strong>
                        {self.undo_log_file}
                    </div>
                    <div class="info-item">
                        <strong>Arquivo de Configuração</strong>
                        {self.config_file}
                    </div>
                    <div class="info-item">
                        <strong>Data de Execução</strong>
                        {timestamp}
                    </div>
                </div>
                
                <div class="summary-box">
                    <h3>📊 Resumo da Operação</h3>
                    <div class="summary-item">
                        <span>Total de arquivos processados:</span>
                        <strong>{len(self.deleted_files) + len(self.not_found_files) + len(self.error_files)}</strong>
                    </div>
                    <div class="summary-item">
                        <span>Arquivos deletados com sucesso:</span>
                        <strong>{self.stats['files_deleted']}</strong>
                    </div>
                    <div class="summary-item">
                        <span>Taxa de sucesso:</span>
                        <strong>{(self.stats['files_deleted'] / max(1, len(self.deleted_files) + len(self.not_found_files) + len(self.error_files)) * 100):.1f}%</strong>
                    </div>
                </div>
            </div>
"""

        # Anos afetados
        if self.stats['years']:
            html_content += f"""
            <div class="section">
                <h2>📅 Anos Afetados</h2>
                <div class="tags">
"""
            for year in sorted(self.stats['years']):
                html_content += f'                    <div class="tag">{year}</div>\n'
            html_content += """                </div>
            </div>
"""

        # Países afetados
        if self.stats['countries']:
            html_content += f"""
            <div class="section">
                <h2>🌍 Países Afetados</h2>
                <div class="tags">
"""
            for country in sorted(self.stats['countries']):
                html_content += f'                    <div class="tag">{country}</div>\n'
            html_content += """                </div>
            </div>
"""

        # Arquivos não encontrados
        if self.not_found_files:
            html_content += f"""
            <div class="section">
                <h2>⚠️ Arquivos Não Encontrados ({len(self.not_found_files)})</h2>
                <div class="file-list">
"""
            for file_path in self.not_found_files[:50]:  # Limitar a 50 arquivos
                html_content += f'                    <div class="file-item not-found">{file_path}</div>\n'
            
            if len(self.not_found_files) > 50:
                html_content += f'                    <div class="file-item not-found" style="text-align:center; font-weight:bold;">... e mais {len(self.not_found_files) - 50} arquivos</div>\n'
            
            html_content += """                </div>
            </div>
"""

        # Erros
        if self.error_files:
            html_content += f"""
            <div class="section">
                <h2>❌ Erros ({len(self.error_files)})</h2>
                <div class="file-list">
"""
            for error in self.error_files[:50]:  # Limitar a 50 erros
                html_content += f'                    <div class="file-item error"><strong>Arquivo:</strong> {error["path"]}<br><strong>Erro:</strong> {error["error"]}</div>\n'
            
            if len(self.error_files) > 50:
                html_content += f'                    <div class="file-item error" style="text-align:center; font-weight:bold;">... e mais {len(self.error_files) - 50} erros</div>\n'
            
            html_content += """                </div>
            </div>
"""

        html_content += """        </div>
        
        <div class="footer">
            <p>Media Organizer Undo - Relatório gerado automaticamente</p>
            <p>© 2026 - Ferramenta de reversão de organização de mídia</p>
        </div>
    </div>
</body>
</html>
"""

        # Salvar HTML
        html_filename = self.undo_log_file.with_suffix('.html')
        with open(html_filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        self.log(f"HTML report saved to: {html_filename}")


def main():
    print("=" * 80)
    print("MEDIA ORGANIZER - UNDO SCRIPT")
    print("=" * 80)
    print()
    
    undo = MediaOrganizerUndo('config.cfg')
    undo.run()
    
    print()
    print("=" * 80)
    print("UNDO PROCESS FINISHED!")
    print("=" * 80)


if __name__ == "__main__":
    main()