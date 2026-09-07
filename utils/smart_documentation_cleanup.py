#!/usr/bin/env python3
"""
SMART DOCUMENTATION CLEANUP
Analysiert, kategorisiert und bereinigt Markdown-Dokumentation
"""

import os
import re
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

class DocumentationAnalyzer:
    """Intelligente Analyse und Bereinigung von Dokumentation"""
    
    def __init__(self, workspace_dir: str = "."):
        self.workspace_dir = Path(workspace_dir)
        self.categories = {
            "current": [],       # Aktuelle, relevante Dokumentation
            "historical": [],    # Historische Dokumentation (wichtig für Verlauf)
            "outdated": [],      # Veraltete/obsolete Dokumentation 
            "duplicate": [],     # Duplikate oder sehr ähnliche Inhalte
            "empty": [],         # Leere oder minimale Dateien
            "consolidate": []    # Kandidaten für Konsolidierung
        }
        
    def analyze_all_docs(self) -> Dict[str, List[Tuple[str, Dict]]]:
        """Analysiert alle Markdown-Dateien im Workspace"""
        print("📚 DOKUMENTATIONS-ANALYSE")
        print("-" * 50)
        
        md_files = list(self.workspace_dir.glob("*.md"))
        print(f"📄 {len(md_files)} Markdown-Dateien gefunden")
        
        for md_file in md_files:
            analysis = self._analyze_single_doc(md_file)
            category = self._categorize_doc(md_file, analysis)
            self.categories[category].append((str(md_file), analysis))
        
        return self.categories
    
    def _analyze_single_doc(self, file_path: Path) -> Dict:
        """Analysiert eine einzelne Markdown-Datei"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            content = ""
        
        # Basis-Statistiken
        lines = content.splitlines()
        word_count = len(content.split()) if content else 0
        
        # Metadaten
        creation_time = file_path.stat().st_mtime
        file_age_days = (datetime.now().timestamp() - creation_time) / 86400
        
        # Content-Analyse
        has_headers = bool(re.search(r'^#{1,6}\s', content, re.MULTILINE))
        has_code_blocks = '```' in content or '`' in content
        has_lists = bool(re.search(r'^\s*[-*+]\s', content, re.MULTILINE))
        has_checkmarks = '✅' in content or '❌' in content
        
        # Status-Erkennung
        completion_indicators = ['✅', 'ABGESCHLOSSEN', 'ERFOLGREICH', 'COMPLETED', 'ACCOMPLISHED']
        is_completion_doc = any(indicator in content for indicator in completion_indicators)
        
        # Problem-/Fix-Dokumentation
        problem_indicators = ['PROBLEM', 'FEHLER', 'ERROR', 'BUG', 'ISSUE', 'BEHOBEN', 'GELÖST']
        is_problem_doc = any(indicator in file_path.name.upper() or indicator in content for indicator in problem_indicators)
        
        # Analyse-/Status-Dokumentation
        analysis_indicators = ['ANALYSE', 'ANALYSIS', 'BEWERTUNG', 'ASSESSMENT', 'STATUS', 'DOKUMENTATION']
        is_analysis_doc = any(indicator in file_path.name.upper() for indicator in analysis_indicators)
        
        return {
            'word_count': word_count,
            'line_count': len(lines),
            'file_size': file_path.stat().st_size,
            'age_days': file_age_days,
            'has_headers': has_headers,
            'has_code_blocks': has_code_blocks,
            'has_lists': has_lists,
            'has_checkmarks': has_checkmarks,
            'is_completion_doc': is_completion_doc,
            'is_problem_doc': is_problem_doc,
            'is_analysis_doc': is_analysis_doc,
            'is_empty': word_count < 10,
            'content_preview': content[:200] if content else ""
        }
    
    def _categorize_doc(self, file_path: Path, analysis: Dict) -> str:
        """Kategorisiert ein Dokument basierend auf Analyse"""
        filename = file_path.name.upper()
        
        # Leere Dateien
        if analysis['is_empty'] or analysis['word_count'] < 10:
            return "empty"
        
        # Aktuelle/wichtige Dokumentation (letzte 7 Tage oder spezielle Namen)
        important_keywords = [
            'START_ANLEITUNG', 'README', 'INSTALLATION', 'SETUP',
            'API', 'USAGE', 'GUIDE', 'TUTORIAL', 'QUICK_START'
        ]
        if (analysis['age_days'] < 7 or 
            any(keyword in filename for keyword in important_keywords) or
            'WEB_EXTRACTION_STATE_OF_ART' in filename or
            'MODULE_CLEANUP_STRATEGY' in filename or
            'ITERATION_CONTINUATION_COMPLETE' in filename):
            return "current"
        
        # Historische Dokumentation (wichtige Meilensteine)
        historical_keywords = [
            'MISSION_ACCOMPLISHED', 'FINALE_', 'FINAL_', 'COMPLETION', 
            'REFACTORING_COMPLETION', 'OPTIMIERUNG_MISSION'
        ]
        if (any(keyword in filename for keyword in historical_keywords) and 
            analysis['word_count'] > 100):
            return "historical"
        
        # Problem-Dokumentation (oft obsolet nach Fix)
        if (analysis['is_problem_doc'] and analysis['age_days'] > 14):
            return "outdated"
        
        # Duplikate/ähnliche Inhalte erkennen
        duplicate_patterns = [
            ('IRRELEVANTE_QUELLEN_BEHOBEN', 'IRRELEVANTE_QUELLEN_BEHOBEN_2025'),
            ('FEEDBACK_', 'FEEDBACK_TEST_'),
            ('RAG_DB_', 'RAG_DATENBANK_'),
            ('WEB_ZU_RAG_', 'WEB_EXTRACTION_')
        ]
        for pattern1, pattern2 in duplicate_patterns:
            if pattern1 in filename or pattern2 in filename:
                return "duplicate"
        
        # Analyse-Dokumentation (kann konsolidiert werden)
        if (analysis['is_analysis_doc'] and 
            analysis['age_days'] > 30 and 
            analysis['word_count'] < 1000):
            return "consolidate"
        
        # Veraltete Dokumentation
        if analysis['age_days'] > 60 and not analysis['is_completion_doc']:
            return "outdated"
        
        # Standard: Historical für wichtige Dokumente
        return "historical"
    
    def generate_cleanup_plan(self) -> str:
        """Generiert detaillierten Bereinigungsplan"""
        plan = []
        plan.append("📚 DOKUMENTATIONS-BEREINIGUNGSPLAN")
        plan.append("=" * 60)
        plan.append(f"Analysiert: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        plan.append("")
        
        # Statistiken
        total_docs = sum(len(docs) for docs in self.categories.values())
        plan.append(f"📊 KATEGORISIERUNG ({total_docs} Dokumente):")
        
        for category, docs in self.categories.items():
            if docs:
                plan.append(f"   📂 {category.upper()}: {len(docs)} Dokumente")
        
        plan.append("")
        
        # Details für jede Kategorie
        if self.categories["empty"]:
            plan.append("🗑️ LEERE DATEIEN (Sichere Löschung):")
            for file_path, analysis in self.categories["empty"]:
                filename = Path(file_path).name
                plan.append(f"   💀 {filename} ({analysis['word_count']} Wörter)")
        
        if self.categories["outdated"]:
            plan.append("\n📅 VERALTETE DOKUMENTATION (Archivierung empfohlen):")
            for file_path, analysis in self.categories["outdated"]:
                filename = Path(file_path).name
                age = int(analysis['age_days'])
                plan.append(f"   🕐 {filename} ({age} Tage alt, {analysis['word_count']} Wörter)")
        
        if self.categories["duplicate"]:
            plan.append("\n🔄 DUPLIKATE/ÄHNLICHE INHALTE (Konsolidierung):")
            for file_path, analysis in self.categories["duplicate"]:
                filename = Path(file_path).name
                plan.append(f"   🔄 {filename} ({analysis['word_count']} Wörter)")
        
        if self.categories["consolidate"]:
            plan.append("\n📋 KONSOLIDIERUNG (Zusammenfassen empfohlen):")
            for file_path, analysis in self.categories["consolidate"]:
                filename = Path(file_path).name
                plan.append(f"   📋 {filename} ({analysis['word_count']} Wörter)")
        
        if self.categories["current"]:
            plan.append("\n✅ AKTUELLE DOKUMENTATION (Behalten):")
            for file_path, analysis in self.categories["current"]:
                filename = Path(file_path).name
                plan.append(f"   ✅ {filename} ({analysis['word_count']} Wörter)")
        
        if self.categories["historical"]:
            plan.append("\n📚 HISTORISCHE DOKUMENTATION (Archivieren):")
            for file_path, analysis in self.categories["historical"]:
                filename = Path(file_path).name
                age = int(analysis['age_days'])
                plan.append(f"   📚 {filename} ({age} Tage alt, {analysis['word_count']} Wörter)")
        
        # Empfehlungen
        plan.append("\n🎯 EMPFEHLUNGEN:")
        
        deletable = len(self.categories["empty"])
        archivable = len(self.categories["outdated"]) + len(self.categories["historical"])
        consolidatable = len(self.categories["duplicate"]) + len(self.categories["consolidate"])
        
        plan.append(f"   🗑️ Sichere Löschung: {deletable} Dateien")
        plan.append(f"   📦 Archivierung: {archivable} Dateien")
        plan.append(f"   🔄 Konsolidierung: {consolidatable} Dateien")
        plan.append(f"   ✅ Behalten: {len(self.categories['current'])} Dateien")
        
        potential_savings = deletable + archivable
        percentage = (potential_savings / total_docs) * 100 if total_docs > 0 else 0
        plan.append(f"   💾 Bereinigungspotenzial: {potential_savings}/{total_docs} ({percentage:.1f}%)")
        
        return "\n".join(plan)
    
    def execute_cleanup(self, 
                       delete_empty: bool = True,
                       archive_old: bool = True, 
                       create_consolidated: bool = True,
                       dry_run: bool = True) -> Dict[str, int]:
        """Führt Dokumentations-Bereinigung durch"""
        
        stats = {"deleted": 0, "archived": 0, "consolidated": 0, "errors": 0}
        
        # Backup erstellen (auch für dry run um Pfad zu haben)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.workspace_dir / f"docs_backup_{timestamp}"
        
        if dry_run:
            print("🔍 DRY RUN - Simulation ohne echte Änderungen")
        else:
            backup_dir.mkdir(exist_ok=True)
            print(f"💾 Backup erstellt: {backup_dir}")
        
        # Leere Dateien löschen
        if delete_empty:
            for file_path, analysis in self.categories["empty"]:
                try:
                    if not dry_run:
                        file_obj = Path(file_path)
                        backup_path = backup_dir / file_obj.name
                        shutil.copy2(file_obj, backup_path)
                        file_obj.unlink()
                    stats["deleted"] += 1
                    print(f"🗑️ {'[DRY RUN] ' if dry_run else ''}Gelöscht: {Path(file_path).name}")
                except Exception as e:
                    stats["errors"] += 1
                    print(f"❌ Fehler beim Löschen {Path(file_path).name}: {e}")
        
        # Archivierung
        if archive_old and not dry_run:
            archive_dir = self.workspace_dir / "docs_archive"
            archive_dir.mkdir(exist_ok=True)
            
            for file_path, analysis in self.categories["outdated"] + self.categories["historical"]:
                try:
                    file_obj = Path(file_path)
                    archive_path = archive_dir / file_obj.name
                    shutil.move(file_obj, archive_path)
                    stats["archived"] += 1
                    print(f"📦 Archiviert: {file_obj.name}")
                except Exception as e:
                    stats["errors"] += 1
                    print(f"❌ Fehler beim Archivieren {Path(file_path).name}: {e}")
        elif archive_old and dry_run:
            for file_path, analysis in self.categories["outdated"] + self.categories["historical"]:
                stats["archived"] += 1
                print(f"📦 [DRY RUN] Archiviert: {Path(file_path).name}")
        
        return stats

def main():
    """Hauptfunktion für Dokumentations-Bereinigung"""
    print("📚 SMART DOCUMENTATION CLEANUP")
    print("=" * 60)
    
    analyzer = DocumentationAnalyzer(".")
    
    # Analyse durchführen
    categories = analyzer.analyze_all_docs()
    
    # Plan generieren
    cleanup_plan = analyzer.generate_cleanup_plan()
    print(cleanup_plan)
    
    # Plan speichern
    plan_file = "documentation_cleanup_plan.txt"
    with open(plan_file, 'w', encoding='utf-8') as f:
        f.write(cleanup_plan)
    print(f"\n💾 Bereinigungsplan gespeichert: {plan_file}")
    
    # Interaktive Bereinigung
    deletable_count = len(categories["empty"])
    archivable_count = len(categories["outdated"]) + len(categories["historical"])
    
    if deletable_count > 0 or archivable_count > 0:
        print(f"\n🤖 AUTOMATISCHE BEREINIGUNG VERFÜGBAR:")
        print(f"   🗑️ {deletable_count} leere Dateien löschen")
        print(f"   📦 {archivable_count} Dateien archivieren")
        
        response = input("\nBereinigung durchführen? (j/n): ")
        
        if response.lower() in ['j', 'ja', 'y', 'yes']:
            stats = analyzer.execute_cleanup(
                delete_empty=True,
                archive_old=True,
                dry_run=False
            )
            
            print(f"\n✅ BEREINIGUNG ABGESCHLOSSEN:")
            print(f"   🗑️ Gelöscht: {stats['deleted']} Dateien")
            print(f"   📦 Archiviert: {stats['archived']} Dateien")
            if stats['errors']:
                print(f"   ❌ Fehler: {stats['errors']}")
        else:
            print("❌ Bereinigung abgebrochen")
    else:
        print("\n🎉 Keine automatische Bereinigung erforderlich!")

if __name__ == "__main__":
    main()
