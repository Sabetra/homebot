#!/usr/bin/env python3
"""
GUI INTEGRATION FOR PERFORMANCE DASHBOARD
Integrates performance monitoring into the existing Tkinter GUI

Features:
- Performance metrics display in main GUI
- Dashboard launcher button
- Real-time monitoring integration
- System health indicators
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import subprocess
import sys
import os
from performance_monitor import get_performance_monitor
import webbrowser
import time

class PerformanceWidget:
    """Performance monitoring widget for the main GUI"""
    
    def __init__(self, parent_frame):
        self.parent = parent_frame
        self.monitor = get_performance_monitor()
        self.update_running = False
        
        self._create_widgets()
        self._start_updates()
    
    def _create_widgets(self):
        """Create performance monitoring widgets"""
        # Main frame
        self.frame = ttk.LabelFrame(self.parent, text="System Performance", padding="10")
        self.frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Top row - System metrics
        metrics_frame = ttk.Frame(self.frame)
        metrics_frame.pack(fill=tk.X, pady=(0, 10))
        
        # CPU meter
        cpu_frame = ttk.Frame(metrics_frame)
        cpu_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Label(cpu_frame, text="CPU:").pack(anchor=tk.W)
        self.cpu_var = tk.StringVar(value="0%")
        self.cpu_label = ttk.Label(cpu_frame, textvariable=self.cpu_var, font=("Arial", 12, "bold"))
        self.cpu_label.pack(anchor=tk.W)
        self.cpu_bar = ttk.Progressbar(cpu_frame, length=100, mode='determinate')
        self.cpu_bar.pack(fill=tk.X, pady=(2, 0))
        
        # Memory meter
        mem_frame = ttk.Frame(metrics_frame)
        mem_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Label(mem_frame, text="Memory:").pack(anchor=tk.W)
        self.mem_var = tk.StringVar(value="0%")
        self.mem_label = ttk.Label(mem_frame, textvariable=self.mem_var, font=("Arial", 12, "bold"))
        self.mem_label.pack(anchor=tk.W)
        self.mem_bar = ttk.Progressbar(mem_frame, length=100, mode='determinate')
        self.mem_bar.pack(fill=tk.X, pady=(2, 0))
        
        # GPU meter (if available)
        gpu_frame = ttk.Frame(metrics_frame)
        gpu_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        ttk.Label(gpu_frame, text="GPU:").pack(anchor=tk.W)
        self.gpu_var = tk.StringVar(value="N/A")
        self.gpu_label = ttk.Label(gpu_frame, textvariable=self.gpu_var, font=("Arial", 12, "bold"))
        self.gpu_label.pack(anchor=tk.W)
        self.gpu_bar = ttk.Progressbar(gpu_frame, length=100, mode='determinate')
        self.gpu_bar.pack(fill=tk.X, pady=(2, 0))
        
        # Second row - RAG metrics
        rag_frame = ttk.Frame(self.frame)
        rag_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(rag_frame, text="RAG Operations:").pack(side=tk.LEFT)
        self.rag_ops_var = tk.StringVar(value="0 total")
        ttk.Label(rag_frame, textvariable=self.rag_ops_var, font=("Arial", 10)).pack(side=tk.LEFT, padx=(10, 0))
        
        ttk.Label(rag_frame, text="Avg Response:").pack(side=tk.LEFT, padx=(20, 0))
        self.rag_time_var = tk.StringVar(value="0 ms")
        ttk.Label(rag_frame, textvariable=self.rag_time_var, font=("Arial", 10)).pack(side=tk.LEFT, padx=(10, 0))
        
        # Third row - Controls
        controls_frame = ttk.Frame(self.frame)
        controls_frame.pack(fill=tk.X)
        
        # Dashboard button
        self.dashboard_btn = ttk.Button(
            controls_frame, 
            text="📊 Open Dashboard", 
            command=self._open_dashboard
        )
        self.dashboard_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Export button
        self.export_btn = ttk.Button(
            controls_frame,
            text="📁 Export Metrics",
            command=self._export_metrics
        )
        self.export_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Status indicator
        self.status_var = tk.StringVar(value="●")
        self.status_label = ttk.Label(
            controls_frame, 
            textvariable=self.status_var, 
            font=("Arial", 14),
            foreground="green"
        )
        self.status_label.pack(side=tk.RIGHT)
        ttk.Label(controls_frame, text="Monitor Status:").pack(side=tk.RIGHT, padx=(0, 5))
    
    def _start_updates(self):
        """Start periodic updates of performance metrics"""
        self.update_running = True
        self._update_metrics()
    
    def _update_metrics(self):
        """Update performance metrics display"""
        if not self.update_running:
            return
        
        try:
            stats = self.monitor.get_current_stats()
            
            # Update system metrics
            sys_stats = stats.get("system", {})
            
            # CPU
            cpu_percent = sys_stats.get("cpu_percent", 0)
            self.cpu_var.set(f"{cpu_percent:.1f}%")
            self.cpu_bar['value'] = cpu_percent
            
            # Color coding for CPU
            if cpu_percent > 80:
                self.cpu_label.configure(foreground="red")
            elif cpu_percent > 60:
                self.cpu_label.configure(foreground="orange")
            else:
                self.cpu_label.configure(foreground="green")
            
            # Memory
            mem_percent = sys_stats.get("memory_percent", 0)
            self.mem_var.set(f"{mem_percent:.1f}%")
            self.mem_bar['value'] = mem_percent
            
            # Color coding for Memory
            if mem_percent > 85:
                self.mem_label.configure(foreground="red")
            elif mem_percent > 70:
                self.mem_label.configure(foreground="orange")
            else:
                self.mem_label.configure(foreground="green")
            
            # GPU
            gpu_percent = sys_stats.get("gpu_percent")
            if gpu_percent is not None:
                self.gpu_var.set(f"{gpu_percent:.1f}%")
                self.gpu_bar['value'] = gpu_percent
                
                # Color coding for GPU
                if gpu_percent > 90:
                    self.gpu_label.configure(foreground="red")
                elif gpu_percent > 75:
                    self.gpu_label.configure(foreground="orange")
                else:
                    self.gpu_label.configure(foreground="green")
            else:
                self.gpu_var.set("N/A")
                self.gpu_bar['value'] = 0
                self.gpu_label.configure(foreground="gray")
            
            # RAG operations
            rag_stats = stats.get("rag_operations", {})
            total_ops = sum(op.get("count", 0) for op in rag_stats.values())
            self.rag_ops_var.set(f"{total_ops:,} total")
            
            # Average response time
            total_time = 0
            total_count = 0
            for op_stats in rag_stats.values():
                avg_time = op_stats.get("avg_duration_ms", 0)
                count = op_stats.get("count", 0)
                total_time += avg_time * count
                total_count += count
            
            avg_response = total_time / total_count if total_count > 0 else 0
            self.rag_time_var.set(f"{avg_response:.0f} ms")
            
            # Monitor status
            if self.monitor.running:
                self.status_var.set("●")
                self.status_label.configure(foreground="green")
            else:
                self.status_var.set("●")
                self.status_label.configure(foreground="red")
                
        except Exception as e:
            print(f"Error updating performance metrics: {e}")
        
        # Schedule next update
        if self.update_running:
            self.parent.after(2000, self._update_metrics)  # Update every 2 seconds
    
    def _open_dashboard(self):
        """Open the Streamlit dashboard"""
        try:
            # Check if dashboard is already running
            if self._is_dashboard_running():
                webbrowser.open("http://localhost:8501")
                return
            
            # Start dashboard in background
            dashboard_script = os.path.join(os.path.dirname(__file__), "performance_dashboard.py")
            
            if os.path.exists(dashboard_script):
                # Start Streamlit dashboard
                subprocess.Popen([
                    sys.executable, "-m", "streamlit", "run", 
                    dashboard_script, "--server.port", "8501",
                    "--server.address", "localhost"
                ], 
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                
                # Wait a moment then open browser
                self.parent.after(3000, lambda: webbrowser.open("http://localhost:8501"))
                
                messagebox.showinfo(
                    "Dashboard Starting", 
                    "Performance dashboard is starting...\nIt will open in your browser in a few seconds."
                )
            else:
                messagebox.showerror(
                    "Dashboard Error", 
                    f"Dashboard script not found: {dashboard_script}"
                )
                
        except Exception as e:
            messagebox.showerror("Dashboard Error", f"Failed to start dashboard: {e}")
    
    def _is_dashboard_running(self):
        """Check if dashboard is already running on port 8501"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('localhost', 8501))
            sock.close()
            return result == 0
        except:
            return False
    
    def _export_metrics(self):
        """Export performance metrics"""
        try:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"performance_export_{timestamp}.json"
            
            self.monitor.export_metrics(filename, hours_back=24)
            
            messagebox.showinfo(
                "Export Complete", 
                f"Performance metrics exported to:\n{filename}"
            )
            
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export metrics: {e}")
    
    def stop_updates(self):
        """Stop metric updates"""
        self.update_running = False

def add_performance_widget_to_gui(parent_frame):
    """Add performance widget to existing GUI"""
    try:
        widget = PerformanceWidget(parent_frame)
        return widget
    except Exception as e:
        print(f"Failed to add performance widget: {e}")
        return None

# Integration function for main GUI
def integrate_performance_monitoring(gui_instance):
    """
    Integrate performance monitoring into existing GUI
    Call this from your main GUI initialization
    """
    try:
        # Add performance widget to main window
        if hasattr(gui_instance, 'main_frame') or hasattr(gui_instance, 'root'):
            parent = getattr(gui_instance, 'main_frame', getattr(gui_instance, 'root', None))
            if parent:
                widget = add_performance_widget_to_gui(parent)
                
                # Store reference for cleanup
                if hasattr(gui_instance, 'performance_widget'):
                    gui_instance.performance_widget = widget
                
                return widget
    except Exception as e:
        print(f"Performance monitoring integration failed: {e}")
    
    return None

if __name__ == "__main__":
    # Standalone test
    root = tk.Tk()
    root.title("Performance Monitor Test")
    root.geometry("600x300")
    
    widget = PerformanceWidget(root)
    
    def on_closing():
        widget.stop_updates()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
