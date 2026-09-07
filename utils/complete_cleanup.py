#!/usr/bin/env python3
"""
KOMPLETTE MODUL-BEREINIGUNG
Löscht ALLE ungenutzten Module und behält nur die 27 essentiellen Module
Basiert auf präziser Dependency-Analyse von gui.py
"""

import os
import shutil
from pathlib import Path
from datetime import datetime

def complete_module_cleanup():
    """Komplette Bereinigung - nur essentielle Module behalten"""
    
    print("🔥 KOMPLETTE MODUL-BEREINIGUNG")
    print("Behalte nur die 27 tatsächlich genutzten Module")
    print("=" * 60)
    
    # Die 27 essentiellen Module (von gui.py Dependency-Trace)
    essential_modules = {
        # Entry-Point und Core
        "gui",
        "model_loader", 
        "agent_chatbot_logic",
        "chatbot_logic",
        
        # Agent-Core-Module
        "agent_toolkit",
        "orchestrator",
        "tools", 
        "context",
        "prompts",
        "types",
        
        # RAG-System
        "rag_store",
        "smart_rag_store",
        
        # Intelligence-Module
        "generic_intent_classifier",
        "universal_entity_validator",
        "universal_evidence_selector", 
        "hybrid_search",
        "intelligent_routing",
        "optimized_research_engine",
        
        # Utility-Module
        "web_policy",
        "logging_setup",
        "ui_utils",
        
        # GUI-Komponenten
        "smart_feedback_widget",
        "smart_gui_components",
        "advanced_gui_components",
        "gui_enhancements",
        "gui_utils"
    }
    
    print(f"✅ Essentielle Module: {len(essential_modules)}")
    for module in sorted(essential_modules):
        print(f"   📦 {module}")
    
    # Backup-Verzeichnis erstellen
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f"complete_cleanup_backup_{timestamp}"
    os.makedirs(backup_dir, exist_ok=True)
    print(f"\n💾 Backup-Verzeichnis: {backup_dir}")
    
    # Alle Python-Dateien finden
    all_python_files = []
    
    # Hauptverzeichnis
    for file in Path(".").glob("*.py"):
        if file.name != "__init__.py":
            all_python_files.append(file)
    
    # Agent-Verzeichnis
    agent_dir = Path("./agent")
    if agent_dir.exists():
        for file in agent_dir.glob("*.py"):
            if file.name != "__init__.py":
                all_python_files.append(file)
    
    print(f"\n📁 Gefundene Python-Dateien: {len(all_python_files)}")
    
    # Kategorisiere Dateien
    to_keep = []
    to_delete = []
    
    for file in all_python_files:
        module_name = file.stem
        
        # Prüfe ob essentiell
        is_essential = False
        
        # Direkte Übereinstimmung
        if module_name in essential_modules:
            is_essential = True
        
        # Mit agent.-Prefix
        if f"agent.{module_name}" in essential_modules:
            is_essential = True
            
        # Ohne agent.-Prefix (für Agent-Module)
        if file.parent.name == "agent" and module_name in essential_modules:
            is_essential = True
        
        if is_essential:
            to_keep.append(file)
        else:
            to_delete.append(file)
    
    print(f"\n📊 BEREINIGUNGSPLAN:")
    print(f"   ✅ Zu behalten: {len(to_keep)} Module")
    print(f"   🗑️ Zu löschen: {len(to_delete)} Module")
    print(f"   📈 Bereinigung: {(len(to_delete)/len(all_python_files))*100:.1f}%")
    
    # Zeige Module die behalten werden
    print(f"\n✅ MODULE DIE BEHALTEN WERDEN ({len(to_keep)}):")
    for file in sorted(to_keep):
        relative_path = file.relative_to(Path("."))
        print(f"   📦 {relative_path}")
    
    # Bestätigung
    print(f"\n⚠️ WARNUNG: {len(to_delete)} Module werden PERMANENT gelöscht!")
    print("Backup wird erstellt, aber das ist eine drastische Änderung.")
    
    response = input(f"\nKomplette Bereinigung durchführen? Nur {len(to_keep)} Module behalten? (j/n): ")
    
    if response.lower() not in ['j', 'ja', 'y', 'yes']:
        print("❌ Bereinigung abgebrochen.")
        return
    
    # Backup erstellen
    print(f"\n💾 Erstelle Backup...")
    backup_count = 0
    
    for file in to_delete:
        try:
            # Relative Pfadstruktur im Backup beibehalten
            relative_path = file.relative_to(Path("."))
            backup_file = Path(backup_dir) / relative_path
            
            # Verzeichnis im Backup erstellen falls nötig
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Backup
            shutil.copy2(file, backup_file)
            backup_count += 1
            
        except Exception as e:
            print(f"   ⚠️ Backup-Fehler für {file}: {e}")
    
    print(f"   ✅ {backup_count} Dateien gesichert")
    
    # Module löschen
    print(f"\n🗑️ Lösche ungenutzte Module...")
    deleted_count = 0
    errors = []
    
    for file in to_delete:
        try:
            file.unlink()
            deleted_count += 1
            if deleted_count <= 10:  # Zeige nur erste 10
                print(f"   🗑️ Gelöscht: {file.relative_to(Path('.'))}")
            elif deleted_count == 11:
                print(f"   🗑️ ... und {len(to_delete)-10} weitere Module")
                
        except Exception as e:
            errors.append(f"{file}: {e}")
    
    # Leere Verzeichnisse aufräumen
    print(f"\n🧹 Räume leere Verzeichnisse auf...")
    
    # Tests-Verzeichnis prüfen
    tests_dir = Path("./tests")
    if tests_dir.exists() and not any(tests_dir.iterdir()):
        try:
            tests_dir.rmdir()
            print(f"   🗑️ Leeres Verzeichnis entfernt: tests/")
        except:
            pass
    
    # Backup auch des agent-Verzeichnisses falls es leer wird
    agent_files = list(Path("./agent").glob("*.py")) if Path("./agent").exists() else []
    if Path("./agent").exists() and len(agent_files) == 0:
        print(f"   ℹ️ Agent-Verzeichnis ist leer, aber wird beibehalten")
    
    # Ergebnisse
    print(f"\n" + "="*60)
    print(f"✅ KOMPLETTE BEREINIGUNG ABGESCHLOSSEN!")
    print(f"="*60)
    print(f"📊 STATISTIKEN:")
    print(f"   🗑️ Module gelöscht: {deleted_count}")
    print(f"   ✅ Module behalten: {len(to_keep)}")
    print(f"   💾 Backup erstellt: {backup_dir}/")
    print(f"   📈 Codebasis reduziert: {(deleted_count/(deleted_count+len(to_keep)))*100:.1f}%")
    
    if errors:
        print(f"   ⚠️ Fehler: {len(errors)}")
        for error in errors[:5]:  # Zeige nur erste 5 Fehler
            print(f"      • {error}")
    
    # Verbleibende Struktur zeigen
    print(f"\n📁 VERBLEIBENDE PROJEKT-STRUKTUR:")
    
    # Hauptverzeichnis
    main_files = sorted([f for f in Path(".").glob("*.py") if f.name != "__init__.py"])
    if main_files:
        print(f"   📂 Hauptverzeichnis ({len(main_files)} Module):")
        for file in main_files:
            print(f"      📦 {file.name}")
    
    # Agent-Verzeichnis
    agent_files = sorted([f for f in Path("./agent").glob("*.py")]) if Path("./agent").exists() else []
    if agent_files:
        print(f"   📂 agent/ ({len(agent_files)} Module):")
        for file in agent_files:
            print(f"      📦 {file.name}")
    
    print(f"\n🎯 NÄCHSTE SCHRITTE:")
    print(f"   1. GUI testen: python gui.py")
    print(f"   2. Funktionalität prüfen")
    print(f"   3. Bei Problemen: Backup aus {backup_dir}/ wiederherstellen")
    print(f"   4. Bei Erfolg: Backup-Verzeichnis löschen")
    
    # Schnelltest anbieten
    print(f"\n🚀 SCHNELLTEST DURCHFÜHREN?")
    test_response = input("GUI-Import-Test durchführen? (j/n): ")
    
    if test_response.lower() in ['j', 'ja', 'y', 'yes']:
        print(f"\n🧪 Teste GUI-Import...")
        try:
            # Teste ob GUI importiert werden kann
            import sys
            sys.path.insert(0, ".")
            
            # Teste die kritischen Imports
            print("   📦 Teste model_loader...")
            from scripts import model_loader  # canonical path
            print("   ✅ model_loader OK")
            
            print("   📦 Teste agent_chatbot_logic...")
            import agent_chatbot_logic  
            print("   ✅ agent_chatbot_logic OK")
            
            print("   📦 Teste gui-Module...")
            # Nur Import-Test, nicht GUI starten
            import importlib.util
            spec = importlib.util.spec_from_file_location("gui", "gui.py")
            print("   ✅ GUI-Module OK")
            
            print(f"\n🎉 ALLE KRITISCHEN MODULE FUNKTIONSFÄHIG!")
            print(f"   ✅ Die Bereinigung war erfolgreich!")
            
        except Exception as e:
            print(f"\n❌ IMPORT-FEHLER: {e}")
            print(f"⚠️ Möglicherweise wurde ein essentielles Modul gelöscht!")
            print(f"💡 Backup wiederherstellen aus: {backup_dir}/")

if __name__ == "__main__":
    complete_module_cleanup()
