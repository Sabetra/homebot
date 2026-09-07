#!/usr/bin/env python3
"""
IMPORT MONITOR
==============

Überwacht Imports auf Memory-Loops
"""

import sys
import time
import psutil
import threading
from collections import defaultdict

class ImportMonitor:
    def __init__(self):
        self.import_counts = defaultdict(int)
        self.memory_start = psutil.Process().memory_info().rss / 1024 / 1024
        self.monitoring = True
        
    def start_monitoring(self):
        """Startet Import-Monitoring in eigenem Thread"""
        def monitor_loop():
            while self.monitoring:
                try:
                    current_memory = psutil.Process().memory_info().rss / 1024 / 1024
                    memory_increase = current_memory - self.memory_start
                    
                    if memory_increase > 100:  # Mehr als 100MB Zuwachs
                        print(f"🚨 MEMORY SPIKE: +{memory_increase:.1f}MB")
                        
                        # Zeige die häufigsten Imports
                        if self.import_counts:
                            top_imports = sorted(
                                self.import_counts.items(), 
                                key=lambda x: x[1], 
                                reverse=True
                            )[:5]
                            
                            print("📊 Häufigste Imports:")
                            for module, count in top_imports:
                                print(f"   {module}: {count}x")
                                if count > 10:  # Mehr als 10x importiert
                                    print(f"   🚨 VERDÄCHTIG: {module} zu oft importiert!")
                    
                    time.sleep(5)  # Check alle 5 Sekunden
                    
                except Exception as e:
                    print(f"Monitor-Fehler: {e}")
                    break
        
        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()
        print("✅ Import-Monitor gestartet")
    
    def log_import(self, module_name):
        """Loggt einen Import"""
        self.import_counts[module_name] += 1
    
    def stop_monitoring(self):
        """Stoppt das Monitoring"""
        self.monitoring = False

# Globaler Monitor
IMPORT_MONITOR = ImportMonitor()

def start_import_monitoring():
    """Startet das Import-Monitoring"""
    IMPORT_MONITOR.start_monitoring()

if __name__ == "__main__":
    start_import_monitoring()
    
    # Teste mit künstlichen Imports
    import time
    for i in range(20):
        IMPORT_MONITOR.log_import('test_module')
        time.sleep(0.1)
