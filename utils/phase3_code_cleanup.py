#!/usr/bin/env python3
"""
PHASE 3 CODE-CLEANUP: Erweiterte Bereinigung
Entfernt weitere redundante Module für maximale Code-Sauberkeit.

Kategorien für Phase 3:
1. Duplicate/Legacy Start-Scripte  
2. Alternative RAG-Implementierungen
3. Veraltete Test-Scripte (außerhalb /tests/)
4. Demo/Experimental-Module
5. Backup/Migration-Tools (nach Erfolg)
6. Redundante Analysis-Tools (erweitert)
"""

import os
import shutil
import logging
from datetime import datetime
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_backup(backup_name="phase3_cleanup"):
    """Erstellt Backup vor Phase 3 Cleanup"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f"{backup_name}_{timestamp}"
    
    print(f"\n📦 BACKUP WIRD ERSTELLT: {backup_dir}")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir

def get_phase3_cleanup_modules():
    """Definiert Module für Phase 3 Cleanup"""
    
    # KATEGORIE 1: Duplicate/Legacy Start-Scripte
    start_scripts = [
        "start_chatbot_original.py",
        "start_gui_fixed.py", 
        "start_optimized_chatbot.py",
        "start_intelligent_routing_chatbot.py",
        "start_chatbot_with_agent.py",
        "start_enhanced_chatbot.py",
        "start_chatbot_clean.py"
    ]
    
    # KATEGORIE 2: Alternative RAG-Implementierungen (nach Erfolg)
    rag_alternatives = [
        "rag_store_new.py",
        "rag_enhanced.py", 
        "simple_rag_migrator.py",
        "rag_migration_manager.py",
        "enhanced_rag_search.py",
        "rag_duplicate_manager.py",  # Nach erfolgreichem Cleanup
        "rag_integrity_checker.py"
    ]
    
    # KATEGORIE 3: Veraltete Test-Scripte (außerhalb /tests/)
    standalone_tests = [
        "test_agent_mode_bypass.py",
        "test_automatic_rag_update.py", 
        "test_chat_verlauf_direkt.py",
        "test_chat_verlauf_schutz.py",
        "test_clickable_links.py",
        "test_concrete_queries.py",
        "test_crashsafe_integration.py",
        "test_datenschutz_web_extraktion.py",
        "test_direct_tool_call.py",
        "test_enhanced_excel_metadata.py",
        "test_enhanced_metadata_gui_integration.py",
        "test_enhanced_rag_integration.py",
        "test_excel_content_access.py",
        "test_excel_import_fix.py",
        "test_feedback_final.py",
        "test_feedback_integration.py",
        "test_final_optimization.py",
        "test_gui_launch.py",
        "test_import_validation.py",
        "test_memory_optimization.py",
        "test_metadata_complete.py",
        "test_multiple_excel.py",
        "test_new_rag_integration.py",
        "test_orchestrator_rag_logic.py",
        "test_rag_store.py",
        "test_source_validation.py",
        "test_tools_rag_integration.py",
        "simple_crashsafe_test.py",
        "simple_wellbeing_context.py"  # Nach Integration
    ]
    
    # KATEGORIE 4: Demo/Experimental-Module
    demo_modules = [
        "demo_url_import.py",
        "demo_time_context_solution.py", 
        "demo_performance_dashboard.py",
        "streamlit_demo.py",
        "setup_feedback_analysis_demo.py",
        "quick_module_cleanup.py",
        "create_test_excel.py"
    ]
    
    # KATEGORIE 5: Backup/Migration-Tools (nach Erfolg)
    migration_tools = [
        "manual_cleanup_helper.py",
        "final_cleanup_refactoring.py",
        "refactoring_analysis.py", 
        "inspect_database.py",
        "repair_chunk_id_system.py",
        "repair_chunk_id_system_smart.py",
        "find_missing_chunks.py",
        "fix_excel_import_schema.py",
        "fix_irrelevant_sources.py",
        "fix_rag_wal_problem.py",
        "fix_wal_problem.py"
    ]
    
    # KATEGORIE 6: Erweiterte Analysis-Tools
    extended_analysis = [
        "content_samples_analysis.py",
        "cot_tot_analysis.py", 
        "deep_content_analysis.py",
        "deep_db_analysis.py",
        "detaillierte_db_inhalt_cot_analyse.py",
        "embedding_quality_analysis.py",
        "feedback_usage_analysis.py",
        "search_optimization_plan.py",
        "configure_max_gpu.py",
        "diversity_integration_complete.py",
        "deploy_diversity_complete.py"
    ]
    
    # KATEGORIE 7: Diagnose-Tools (nach Problembehebung)
    diagnose_tools = [
        "diagnose_40gb_problem.py",
        "diagnose_agent_chat_bottleneck.py",
        "diagnose_excel_import_error.py", 
        "diagnose_feedback_link_problem.py",
        "diagnose_orchestrator_bottleneck.py",
        "debug_gui_crash.py",
        "debug_psychological_gui.py"
    ]
    
    # KATEGORIE 8: Finalization-Tools (nach Abschluss)
    finalization_tools = [
        "final_confirmation.py",
        "final_diversity_test.py",
        "final_feedback_verification.py", 
        "final_integration_test.py",
        "FINALE_CONTENT_VALIDIERUNG_2025.py"
    ]
    
    return {
        "Start-Scripte": start_scripts,
        "RAG-Alternativen": rag_alternatives, 
        "Standalone-Tests": standalone_tests,
        "Demo-Module": demo_modules,
        "Migration-Tools": migration_tools,
        "Extended-Analysis": extended_analysis,
        "Diagnose-Tools": diagnose_tools,
        "Finalization-Tools": finalization_tools
    }

def safe_remove_modules(modules_dict, backup_dir, dry_run=True):
    """Entfernt Module sicher mit Backup"""
    
    removed_count = 0
    total_count = 0
    removal_log = []
    
    for category, modules in modules_dict.items():
        print(f"\n🔍 KATEGORIE: {category}")
        print("=" * 50)
        
        category_removed = 0
        for module in modules:
            total_count += 1
            
            if os.path.exists(module):
                if not dry_run:
                    # Backup erstellen
                    shutil.copy2(module, backup_dir)
                    # Modul entfernen
                    os.remove(module)
                    removal_log.append(f"✅ {module}")
                    print(f"✅ Entfernt: {module}")
                else:
                    removal_log.append(f"📋 {module}")
                    print(f"📋 Würde entfernt: {module}")
                
                removed_count += 1
                category_removed += 1
            else:
                print(f"⚠️ Nicht gefunden: {module}")
        
        print(f"📊 {category}: {category_removed} Module")
    
    print(f"\n📊 ZUSAMMENFASSUNG:")
    print(f"📦 Total Module gescannt: {total_count}")
    print(f"🗑️ Module {'entfernt' if not dry_run else 'für Entfernung markiert'}: {removed_count}")
    print(f"📈 Bereinigung: {(removed_count/total_count)*100:.1f}%")
    
    return removed_count, removal_log

def check_critical_modules_intact():
    """Prüft ob kritische Module noch vorhanden sind"""
    
    critical_modules = [
        "gui.py",
        "chatbot_logic.py", 
        "agent_chatbot_logic.py",
        "model_loader.py",
        "agent_toolkit.py",
        "wellbeing/wellbeing_support_interface.py",
        "agent/rag_store.py",
        "agent/smart_rag_store.py", 
        "agent/tools.py",
        "agent/orchestrator.py",
        "response_feedback_widget.py",
        "smart_feedback_widget.py",
        "feedback_analysis_tab_crashsafe.py"
    ]
    
    print(f"\n🛡️ KRITISCHE MODULE CHECK:")
    print("=" * 40)
    
    all_intact = True
    for module in critical_modules:
        if os.path.exists(module):
            print(f"✅ {module}")
        else:
            print(f"❌ FEHLT: {module}")
            all_intact = False
    
    return all_intact

def generate_cleanup_report(removal_log, backup_dir):
    """Generiert detaillierten Cleanup-Report"""
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_path = f"phase3_cleanup_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"PHASE 3 CODE-CLEANUP REPORT\n")
        f.write(f"Zeitstempel: {timestamp}\n")
        f.write(f"Backup-Verzeichnis: {backup_dir}\n")
        f.write("=" * 50 + "\n\n")
        
        f.write("ENTFERNTE MODULE:\n")
        f.write("-" * 20 + "\n")
        for entry in removal_log:
            f.write(f"{entry}\n")
        
        f.write(f"\nGESAMT: {len(removal_log)} Module bearbeitet\n")
        f.write(f"\nBackup erstellt in: {backup_dir}\n")
        f.write("Bei Problemen: Module aus Backup wiederherstellen\n")
    
    print(f"\n📄 Report erstellt: {report_path}")
    return report_path

def main():
    """Hauptfunktion für Phase 3 Cleanup"""
    
    print("🧹 PHASE 3 CODE-CLEANUP GESTARTET")
    print("=" * 60)
    print("🎯 Ziel: Entfernung weiterer 50-70 redundanter Module")
    print("🛡️ Sicherheit: Vollständiges Backup + Critical Module Check")
    print("=" * 60)
    
    # 1. Backup erstellen
    backup_dir = create_backup()
    
    # 2. Module-Listen abrufen
    modules_dict = get_phase3_cleanup_modules()
    
    # 3. Dry-Run durchführen
    print(f"\n🔍 DRY-RUN: Was würde entfernt werden?")
    print("=" * 50)
    dry_removed, dry_log = safe_remove_modules(modules_dict, backup_dir, dry_run=True)
    
    # 4. User-Confirmation
    print(f"\n⚠️ WICHTIGE ENTSCHEIDUNG:")
    print(f"📦 {dry_removed} Module wurden für Entfernung identifiziert")
    print(f"💾 Backup wird erstellt in: {backup_dir}")
    print(f"🛡️ Kritische Module bleiben unberührt")
    
    user_input = input(f"\n🤔 Phase 3 Cleanup durchführen? (ja/nein): ").strip().lower()
    
    if user_input in ['ja', 'j', 'yes', 'y']:
        # 5. Kritische Module Check
        print(f"\n🛡️ SICHERHEITSCHECK...")
        if not check_critical_modules_intact():
            print(f"❌ ABBRUCH: Kritische Module fehlen bereits!")
            return False
        
        # 6. Tatsächlicher Cleanup
        print(f"\n🗑️ CLEANUP WIRD DURCHGEFÜHRT...")
        actual_removed, actual_log = safe_remove_modules(modules_dict, backup_dir, dry_run=False)
        
        # 7. Post-Cleanup Check
        print(f"\n🔍 POST-CLEANUP ÜBERPRÜFUNG...")
        if check_critical_modules_intact():
            print(f"✅ Alle kritischen Module sind intakt!")
        else:
            print(f"❌ WARNUNG: Kritische Module wurden beschädigt!")
        
        # 8. Report generieren
        report_path = generate_cleanup_report(actual_log, backup_dir)
        
        print(f"\n🎉 PHASE 3 CLEANUP ERFOLGREICH ABGESCHLOSSEN!")
        print(f"🗑️ {actual_removed} Module entfernt")
        print(f"💾 Backup: {backup_dir}")
        print(f"📄 Report: {report_path}")
        print(f"\n🚀 Code ist jetzt noch sauberer und wartbarer!")
        
        return True
    else:
        print(f"\n⏹️ Cleanup abgebrochen - keine Änderungen vorgenommen")
        return False

if __name__ == "__main__":
    try:
        success = main()
        if success:
            print(f"\n🎯 NÄCHSTE SCHRITTE:")
            print(f"1. ✅ GUI testen: python gui.py")
            print(f"2. ✅ Alle Funktionen prüfen")  
            print(f"3. ✅ Bei Problemen: Module aus Backup wiederherstellen")
        else:
            print(f"\n📋 Cleanup wurde nicht durchgeführt")
    except KeyboardInterrupt:
        print(f"\n\n⏹️ Cleanup durch Benutzer abgebrochen")
    except Exception as e:
        print(f"\n💥 FEHLER: {e}")
        import traceback
        traceback.print_exc()
