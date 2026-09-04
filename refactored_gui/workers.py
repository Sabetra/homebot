"""
Worker threads for handling chat operations and long-running tasks.
Separates blocking operations from the main UI thread.
"""

import logging
import traceback
from typing import Optional

from PySide6.QtCore import QThread, Signal

from agent_chatbot_logic import AgentChatbotLogic

logger = logging.getLogger(__name__)


class ChatWorker(QThread):
    """Worker thread for handling chat requests without blocking the UI."""
    
    response_ready = Signal(str)
    error_occurred = Signal(str)
    progress_update = Signal(str)
    
    def __init__(self, chat_logic: AgentChatbotLogic, prompt: str, image_path: Optional[str] = None):
        super().__init__()
        self.chat_logic = chat_logic
        self.prompt = prompt
        self.image_path = image_path
        self._is_cancelled = False
    
    def cancel(self):
        """Request cancellation of the current operation."""
        self._is_cancelled = True
        logger.info("Chat worker cancellation requested")
    
    def run(self):
        """Execute the chat request in a separate thread."""
        try:
            if self._is_cancelled:
                return
            
            self.progress_update.emit("Processing request...")
            
            # Check for cancellation before starting
            if self._is_cancelled:
                return
            
            result = self.chat_logic.chat(self.prompt, self.image_path)
            
            # Check for cancellation before emitting result
            if self._is_cancelled:
                return
            
            self.response_ready.emit(result)
            
        except MemoryError as e:
            if not self._is_cancelled:
                self.error_occurred.emit(
                    "❌ SPEICHER-FEHLER: Nicht genügend Arbeitsspeicher verfügbar. "
                    "Versuchen Sie ein kleineres Modell oder reduzieren Sie die Kontextgröße."
                )
        except FileNotFoundError as e:
            if not self._is_cancelled:
                self.error_occurred.emit(f"❌ DATEI-FEHLER: {e}")
        except PermissionError as e:
            if not self._is_cancelled:
                self.error_occurred.emit(f"❌ BERECHTIGUNG-FEHLER: {e}")
        except Exception as e:
            if not self._is_cancelled:
                # Detailed error information with type and traceback
                error_type = type(e).__name__
                tb = traceback.format_exc()
                logger.error(f"ChatWorker error ({error_type}):\n{tb}")
                
                # User-friendly message based on error type
                error_msg = self._format_error_message(e, error_type)
                self.error_occurred.emit(error_msg)
    
    def _format_error_message(self, error: Exception, error_type: str) -> str:
        """Format error messages in a user-friendly way."""
        error_str = str(error).lower()
        
        if "cuda" in error_str or "gpu" in error_str:
            return (f"❌ GPU-FEHLER ({error_type}): {error}\n\n"
                   "💡 Tipp: Versuchen Sie CPU-Modus oder prüfen Sie CUDA-Installation.")
        elif "torch" in error_str:
            return (f"❌ PYTORCH-FEHLER ({error_type}): {error}\n\n"
                   "💡 Tipp: Modell möglicherweise beschädigt oder inkompatibel.")
        elif "llama" in error_str or "model" in error_str:
            return (f"❌ MODELL-FEHLER ({error_type}): {error}\n\n"
                   "💡 Tipp: Modell neu laden oder anderen Modellpfad versuchen.")
        else:
            return (f"❌ UNBEKANNTER FEHLER ({error_type}): {error}\n\n"
                   "🔍 Details im Log verfügbar.")


class ModelLoaderWorker(QThread):
    """Worker thread for loading models without blocking the UI."""
    
    model_loaded = Signal(object)  # Emits the loaded model
    error_occurred = Signal(str)
    progress_update = Signal(str)
    
    def __init__(self, model_loader, model_path: str):
        super().__init__()
        self.model_loader = model_loader
        self.model_path = model_path
        self._is_cancelled = False
    
    def cancel(self):
        """Request cancellation of the model loading."""
        self._is_cancelled = True
        logger.info("Model loader cancellation requested")
    
    def run(self):
        """Load the model in a separate thread."""
        try:
            if self._is_cancelled:
                return
            
            self.progress_update.emit("Initializing model loader...")
            
            if self._is_cancelled:
                return
            
            self.progress_update.emit("Loading model file...")
            model = self.model_loader.load_model(self.model_path)
            
            if self._is_cancelled:
                return
            
            self.progress_update.emit("Model loaded successfully!")
            self.model_loaded.emit(model)
            
        except Exception as e:
            if not self._is_cancelled:
                error_type = type(e).__name__
                logger.error(f"Model loading error ({error_type}): {e}")
                self.error_occurred.emit(f"❌ Fehler beim Laden des Modells: {e}")


class FileProcessorWorker(QThread):
    """Worker thread for processing files (PDFs, images, etc.) without blocking the UI."""
    
    file_processed = Signal(str, object)  # filename, result
    error_occurred = Signal(str, str)  # filename, error message
    progress_update = Signal(str)
    
    def __init__(self, processor_func, files: list, **kwargs):
        super().__init__()
        self.processor_func = processor_func
        self.files = files
        self.kwargs = kwargs
        self._is_cancelled = False
    
    def cancel(self):
        """Request cancellation of file processing."""
        self._is_cancelled = True
        logger.info("File processor cancellation requested")
    
    def run(self):
        """Process files in a separate thread."""
        try:
            total_files = len(self.files)
            
            for i, file_path in enumerate(self.files):
                if self._is_cancelled:
                    return
                
                self.progress_update.emit(f"Processing {i+1}/{total_files}: {file_path}")
                
                try:
                    result = self.processor_func(file_path, **self.kwargs)
                    if not self._is_cancelled:
                        self.file_processed.emit(file_path, result)
                        
                except Exception as e:
                    if not self._is_cancelled:
                        logger.error(f"Error processing {file_path}: {e}")
                        self.error_occurred.emit(file_path, str(e))
            
            if not self._is_cancelled:
                self.progress_update.emit("All files processed successfully!")
                
        except Exception as e:
            if not self._is_cancelled:
                logger.error(f"File processor error: {e}")
                self.error_occurred.emit("", f"File processing failed: {e}")
