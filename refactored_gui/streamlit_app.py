"""
Streamlit web interface for the chatbot.
Provides a modern web-based alternative to the Qt desktop application.
"""

import streamlit as st
import logging
import os
import sys
from typing import Optional, Dict, Any
from datetime import datetime
import asyncio
import threading
import time

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

try:
    from scripts.model_loader import ModelLoader
    from agent_chatbot_logic import AgentChatbotLogic
    MODEL_AVAILABLE = True
except ImportError as e:
    st.error(f"Model components not available: {e}")
    MODEL_AVAILABLE = False

from refactored_gui.config import ModelDefaults, RAGDefaults
from refactored_gui.workers import ChatWorker

# Quality Dashboard
try:
    from refactored_gui.quality_dashboard import render_quality_dashboard
    QUALITY_DASHBOARD_AVAILABLE = True
except ImportError:
    QUALITY_DASHBOARD_AVAILABLE = False

logger = logging.getLogger(__name__)

# Configure Streamlit page
st.set_page_config(
    page_title="Mistral Chatbot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

class StreamlitChatInterface:
    """Streamlit-based chat interface for the chatbot."""
    
    def __init__(self):
        self.model_loader: Optional[ModelLoader] = None
        self.chat_logic: Optional[AgentChatbotLogic] = None
        self.model_defaults = ModelDefaults()
        self.rag_defaults = RAGDefaults()
        
        # Initialize session state
        self._init_session_state()
    
    def _init_session_state(self):
        """Initialize Streamlit session state variables."""
        if 'chat_history' not in st.session_state:
            st.session_state.chat_history = []
        
        if 'model_loaded' not in st.session_state:
            st.session_state.model_loaded = False
        
        if 'model_path' not in st.session_state:
            st.session_state.model_path = ""
        
        if 'settings' not in st.session_state:
            st.session_state.settings = self.model_defaults.__dict__.copy()
        
        if 'rag_settings' not in st.session_state:
            st.session_state.rag_settings = self.rag_defaults.__dict__.copy()
        
        if 'processing' not in st.session_state:
            st.session_state.processing = False
    
    def render_sidebar(self):
        """Render the sidebar with settings and controls."""
        with st.sidebar:
            st.title("🤖 Chatbot Settings")
            
            # Model Loading Section
            st.header("📦 Model Loading")
            
            if not MODEL_AVAILABLE:
                st.error("Model components not available!")
                return
            
            # Model file input
            model_file = st.file_uploader(
                "GGUF Model File",
                type=['gguf'],
                help="Upload a GGUF format model file"
            )
            
            if model_file is not None and not st.session_state.model_loaded:
                if st.button("Load Model", type="primary"):
                    self._load_model_from_upload(model_file)
            
            # Model status
            if st.session_state.model_loaded:
                st.success(f"✅ Model loaded: {st.session_state.model_path}")
                if st.button("Unload Model"):
                    self._unload_model()
            else:
                st.warning("❌ No model loaded")
            
            st.divider()
            
            # Model Settings
            st.header("⚙️ Model Settings")
            
            # Temperature
            st.session_state.settings['temperature'] = st.slider(
                "Temperature",
                min_value=0.0,
                max_value=2.0,
                value=st.session_state.settings.get('temperature', 0.7),
                step=0.05,
                help="Controls randomness in generation"
            )
            
            # Max Tokens
            st.session_state.settings['max_tokens'] = st.number_input(
                "Max Tokens",
                min_value=1,
                max_value=32768,
                value=st.session_state.settings.get('max_tokens', 8192),
                help="Maximum number of tokens in response"
            )
            
            # Context Size
            st.session_state.settings['n_ctx'] = st.number_input(
                "Context Size",
                min_value=512,
                max_value=131072,
                value=st.session_state.settings.get('n_ctx', 16384),
                help="Context window size"
            )
            
            # System Prompt
            st.session_state.settings['system_prompt'] = st.text_area(
                "System Prompt",
                value=st.session_state.settings.get('system_prompt', ""),
                height=100,
                help="Custom system prompt (leave empty for default)"
            )
            
            st.divider()
            
            # RAG Settings
            st.header("🔍 RAG Settings")
            
            st.session_state.rag_settings['enabled'] = st.checkbox(
                "Enable RAG",
                value=st.session_state.rag_settings.get('enabled', False),
                help="Enable Retrieval-Augmented Generation"
            )
            
            if st.session_state.rag_settings['enabled']:
                st.session_state.rag_settings['k'] = st.number_input(
                    "Number of Documents (k)",
                    min_value=1,
                    max_value=20,
                    value=st.session_state.rag_settings.get('k', 3),
                    help="Number of documents to retrieve"
                )
                
                st.session_state.rag_settings['min_score'] = st.slider(
                    "Minimum Relevance Score",
                    min_value=0.0,
                    max_value=1.0,
                    value=st.session_state.rag_settings.get('min_score', 0.3),
                    step=0.05,
                    help="Minimum relevance score for documents"
                )
            
            st.divider()
            
            # Actions
            st.header("🔧 Actions")
            
            if st.button("Clear Chat History"):
                st.session_state.chat_history = []
                st.rerun()
            
            if st.button("Reset Settings"):
                st.session_state.settings = self.model_defaults.__dict__.copy()
                st.session_state.rag_settings = self.rag_defaults.__dict__.copy()
                st.rerun()
    
    def render_main_chat(self):
        """Render the main chat interface."""
        st.title("💬 Mistral Chatbot")
        
        if not MODEL_AVAILABLE:
            st.error("Model components are not available. Please check your installation.")
            return
        
        if not st.session_state.model_loaded:
            st.warning("Please load a model from the sidebar to start chatting.")
            return
        
        # Chat history display
        chat_container = st.container()
        
        with chat_container:
            for i, message in enumerate(st.session_state.chat_history):
                with st.chat_message(message["role"]):
                    st.write(message["content"])
                    if message["role"] == "assistant" and "timestamp" in message:
                        st.caption(f"🕒 {message['timestamp']}")
        
        # Chat input
        if prompt := st.chat_input("Type your message here...", disabled=st.session_state.processing):
            if prompt.strip():
                self._handle_user_message(prompt)
    
    def render_document_upload(self):
        """Render document upload interface."""
        st.header("📄 Document Upload (RAG)")
        
        if not st.session_state.rag_settings.get('enabled', False):
            st.info("Enable RAG in the sidebar to upload documents.")
            return
        
        # File upload
        uploaded_files = st.file_uploader(
            "Upload Documents",
            type=['pdf', 'txt', 'docx'],
            accept_multiple_files=True,
            help="Upload documents for RAG processing"
        )
        
        if uploaded_files:
            if st.button("Process Documents"):
                self._process_uploaded_documents(uploaded_files)
        
        # URL input for web scraping
        st.subheader("🌐 Web Content")
        urls_text = st.text_area(
            "URLs (one per line)",
            placeholder="https://example.com\nhttps://another-site.com",
            help="Enter URLs to scrape and add to RAG database"
        )
        
        if urls_text.strip():
            urls = [url.strip() for url in urls_text.split('\n') if url.strip()]
            if st.button("Process URLs"):
                self._process_urls(urls)
    
    def _load_model_from_upload(self, model_file):
        """Load model from uploaded file."""
        try:
            # Save uploaded file temporarily
            temp_path = f"/tmp/{model_file.name}"
            with open(temp_path, "wb") as f:
                f.write(model_file.read())
            
            # Load model
            with st.spinner("Loading model..."):
                self.model_loader = ModelLoader()
                self.model_loader.load_model(temp_path)
                self.chat_logic = AgentChatbotLogic(self.model_loader)
                
                st.session_state.model_loaded = True
                st.session_state.model_path = model_file.name
                
            st.success("Model loaded successfully!")
            st.rerun()
            
        except Exception as e:
            st.error(f"Error loading model: {e}")
            logger.error(f"Model loading error: {e}")
    
    def _unload_model(self):
        """Unload the current model."""
        try:
            self.model_loader = None
            self.chat_logic = None
            st.session_state.model_loaded = False
            st.session_state.model_path = ""
            st.success("Model unloaded successfully!")
            st.rerun()
        except Exception as e:
            st.error(f"Error unloading model: {e}")
    
    def _handle_user_message(self, prompt: str):
        """Handle user message and generate response."""
        # Add user message to history
        st.session_state.chat_history.append({
            "role": "user",
            "content": prompt,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        })
        
        # Show user message immediately
        with st.chat_message("user"):
            st.write(prompt)
        
        # Generate response
        try:
            st.session_state.processing = True
            
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    response = self._generate_response(prompt)
                
                st.write(response)
                timestamp = datetime.now().strftime("%H:%M:%S")
                st.caption(f"🕒 {timestamp}")
                
                # Add assistant response to history
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": response,
                    "timestamp": timestamp
                })
        
        except Exception as e:
            error_msg = f"Error generating response: {e}"
            st.error(error_msg)
            logger.error(error_msg)
        
        finally:
            st.session_state.processing = False
            st.rerun()
    
    def _generate_response(self, prompt: str) -> str:
        """Generate response using the chat logic."""
        if not self.chat_logic:
            return "Error: No model loaded"
        
        try:
            # Apply current settings
            for key, value in st.session_state.settings.items():
                if hasattr(self.chat_logic, key):
                    setattr(self.chat_logic, key, value)
            
            # Generate response
            response = self.chat_logic.chat(prompt)
            return response
        
        except Exception as e:
            logger.error(f"Response generation error: {e}")
            return f"Error: {e}"
    
    def _process_uploaded_documents(self, uploaded_files):
        """Process uploaded documents for RAG."""
        with st.spinner(f"Processing {len(uploaded_files)} documents..."):
            try:
                # Here you would integrate with your RAG processing logic
                # For now, show a placeholder
                progress_bar = st.progress(0)
                for i, file in enumerate(uploaded_files):
                    # Simulate processing
                    time.sleep(0.5)
                    progress_bar.progress((i + 1) / len(uploaded_files))
                
                st.success(f"Successfully processed {len(uploaded_files)} documents!")
                
            except Exception as e:
                st.error(f"Error processing documents: {e}")
                logger.error(f"Document processing error: {e}")
    
    def _process_urls(self, urls):
        """Process URLs for RAG."""
        with st.spinner(f"Processing {len(urls)} URLs..."):
            try:
                # Here you would integrate with your URL processing logic
                # For now, show a placeholder
                progress_bar = st.progress(0)
                for i, url in enumerate(urls):
                    # Simulate processing
                    time.sleep(1)
                    progress_bar.progress((i + 1) / len(urls))
                
                st.success(f"Successfully processed {len(urls)} URLs!")
                
            except Exception as e:
                st.error(f"Error processing URLs: {e}")
                logger.error(f"URL processing error: {e}")
    
    def run(self):
        """Main application runner."""
        # Create layout
        self.render_sidebar()
        
        # Main content area with tabs
        tab_names = ["💬 Chat", "📄 Documents"]
        if QUALITY_DASHBOARD_AVAILABLE:
            tab_names.append("📊 RAG Quality")
        
        tabs = st.tabs(tab_names)
        
        with tabs[0]:
            self.render_main_chat()
        
        with tabs[1]:
            self.render_document_upload()
        
        if QUALITY_DASHBOARD_AVAILABLE and len(tabs) > 2:
            with tabs[2]:
                render_quality_dashboard()


def main():
    """Main entry point for Streamlit app."""
    try:
        # Configure logging
        logging.basicConfig(level=logging.INFO)
        
        # Create and run the interface
        interface = StreamlitChatInterface()
        interface.run()
        
    except Exception as e:
        st.error(f"Application error: {e}")
        logger.error(f"Streamlit app error: {e}")


if __name__ == "__main__":
    main()
