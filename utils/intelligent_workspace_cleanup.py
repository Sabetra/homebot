#!/usr/bin/env python3
"""
Intelligentes Workspace-Cleanup für das RAG/KG-Projekt
Bereinigt temporäre Dateien, Logs, Backups und nicht mehr benötigte Analyse-Skripte
"""

import os
import shutil
import json
from datetime import datetime
from pathlib import Path
from typing import Any
import logging

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class IntelligentWorkspaceCleanup:
    def __init__(self, workspace_path: str) -> None:
        self.workspace_path: Path = Path(workspace_path)
        self.cleanup_report: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "cleanup_summary": {},
            "files_removed": [],
            "folders_removed": [],
            "files_kept": [],
            "errors": []
        }
        
        # Wichtige Dateien und Ordner, die NIEMALS gelöscht werden sollen
        self.protected_files = {
            # Kernfunktionalität
            "gui.py", "agent_toolkit.py", "chatbot_logic.py",
            "Chatbot.ipynb", "Chatbot_optimized.ipynb",
            
            # Konfiguration und Environment
            ".env.datenschutz", "requirements_2025.txt",
            "start_chatbot.bat", "start_dashboard.bat",
            
            # Aktuelle Datenbanken
            "rag_store.db", "pdf_chunks.db", "wellbeing_store.db",
            "wellbeing_store.db.key", "performance_metrics.db",
            
            # Wichtige aktuelle Module
            "bulk_kg_generator.py", "elegant_chunk_monitor.py",
            "show_top_entities.py", "cleanup_384_embeddings.py",
            "analyze_db_corrected.py",
            
            # Dokumentation
            "START_ANLEITUNG.md", "README.md",
        }
        
        self.protected_folders = {
            "agent", "venv_mistral_gguf", "wellbeing",
            "vector_cache", ".vscode", "rag_backups"
        }
        
        # Kategorien für die Bereinigung
        self.cleanup_categories = {
            "temporary_analysis": {
                "patterns": ["analyze_", "debug_", "test_", "check_", "diagnose_"],
                "exceptions": ["analyze_db_corrected.py"]  # Aktuelles wichtiges Modul
            },
            "backup_folders": {
                "patterns": ["backup_", "_backup_", "cleanup_backup_"],
                "exceptions": ["rag_backups"]  # Wichtige RAG-Backups behalten
            },
            "log_files": {
                "patterns": [".log"],
                "exceptions": []
            },
            "report_files": {
                "patterns": ["_report_", "cleanup_report_", "_analysis_"],
                "exceptions": []
            },
            "obsolete_modules": {
                "patterns": ["fix_", "force_", "emergency_", "apply_", "patch_"],
                "exceptions": []
            },
            "test_databases": {
                "patterns": ["test_", ".db"],
                "exceptions": ["rag_store.db", "pdf_chunks.db", "wellbeing_store.db", "performance_metrics.db"]
            }
        }

    def should_keep_file(self, file_path: Path) -> bool:
        """Bestimmt, ob eine Datei behalten werden soll"""
        filename = file_path.name
        
        # Geschützte Dateien immer behalten
        if filename in self.protected_files:
            return True
        
        # Geschützte Ordner prüfen
        for parent in file_path.parents:
            if parent.name in self.protected_folders:
                return True
        
        return False

    def categorize_file(self, file_path: Path) -> str:
        """Kategorisiert eine Datei für die Bereinigung"""
        filename = file_path.name.lower()
        
        for category, config in self.cleanup_categories.items():
            # Prüfe Ausnahmen
            if filename in [exc.lower() for exc in config["exceptions"]]:
                continue
                
            # Prüfe Patterns
            for pattern in config["patterns"]:
                if pattern.lower() in filename:
                    return category
        
        return "unknown"

    def cleanup_category(self, category: str) -> None:
        """Bereinigt alle Dateien einer bestimmten Kategorie"""
        logger.info(f"Bereinige Kategorie: {category}")
        removed_count = 0
        
        for item in self.workspace_path.iterdir():
            try:
                if self.should_keep_file(item):
                    continue
                
                if self.categorize_file(item) == category:
                    if item.is_file():
                        size = item.stat().st_size
                        item.unlink()
                        self.cleanup_report["files_removed"].append({
                            "path": str(item),
                            "category": category,
                            "size_bytes": size
                        })
                        removed_count += 1
                        logger.info(f"Datei entfernt: {item.name}")
                    
                    elif item.is_dir():
                        size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                        shutil.rmtree(item)
                        self.cleanup_report["folders_removed"].append({
                            "path": str(item),
                            "category": category,
                            "size_bytes": size
                        })
                        removed_count += 1
                        logger.info(f"Ordner entfernt: {item.name}")
                        
            except Exception as e:
                error_msg = f"Fehler beim Entfernen von {item}: {str(e)}"
                logger.error(error_msg)
                self.cleanup_report["errors"].append(error_msg)
        
        self.cleanup_report["cleanup_summary"][category] = removed_count
        logger.info(f"Kategorie {category}: {removed_count} Elemente entfernt")

    def cleanup_empty_folders(self) -> None:
        """Entfernt leere Ordner"""
        logger.info("Entferne leere Ordner...")
        removed_count = 0
        
        for item in self.workspace_path.iterdir():
            if item.is_dir() and item.name not in self.protected_folders:
                try:
                    if not any(item.iterdir()):  # Ordner ist leer
                        item.rmdir()
                        self.cleanup_report["folders_removed"].append({
                            "path": str(item),
                            "category": "empty_folder",
                            "size_bytes": 0
                        })
                        removed_count += 1
                        logger.info(f"Leerer Ordner entfernt: {item.name}")
                except Exception as e:
                    logger.error(f"Fehler beim Entfernen von leerem Ordner {item}: {str(e)}")
        
        self.cleanup_report["cleanup_summary"]["empty_folders"] = removed_count

    def create_archive_folder(self) -> None:
        """Erstellt einen Archiv-Ordner für wichtige alte Dateien"""
        archive_folder = self.workspace_path / "archive_old_analysis"
        archive_folder.mkdir(exist_ok=True)
        
        # Wichtige aber alte Analyse-Dateien archivieren statt löschen
        important_old_files = [
            "FINALE_BOT_ANALYSE_ERGEBNISSE_2025.md",
            "FINAL_PROJECT_COMPLETION_REPORT_2025.md",
            "PSYCHOLOGISCHES_SYSTEM_REFACTORING_ABGESCHLOSSEN_2025.md",
            "GPU_OPTIMIZATION_RESULTS_2025.md"
        ]
        
        for filename in important_old_files:
            file_path = self.workspace_path / filename
            if file_path.exists():
                shutil.move(str(file_path), str(archive_folder / filename))
                logger.info(f"Datei archiviert: {filename}")

    def generate_kept_files_list(self) -> None:
        """Erstellt eine Liste der behaltenen wichtigen Dateien"""
        for item in self.workspace_path.iterdir():
            if item.is_file() and self.should_keep_file(item):
                self.cleanup_report["files_kept"].append({
                    "path": str(item),
                    "size_bytes": item.stat().st_size
                })

    def calculate_space_saved(self) -> int:
        """Berechnet den gesparten Speicherplatz"""
        total_saved = 0
        for item in self.cleanup_report["files_removed"]:
            total_saved += item["size_bytes"]
        for item in self.cleanup_report["folders_removed"]:
            total_saved += item["size_bytes"]
        return total_saved

    def run_cleanup(self) -> None:
        """Führt die komplette Bereinigung durch"""
        logger.info("Starte intelligente Workspace-Bereinigung...")
        
        # Archiv-Ordner erstellen
        self.create_archive_folder()
        
        # Kategorien bereinigen
        for category in self.cleanup_categories.keys():
            self.cleanup_category(category)
        
        # Leere Ordner entfernen
        self.cleanup_empty_folders()
        
        # Liste der behaltenen Dateien erstellen
        self.generate_kept_files_list()
        
        # Bericht generieren
        space_saved = self.calculate_space_saved()
        self.cleanup_report["total_space_saved_bytes"] = space_saved
        self.cleanup_report["total_space_saved_mb"] = round(space_saved / (1024 * 1024), 2)
        
        # Bericht speichern
        report_path = self.workspace_path / f"cleanup_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(self.cleanup_report, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Bereinigung abgeschlossen! Bericht gespeichert in: {report_path}")
        logger.info(f"Gesparter Speicherplatz: {self.cleanup_report['total_space_saved_mb']} MB")
        
        # Zusammenfassung ausgeben
        print("\n" + "="*60)
        print("WORKSPACE CLEANUP ZUSAMMENFASSUNG")
        print("="*60)
        print(f"Gesparter Speicherplatz: {self.cleanup_report['total_space_saved_mb']} MB")
        print(f"Entfernte Dateien: {len(self.cleanup_report['files_removed'])}")
        print(f"Entfernte Ordner: {len(self.cleanup_report['folders_removed'])}")
        print(f"Behaltene wichtige Dateien: {len(self.cleanup_report['files_kept'])}")
        print(f"Fehler: {len(self.cleanup_report['errors'])}")
        print("\nKategorien:")
        for category, count in self.cleanup_report['cleanup_summary'].items():
            print(f"  {category}: {count} Elemente")
        print("="*60)

if __name__ == "__main__":
    import argparse as _argparse

    # Workspace-Pfad: Standard = Projekt-Root (dieses Modul liegt in utils/).
    # Kein harter Maschinenpfad - der Default wird relativ zur Datei aufgelost.
    _parser = _argparse.ArgumentParser(description="Intelligentes Workspace-Cleanup (Dry-Run)")
    _parser.add_argument(
        "workspace",
        nargs="?",
        default=str(Path(__file__).resolve().parents[1]),
        help="Workspace-Root (Default: Projekt-Root)",
    )
    _args = _parser.parse_args()

    workspace_path = _args.workspace
    cleanup = IntelligentWorkspaceCleanup(workspace_path)
    cleanup.run_cleanup()
