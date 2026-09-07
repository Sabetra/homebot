"""
Performance monitoring tab for system metrics and optimization.
Provides real-time monitoring of CPU, memory, GPU usage and application performance.
"""

import logging
import sys
import psutil
from typing import Dict, Any, Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox, 
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QWidget, QScrollArea
)
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QFont

from ..widgets import BaseTab, StyledLabel, MonospaceTextWidget
from ..config import UIConstants, PerformanceSettings

logger = logging.getLogger(__name__)

# Check for GPU monitoring capabilities
try:
    import GPUtil
    GPU_MONITORING_AVAILABLE = True
    gputil_module = GPUtil
except ImportError:
    GPU_MONITORING_AVAILABLE = False
    gputil_module = None
    logger.info("GPUtil not available - GPU monitoring disabled")

try:
    import torch
    TORCH_AVAILABLE = True
    torch_module = torch
except ImportError:
    TORCH_AVAILABLE = False
    torch_module = None


class PerformanceTab(BaseTab):
    """Performance monitoring and system metrics tab."""
    
    # Signals
    refresh_requested = Signal()
    auto_refresh_toggled = Signal(bool)
    
    def __init__(self, parent=None):
        self.performance_settings = PerformanceSettings()
        self.auto_refresh_timer = QTimer()
        self.auto_refresh_timer.timeout.connect(self._auto_refresh)
        super().__init__(UIConstants.TAB_PERFORMANCE, parent)
    
    def setup_ui(self):
        """Setup the performance monitoring interface."""
        # Make the tab scrollable
        scroll_area = QScrollArea()
        scroll_widget = self._create_scroll_content()
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        self.main_layout.addWidget(scroll_area)
    
    def _create_scroll_content(self) -> QWidget:
        """Create the scrollable content widget."""
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        
        # Control Panel
        self._setup_control_panel(content_layout)
        
        # System Overview
        self._setup_system_overview(content_layout)
        
        # CPU & Memory Monitoring
        self._setup_cpu_memory_monitoring(content_layout)
        
        # GPU Monitoring (if available)
        if GPU_MONITORING_AVAILABLE:
            self._setup_gpu_monitoring(content_layout)
        
        # Application Performance
        self._setup_app_performance(content_layout)
        
        # Process Information
        self._setup_process_info(content_layout)
        
        return content_widget
    
    def _setup_control_panel(self, layout: QVBoxLayout):
        """Setup performance monitoring controls."""
        control_group = QGroupBox("Monitoring Controls")
        control_layout = QHBoxLayout(control_group)
        
        # Refresh button
        self.refresh_button = QPushButton("Aktualisieren")
        self.refresh_button.clicked.connect(self._manual_refresh)
        control_layout.addWidget(self.refresh_button)
        
        # Auto-refresh toggle
        self.auto_refresh_button = QPushButton("Auto-Refresh: Aus")
        self.auto_refresh_button.setCheckable(True)
        self.auto_refresh_button.toggled.connect(self._toggle_auto_refresh)
        control_layout.addWidget(self.auto_refresh_button)
        
        # Status indicator
        self.monitoring_status = StyledLabel("Monitoring: Bereit", "success")
        control_layout.addWidget(self.monitoring_status)
        
        control_layout.addStretch()
        
        layout.addWidget(control_group)
    
    def _setup_system_overview(self, layout: QVBoxLayout):
        """Setup system overview section."""
        overview_group = QGroupBox("System Overview")
        overview_layout = QVBoxLayout(overview_group)
        
        # System info
        self.system_info_label = MonospaceTextWidget()
        self.system_info_label.setMaximumHeight(120)
        overview_layout.addWidget(self.system_info_label)
        
        # Load system info initially
        self._update_system_info()
        
        layout.addWidget(overview_group)
    
    def _setup_cpu_memory_monitoring(self, layout: QVBoxLayout):
        """Setup CPU and memory monitoring."""
        cpu_mem_group = QGroupBox("CPU & Memory")
        cpu_mem_layout = QVBoxLayout(cpu_mem_group)
        
        # CPU Usage
        cpu_layout = QHBoxLayout()
        cpu_layout.addWidget(QLabel("CPU Usage:"))
        self.cpu_progress = QProgressBar()
        self.cpu_progress.setRange(0, 100)
        self.cpu_label = QLabel("0%")
        cpu_layout.addWidget(self.cpu_progress)
        cpu_layout.addWidget(self.cpu_label)
        cpu_mem_layout.addLayout(cpu_layout)
        
        # Memory Usage
        memory_layout = QHBoxLayout()
        memory_layout.addWidget(QLabel("Memory Usage:"))
        self.memory_progress = QProgressBar()
        self.memory_progress.setRange(0, 100)
        self.memory_label = QLabel("0%")
        memory_layout.addWidget(self.memory_progress)
        memory_layout.addWidget(self.memory_label)
        cpu_mem_layout.addLayout(memory_layout)
        
        # Disk Usage
        disk_layout = QHBoxLayout()
        disk_layout.addWidget(QLabel("Disk Usage:"))
        self.disk_progress = QProgressBar()
        self.disk_progress.setRange(0, 100)
        self.disk_label = QLabel("0%")
        disk_layout.addWidget(self.disk_progress)
        disk_layout.addWidget(self.disk_label)
        cpu_mem_layout.addLayout(disk_layout)
        
        layout.addWidget(cpu_mem_group)
    
    def _setup_gpu_monitoring(self, layout: QVBoxLayout):
        """Setup GPU monitoring section."""
        gpu_group = QGroupBox("GPU Monitoring")
        gpu_layout = QVBoxLayout(gpu_group)
        
        if not GPU_MONITORING_AVAILABLE:
            gpu_layout.addWidget(QLabel("GPU monitoring not available (GPUtil not installed)"))
        else:
            # GPU table
            self.gpu_table = QTableWidget()
            self.gpu_table.setColumnCount(4)
            self.gpu_table.setHorizontalHeaderLabels(["GPU", "Usage", "Memory", "Temperature"])
            self.gpu_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.gpu_table.setMaximumHeight(150)
            gpu_layout.addWidget(self.gpu_table)
            
            # PyTorch GPU info
            if TORCH_AVAILABLE:
                self.torch_gpu_info = MonospaceTextWidget()
                self.torch_gpu_info.setMaximumHeight(80)
                gpu_layout.addWidget(QLabel("PyTorch GPU Info:"))
                gpu_layout.addWidget(self.torch_gpu_info)
        
        layout.addWidget(gpu_group)
    
    def _setup_app_performance(self, layout: QVBoxLayout):
        """Setup application performance monitoring."""
        app_group = QGroupBox("Application Performance")
        app_layout = QVBoxLayout(app_group)
        
        # Performance metrics
        metrics_layout = QHBoxLayout()
        
        # Response time
        metrics_layout.addWidget(QLabel("Avg Response Time:"))
        self.response_time_label = StyledLabel("N/A", "default")
        metrics_layout.addWidget(self.response_time_label)
        
        # Memory usage by app
        metrics_layout.addWidget(QLabel("App Memory:"))
        self.app_memory_label = StyledLabel("N/A", "default")
        metrics_layout.addWidget(self.app_memory_label)
        
        # Thread count
        metrics_layout.addWidget(QLabel("Active Threads:"))
        self.thread_count_label = StyledLabel("N/A", "default")
        metrics_layout.addWidget(self.thread_count_label)
        
        metrics_layout.addStretch()
        app_layout.addLayout(metrics_layout)
        
        # Performance log
        app_layout.addWidget(QLabel("Performance Log:"))
        self.performance_log = MonospaceTextWidget()
        self.performance_log.setMaximumHeight(100)
        app_layout.addWidget(self.performance_log)
        
        layout.addWidget(app_group)
    
    def _setup_process_info(self, layout: QVBoxLayout):
        """Setup process information section."""
        process_group = QGroupBox("Process Information")
        process_layout = QVBoxLayout(process_group)
        
        # Process table
        self.process_table = QTableWidget()
        self.process_table.setColumnCount(5)
        self.process_table.setHorizontalHeaderLabels(["PID", "Name", "CPU%", "Memory%", "Status"])
        self.process_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.process_table.setMaximumHeight(200)
        process_layout.addWidget(self.process_table)
        
        layout.addWidget(process_group)
    
    def _manual_refresh(self):
        """Handle manual refresh request."""
        self.monitoring_status.setText("🔄 Aktualisiere...")
        self.monitoring_status.apply_style("default")
        self._update_all_metrics()
        self.monitoring_status.setText("✅ Aktualisiert")
        self.monitoring_status.apply_style("success")
        self.refresh_requested.emit()
    
    def _toggle_auto_refresh(self, enabled: bool):
        """Toggle auto-refresh functionality."""
        if enabled:
            self.auto_refresh_timer.start(self.performance_settings.auto_refresh_interval)
            self.auto_refresh_button.setText("Auto-Refresh: An")
            self.monitoring_status.setText("✅ Auto-Refresh aktiv")
            self.monitoring_status.apply_style("success")
        else:
            self.auto_refresh_timer.stop()
            self.auto_refresh_button.setText("Auto-Refresh: Aus")
            self.monitoring_status.setText("✅ Auto-Refresh inaktiv")
            self.monitoring_status.apply_style("success")
        
        self.auto_refresh_toggled.emit(enabled)
    
    def _auto_refresh(self):
        """Handle automatic refresh."""
        self._update_all_metrics()
    
    def _update_all_metrics(self):
        """Update all performance metrics."""
        try:
            self._update_cpu_memory_metrics()
            if GPU_MONITORING_AVAILABLE:
                self._update_gpu_metrics()
            self._update_app_performance()
            self._update_process_info()
            
        except Exception as e:
            logger.error(f"Error updating metrics: {e}")
            self.monitoring_status.setText("❌ Fehler beim Aktualisieren")
            self.monitoring_status.apply_style("error")
    
    def _update_system_info(self):
        """Update system information."""
        try:
            info = f"""System Information:
Platform: {sys.platform}
Python: {sys.version.split()[0]}
CPU Cores: {psutil.cpu_count(logical=False)} physical, {psutil.cpu_count(logical=True)} logical
Total Memory: {psutil.virtual_memory().total / (1024**3):.1f} GB
Architecture: {sys.maxsize > 2**32 and '64-bit' or '32-bit'}"""
            
            self.system_info_label.setPlainText(info)
            
        except Exception as e:
            logger.error(f"Error updating system info: {e}")
    
    def _update_cpu_memory_metrics(self):
        """Update CPU and memory usage metrics."""
        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.1)
            self.cpu_progress.setValue(int(cpu_percent))
            self.cpu_label.setText(f"{cpu_percent:.1f}%")
            
            # Memory usage
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            self.memory_progress.setValue(int(memory_percent))
            self.memory_label.setText(f"{memory_percent:.1f}% ({memory.used / (1024**3):.1f} GB)")
            
            # Disk usage
            disk = psutil.disk_usage('/')
            disk_percent = (disk.used / disk.total) * 100
            self.disk_progress.setValue(int(disk_percent))
            self.disk_label.setText(f"{disk_percent:.1f}% ({disk.used / (1024**3):.1f} GB)")
            
        except Exception as e:
            logger.error(f"Error updating CPU/Memory metrics: {e}")
    
    def _update_gpu_metrics(self):
        """Update GPU metrics if available."""
        if not GPU_MONITORING_AVAILABLE:
            return
        
        try:
            if not gputil_module:
                return
                
            gpus = gputil_module.getGPUs()
            self.gpu_table.setRowCount(len(gpus))
            
            for i, gpu in enumerate(gpus):
                self.gpu_table.setItem(i, 0, QTableWidgetItem(gpu.name))
                self.gpu_table.setItem(i, 1, QTableWidgetItem(f"{gpu.load * 100:.1f}%"))
                self.gpu_table.setItem(i, 2, QTableWidgetItem(f"{gpu.memoryUtil * 100:.1f}%"))
                self.gpu_table.setItem(i, 3, QTableWidgetItem(f"{gpu.temperature}°C"))
            
            # PyTorch GPU info
            if TORCH_AVAILABLE and torch_module and torch_module.cuda.is_available():
                torch_info = f"""PyTorch CUDA Info:
Available: {torch_module.cuda.is_available()}
Device Count: {torch_module.cuda.device_count()}
Current Device: {torch_module.cuda.current_device()}
Memory Allocated: {torch_module.cuda.memory_allocated() / (1024**2):.1f} MB
Memory Cached: {torch_module.cuda.memory_reserved() / (1024**2):.1f} MB"""
                self.torch_gpu_info.setPlainText(torch_info)
            
        except Exception as e:
            logger.error(f"Error updating GPU metrics: {e}")
    
    def _update_app_performance(self):
        """Update application-specific performance metrics."""
        try:
            # Get current process
            process = psutil.Process()
            
            # Memory usage by this process
            memory_info = process.memory_info()
            app_memory_mb = memory_info.rss / (1024 * 1024)
            self.app_memory_label.setText(f"{app_memory_mb:.1f} MB")
            
            # Thread count
            thread_count = process.num_threads()
            self.thread_count_label.setText(str(thread_count))
            
            # Log performance data
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] Memory: {app_memory_mb:.1f}MB, Threads: {thread_count}"
            
            current_log = self.performance_log.toPlainText()
            log_lines = current_log.split('\n') if current_log else []
            log_lines.insert(0, log_entry)
            
            # Keep only recent entries
            if len(log_lines) > self.performance_settings.max_log_entries:
                log_lines = log_lines[:self.performance_settings.max_log_entries]
            
            self.performance_log.setPlainText('\n'.join(log_lines))
            
        except Exception as e:
            logger.error(f"Error updating app performance: {e}")
    
    def _update_process_info(self):
        """Update process information table."""
        try:
            # Get top processes by CPU usage
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status']):
                try:
                    processes.append(proc.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            # Sort by CPU usage and take top 10
            processes.sort(key=lambda x: x['cpu_percent'] or 0, reverse=True)
            top_processes = processes[:10]
            
            self.process_table.setRowCount(len(top_processes))
            
            for i, proc in enumerate(top_processes):
                self.process_table.setItem(i, 0, QTableWidgetItem(str(proc['pid'])))
                self.process_table.setItem(i, 1, QTableWidgetItem(proc['name'] or 'N/A'))
                self.process_table.setItem(i, 2, QTableWidgetItem(f"{proc['cpu_percent'] or 0:.1f}%"))
                self.process_table.setItem(i, 3, QTableWidgetItem(f"{proc['memory_percent'] or 0:.1f}%"))
                self.process_table.setItem(i, 4, QTableWidgetItem(proc['status'] or 'N/A'))
            
        except Exception as e:
            logger.error(f"Error updating process info: {e}")
    
    def set_response_time(self, response_time: float):
        """Update the average response time display."""
        self.response_time_label.setText(f"{response_time:.2f}s")
        
        # Color code based on performance
        if response_time < 1.0:
            self.response_time_label.apply_style("success")
        elif response_time < 3.0:
            self.response_time_label.apply_style("warning")
        else:
            self.response_time_label.apply_style("error")
    
    def cleanup(self):
        """Cleanup resources when tab is closed."""
        if self.auto_refresh_timer.isActive():
            self.auto_refresh_timer.stop()
