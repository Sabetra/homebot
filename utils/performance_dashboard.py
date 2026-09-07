#!/usr/bin/env python3
"""
PERFORMANCE MONITORING DASHBOARD
Real-time Streamlit dashboard for RAG Backend System

Features:
- Real-time system and RAG metrics visualization
- Interactive charts and gauges
- Historical data analysis
- Performance alerts display
- Metrics export functionality
- System health overview
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import time
import json
from datetime import datetime, timedelta
from performance_monitor import get_performance_monitor, PerformanceMonitor
import os

# Page configuration
st.set_page_config(
    page_title="RAG System Performance Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
<style>
    .metric-card {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        padding: 1rem;
        border-radius: 10px;
        color: white;
        margin: 0.5rem 0;
    }
    .alert-critical {
        background-color: #ff4444;
        color: white;
        padding: 10px;
        border-radius: 5px;
        margin: 5px 0;
    }
    .alert-warning {
        background-color: #ffaa00;
        color: white;
        padding: 10px;
        border-radius: 5px;
        margin: 5px 0;
    }
    .alert-info {
        background-color: #00aaff;
        color: white;
        padding: 10px;
        border-radius: 5px;
        margin: 5px 0;
    }
    .status-good {
        color: #00aa00;
        font-weight: bold;
    }
    .status-warning {
        color: #ffaa00;
        font-weight: bold;
    }
    .status-critical {
        color: #ff4444;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

class PerformanceDashboard:
    """Main dashboard class"""
    
    def __init__(self):
        self.monitor = get_performance_monitor()
        
    def run(self):
        """Main dashboard runner"""
        st.title("🚀 RAG System Performance Dashboard")
        st.markdown("---")
        
        # Sidebar controls
        self._render_sidebar()
        
        # Main content tabs
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "🏠 Overview", 
            "💻 System Metrics", 
            "🧠 RAG Operations", 
            "📈 Historical Analysis", 
            "⚙️ Settings"
        ])
        
        with tab1:
            self._render_overview()
        
        with tab2:
            self._render_system_metrics()
            
        with tab3:
            self._render_rag_metrics()
            
        with tab4:
            self._render_historical_analysis()
            
        with tab5:
            self._render_settings()
    
    def _render_sidebar(self):
        """Render sidebar controls"""
        st.sidebar.title("Dashboard Controls")
        
        # Auto-refresh toggle
        auto_refresh = st.sidebar.checkbox("Auto Refresh", value=True)
        refresh_interval = st.sidebar.slider("Refresh Interval (seconds)", 1, 30, 5)
        
        # Manual refresh button
        if st.sidebar.button("🔄 Refresh Now"):
            st.experimental_rerun()
        
        # Export options
        st.sidebar.markdown("### 📥 Export Data")
        export_hours = st.sidebar.selectbox("Hours to Export", [1, 6, 12, 24, 48, 168])
        
        if st.sidebar.button("📁 Export Metrics"):
            self._export_metrics(export_hours)
        
        # System info
        st.sidebar.markdown("### 💻 System Info")
        stats = self.monitor.get_current_stats()
        if stats["system"]:
            st.sidebar.metric("CPU Usage", f"{stats['system']['cpu_percent']:.1f}%")
            st.sidebar.metric("Memory Usage", f"{stats['system']['memory_percent']:.1f}%")
            if stats["system"]["gpu_percent"] is not None:
                st.sidebar.metric("GPU Usage", f"{stats['system']['gpu_percent']:.1f}%")
        
        # Auto-refresh implementation
        if auto_refresh:
            time.sleep(refresh_interval)
            st.experimental_rerun()
    
    def _render_overview(self):
        """Render overview dashboard"""
        col1, col2, col3, col4 = st.columns(4)
        
        stats = self.monitor.get_current_stats()
        
        # System health indicator
        health_status = self._get_system_health(stats)
        
        with col1:
            st.markdown(f"""
            <div class="metric-card">
                <h3>System Health</h3>
                <h2 class="{health_status['class']}">{health_status['status']}</h2>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            active_ops = sum(stats["rag_operations"].get(op, {}).get("count", 0) 
                           for op in stats["rag_operations"])
            st.markdown(f"""
            <div class="metric-card">
                <h3>Total Operations</h3>
                <h2>{active_ops:,}</h2>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            avg_response = self._get_average_response_time(stats)
            st.markdown(f"""
            <div class="metric-card">
                <h3>Avg Response Time</h3>
                <h2>{avg_response:.0f} ms</h2>
            </div>
            """, unsafe_allow_html=True)
        
        with col4:
            uptime = self._get_uptime()
            st.markdown(f"""
            <div class="metric-card">
                <h3>Uptime</h3>
                <h2>{uptime}</h2>
            </div>
            """, unsafe_allow_html=True)
        
        # Current alerts
        self._render_alerts()
        
        # Quick system overview charts
        col1, col2 = st.columns(2)
        
        with col1:
            self._render_system_overview_chart()
        
        with col2:
            self._render_rag_overview_chart()
    
    def _render_system_metrics(self):
        """Render detailed system metrics"""
        st.header("💻 System Resource Monitoring")
        
        # Real-time gauges
        col1, col2, col3 = st.columns(3)
        
        stats = self.monitor.get_current_stats()
        sys_stats = stats.get("system", {})
        
        with col1:
            fig_cpu = go.Figure(go.Indicator(
                mode="gauge+number+delta",
                value=sys_stats.get("cpu_percent", 0),
                domain={'x': [0, 1], 'y': [0, 1]},
                title={'text': "CPU Usage (%)"},
                gauge={
                    'axis': {'range': [None, 100]},
                    'bar': {'color': "darkblue"},
                    'steps': [
                        {'range': [0, 50], 'color': "lightgray"},
                        {'range': [50, 80], 'color': "yellow"},
                        {'range': [80, 100], 'color': "red"}
                    ],
                    'threshold': {
                        'line': {'color': "red", 'width': 4},
                        'thickness': 0.75,
                        'value': 90
                    }
                }
            ))
            fig_cpu.update_layout(height=300)
            st.plotly_chart(fig_cpu, )
        
        with col2:
            fig_mem = go.Figure(go.Indicator(
                mode="gauge+number",
                value=sys_stats.get("memory_percent", 0),
                domain={'x': [0, 1], 'y': [0, 1]},
                title={'text': "Memory Usage (%)"},
                gauge={
                    'axis': {'range': [None, 100]},
                    'bar': {'color': "darkgreen"},
                    'steps': [
                        {'range': [0, 60], 'color': "lightgray"},
                        {'range': [60, 85], 'color': "yellow"},
                        {'range': [85, 100], 'color': "red"}
                    ],
                    'threshold': {
                        'line': {'color': "red", 'width': 4},
                        'thickness': 0.75,
                        'value': 90
                    }
                }
            ))
            fig_mem.update_layout(height=300)
            st.plotly_chart(fig_mem, )
        
        with col3:
            if sys_stats.get("gpu_percent") is not None:
                fig_gpu = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=sys_stats.get("gpu_percent", 0),
                    domain={'x': [0, 1], 'y': [0, 1]},
                    title={'text': "GPU Usage (%)"},
                    gauge={
                        'axis': {'range': [None, 100]},
                        'bar': {'color': "purple"},
                        'steps': [
                            {'range': [0, 70], 'color': "lightgray"},
                            {'range': [70, 90], 'color': "yellow"},
                            {'range': [90, 100], 'color': "red"}
                        ],
                        'threshold': {
                            'line': {'color': "red", 'width': 4},
                            'thickness': 0.75,
                            'value': 95
                        }
                    }
                ))
                fig_gpu.update_layout(height=300)
                st.plotly_chart(fig_gpu, )
            else:
                st.info("GPU monitoring not available")
        
        # Historical system metrics chart
        self._render_system_history_chart()
    
    def _render_rag_metrics(self):
        """Render RAG operation metrics"""
        st.header("🧠 RAG Operation Performance")
        
        stats = self.monitor.get_current_stats()
        rag_stats = stats.get("rag_operations", {})
        
        if not rag_stats:
            st.info("No RAG operations recorded yet")
            return
        
        # Operation summary cards
        cols = st.columns(len(rag_stats))
        for i, (op_type, op_stats) in enumerate(rag_stats.items()):
            with cols[i]:
                avg_duration = op_stats.get("avg_duration_ms", 0)
                count = op_stats.get("count", 0)
                
                status_class = "status-good"
                if avg_duration > 2000:
                    status_class = "status-warning"
                if avg_duration > 5000:
                    status_class = "status-critical"
                
                st.markdown(f"""
                <div class="metric-card">
                    <h4>{op_type.title()}</h4>
                    <h3>{count:,} ops</h3>
                    <p class="{status_class}">{avg_duration:.0f} ms avg</p>
                </div>
                """, unsafe_allow_html=True)
        
        # Operation performance chart
        self._render_rag_performance_chart()
        
        # Recent operations table
        st.subheader("Recent Operations")
        recent_data = self._get_recent_rag_operations()
        if recent_data:
            df = pd.DataFrame(recent_data)
            st.dataframe(df, )
    
    def _render_historical_analysis(self):
        """Render historical analysis"""
        st.header("📈 Historical Performance Analysis")
        
        # Time range selector
        col1, col2 = st.columns(2)
        with col1:
            hours_back = st.selectbox("Analysis Period", [1, 6, 12, 24, 48, 168], index=3)
        with col2:
            metric_type = st.selectbox("Metric Type", ["System", "RAG Operations", "Both"])
        
        # Get historical data
        historical_data = self.monitor.get_historical_data(hours_back)
        
        if metric_type in ["System", "Both"]:
            self._render_historical_system_chart(historical_data, hours_back)
        
        if metric_type in ["RAG Operations", "Both"]:
            self._render_historical_rag_chart(historical_data, hours_back)
        
        # Performance summary statistics
        self._render_performance_summary(historical_data)
    
    def _render_settings(self):
        """Render settings panel"""
        st.header("⚙️ Dashboard Settings")
        
        # Monitor configuration
        st.subheader("Monitor Configuration")
        
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("Database Path", value=self.monitor.db_path, disabled=True)
            st.checkbox("Monitor Running", value=self.monitor.running, disabled=True)
        
        with col2:
            if st.button("🏁 Stop Monitor"):
                self.monitor.stop_monitoring()
                st.success("Monitor stopped")
            
            if st.button("▶️ Start Monitor"):
                if not self.monitor.running:
                    self.monitor.start_monitoring()
                    st.success("Monitor started")
                else:
                    st.warning("Monitor already running")
        
        # Alert configuration
        st.subheader("Performance Alerts")
        
        alerts_data = []
        for alert in self.monitor.alerts:
            alerts_data.append({
                "Metric": alert.metric_name,
                "Threshold": alert.threshold,
                "Comparison": alert.comparison,
                "Severity": alert.severity,
                "Message": alert.message
            })
        
        if alerts_data:
            df_alerts = pd.DataFrame(alerts_data)
            st.dataframe(df_alerts, )
        
        # Database management
        st.subheader("Database Management")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("🗑️ Clear Old Data"):
                self._clear_old_data()
        
        with col2:
            if st.button("📊 Database Stats"):
                self._show_database_stats()
        
        with col3:
            if st.button("🔧 Optimize Database"):
                self._optimize_database()
    
    def _get_system_health(self, stats):
        """Determine overall system health"""
        sys_stats = stats.get("system", {})
        
        cpu = sys_stats.get("cpu_percent", 0)
        memory = sys_stats.get("memory_percent", 0)
        gpu = sys_stats.get("gpu_percent", 0) or 0
        
        if cpu > 90 or memory > 90 or gpu > 95:
            return {"status": "CRITICAL", "class": "status-critical"}
        elif cpu > 70 or memory > 80 or gpu > 85:
            return {"status": "WARNING", "class": "status-warning"}
        else:
            return {"status": "HEALTHY", "class": "status-good"}
    
    def _get_average_response_time(self, stats):
        """Calculate average response time across all operations"""
        rag_stats = stats.get("rag_operations", {})
        total_time = 0
        total_ops = 0
        
        for op_stats in rag_stats.values():
            avg_duration = op_stats.get("avg_duration_ms", 0)
            count = op_stats.get("count", 0)
            total_time += avg_duration * count
            total_ops += count
        
        return total_time / total_ops if total_ops > 0 else 0
    
    def _get_uptime(self):
        """Get system uptime (placeholder)"""
        return "24h 15m"  # TODO: Implement actual uptime tracking
    
    def _render_alerts(self):
        """Render current alerts"""
        st.subheader("🚨 Current Alerts")
        
        # TODO: Implement actual alert checking
        # For now, show placeholder
        st.info("No active alerts")
    
    def _render_system_overview_chart(self):
        """Render system overview chart"""
        st.subheader("System Resources (Real-time)")
        
        # Get recent system metrics
        if not self.monitor.recent_system_metrics:
            st.info("No system data available yet")
            return
        
        # Convert to DataFrame for plotting
        data = []
        for metric in list(self.monitor.recent_system_metrics)[-50:]:  # Last 50 readings
            data.append({
                "timestamp": datetime.fromtimestamp(metric.timestamp),
                "CPU": metric.cpu_percent,
                "Memory": metric.memory_percent,
                "GPU": metric.gpu_percent or 0
            })
        
        if data:
            df = pd.DataFrame(data)
            fig = px.line(df, x="timestamp", y=["CPU", "Memory", "GPU"],
                         title="System Resource Usage (%)")
            fig.update_layout(height=400)
            st.plotly_chart(fig, )
    
    def _render_rag_overview_chart(self):
        """Render RAG operations overview chart"""
        st.subheader("RAG Operations (Real-time)")
        
        if not self.monitor.recent_rag_metrics:
            st.info("No RAG operation data available yet")
            return
        
        # Convert to DataFrame for plotting
        data = []
        for metric in list(self.monitor.recent_rag_metrics)[-50:]:  # Last 50 operations
            data.append({
                "timestamp": datetime.fromtimestamp(metric.timestamp),
                "operation": metric.operation_type,
                "duration_ms": metric.duration_ms,
                "success": metric.success
            })
        
        if data:
            df = pd.DataFrame(data)
            fig = px.scatter(df, x="timestamp", y="duration_ms", color="operation",
                           size="duration_ms", title="Operation Response Times")
            fig.update_layout(height=400)
            st.plotly_chart(fig, )
    
    def _render_system_history_chart(self):
        """Render detailed system history chart"""
        st.subheader("System Resource History (Last Hour)")
        
        # Get recent data
        recent_data = []
        for metric in list(self.monitor.recent_system_metrics):
            recent_data.append({
                "timestamp": datetime.fromtimestamp(metric.timestamp),
                "CPU": metric.cpu_percent,
                "Memory": metric.memory_percent,
                "GPU": metric.gpu_percent or 0,
                "Disk": metric.disk_percent
            })
        
        if recent_data:
            df = pd.DataFrame(recent_data)
            
            # Create subplots
            fig = make_subplots(rows=2, cols=2,
                              subplot_titles=('CPU Usage', 'Memory Usage', 'GPU Usage', 'Disk Usage'))
            
            fig.add_trace(go.Scatter(x=df["timestamp"], y=df["CPU"], name="CPU %"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df["timestamp"], y=df["Memory"], name="Memory %"), row=1, col=2)
            fig.add_trace(go.Scatter(x=df["timestamp"], y=df["GPU"], name="GPU %"), row=2, col=1)
            fig.add_trace(go.Scatter(x=df["timestamp"], y=df["Disk"], name="Disk %"), row=2, col=2)
            
            fig.update_layout(height=600, showlegend=False)
            st.plotly_chart(fig, )
    
    def _render_rag_performance_chart(self):
        """Render RAG performance chart"""
        st.subheader("Operation Performance Distribution")
        
        if not self.monitor.recent_rag_metrics:
            st.info("No RAG operation data available yet")
            return
        
        # Convert to DataFrame
        data = []
        for metric in self.monitor.recent_rag_metrics:
            data.append({
                "operation": metric.operation_type,
                "duration_ms": metric.duration_ms,
                "success": metric.success,
                "timestamp": datetime.fromtimestamp(metric.timestamp)
            })
        
        if data:
            df = pd.DataFrame(data)
            
            # Box plot of operation durations by type
            fig = px.box(df, x="operation", y="duration_ms", 
                        title="Operation Duration Distribution by Type")
            fig.update_layout(height=400)
            st.plotly_chart(fig, )
    
    def _get_recent_rag_operations(self):
        """Get recent RAG operations for table display"""
        if not self.monitor.recent_rag_metrics:
            return []
        
        recent = list(self.monitor.recent_rag_metrics)[-20:]  # Last 20 operations
        data = []
        
        for metric in recent:
            data.append({
                "Timestamp": datetime.fromtimestamp(metric.timestamp).strftime("%H:%M:%S"),
                "Operation": metric.operation_type,
                "Duration (ms)": f"{metric.duration_ms:.1f}",
                "Success": "✅" if metric.success else "❌",
                "Results": metric.results_count or "N/A"
            })
        
        return data
    
    def _render_historical_system_chart(self, historical_data, hours_back):
        """Render historical system metrics chart"""
        st.subheader(f"System Metrics - Last {hours_back} Hours")
        
        sys_data = historical_data.get("system_metrics", [])
        if not sys_data:
            st.info("No historical system data available")
            return
        
        df = pd.DataFrame(sys_data)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        
        fig = px.line(df, x="timestamp", y=["cpu_percent", "memory_percent"],
                     title="Historical System Resource Usage")
        fig.update_layout(height=400)
        st.plotly_chart(fig, )
    
    def _render_historical_rag_chart(self, historical_data, hours_back):
        """Render historical RAG metrics chart"""
        st.subheader(f"RAG Operations - Last {hours_back} Hours")
        
        rag_data = historical_data.get("rag_metrics", [])
        if not rag_data:
            st.info("No historical RAG data available")
            return
        
        df = pd.DataFrame(rag_data)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        
        # Operations over time
        fig = px.histogram(df, x="timestamp", color="operation_type",
                          title="Operations Over Time")
        fig.update_layout(height=400)
        st.plotly_chart(fig, )
        
        # Average duration by operation type
        avg_duration = df.groupby("operation_type")["duration_ms"].mean().reset_index()
        fig2 = px.bar(avg_duration, x="operation_type", y="duration_ms",
                     title="Average Duration by Operation Type")
        fig2.update_layout(height=400)
        st.plotly_chart(fig2, )
    
    def _render_performance_summary(self, historical_data):
        """Render performance summary statistics"""
        st.subheader("Performance Summary")
        
        sys_data = historical_data.get("system_metrics", [])
        rag_data = historical_data.get("rag_metrics", [])
        
        col1, col2 = st.columns(2)
        
        with col1:
            if sys_data:
                sys_df = pd.DataFrame(sys_data)
                st.markdown("**System Metrics Summary:**")
                st.markdown(f"- Avg CPU: {sys_df['cpu_percent'].mean():.1f}%")
                st.markdown(f"- Max CPU: {sys_df['cpu_percent'].max():.1f}%")
                st.markdown(f"- Avg Memory: {sys_df['memory_percent'].mean():.1f}%")
                st.markdown(f"- Max Memory: {sys_df['memory_percent'].max():.1f}%")
        
        with col2:
            if rag_data:
                rag_df = pd.DataFrame(rag_data)
                st.markdown("**RAG Operations Summary:**")
                st.markdown(f"- Total Operations: {len(rag_df):,}")
                st.markdown(f"- Success Rate: {(rag_df['success'].sum() / len(rag_df) * 100):.1f}%")
                st.markdown(f"- Avg Duration: {rag_df['duration_ms'].mean():.1f} ms")
                st.markdown(f"- Max Duration: {rag_df['duration_ms'].max():.1f} ms")
    
    def _export_metrics(self, hours_back):
        """Export metrics to file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"performance_export_{timestamp}.json"
        
        try:
            self.monitor.export_metrics(filename, hours_back)
            st.success(f"Metrics exported to {filename}")
            
            # Offer download
            with open(filename, 'r') as f:
                st.download_button(
                    label="📥 Download Export File",
                    data=f.read(),
                    file_name=filename,
                    mime="application/json"
                )
        except Exception as e:
            st.error(f"Export failed: {e}")
    
    def _clear_old_data(self):
        """Clear old data from database"""
        # TODO: Implement database cleanup
        st.success("Old data cleared (placeholder)")
    
    def _show_database_stats(self):
        """Show database statistics"""
        # TODO: Implement database statistics
        st.info("Database statistics (placeholder)")
    
    def _optimize_database(self):
        """Optimize database"""
        # TODO: Implement database optimization
        st.success("Database optimized (placeholder)")

def main():
    """Main dashboard application"""
    try:
        dashboard = PerformanceDashboard()
        dashboard.run()
    except Exception as e:
        st.error(f"Dashboard error: {e}")
        st.exception(e)

if __name__ == "__main__":
    main()
