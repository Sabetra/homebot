#!/usr/bin/env python3
"""
GPU OPTIMIZATION MODULE
Optimizes GPU utilization for embedding creation and RAG operations

Features:
- Dynamic GPU layer calculation based on available VRAM
- Optimized batch sizes for different operations
- GPU memory management
- Performance monitoring for GPU operations
"""

import torch
import psutil
import os
import logging
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

# Set up logging
logger = logging.getLogger(__name__)

@dataclass
class GPUOptimizationConfig:
    """GPU optimization configuration"""
    # Model loading
    use_all_gpu_layers: bool = True
    gpu_layers_override: Optional[int] = None
    
    # Embedding optimization
    embedding_batch_size: int = 128  # Larger batches for better GPU utilization
    embedding_max_workers: int = 4   # Parallel encoding workers
    
    # Memory management
    gpu_memory_fraction: float = 0.9  # Use 90% of GPU memory
    allow_growth: bool = True
    
    # Performance settings
    use_fp16: bool = True  # Half precision for better throughput
    use_compile: bool = True  # PyTorch 2.0 compilation
    
class GPUOptimizer:
    """GPU optimization and management"""
    
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.config = GPUOptimizationConfig()
        self.gpu_info = self._detect_gpu_capabilities()
        
        if self.debug:
            self._print_gpu_info()
    
    def _detect_gpu_capabilities(self) -> Dict[str, Any]:
        """Detect GPU capabilities and specifications"""
        gpu_info = {
            "available": False,
            "count": 0,
            "total_memory_gb": 0,
            "name": "Unknown",
            "compute_capability": None,
            "cuda_version": None
        }
        
        try:
            if torch.cuda.is_available():
                gpu_info["available"] = True
                gpu_info["count"] = torch.cuda.device_count()
                
                # Primary GPU info
                device_props = torch.cuda.get_device_properties(0)
                gpu_info["name"] = device_props.name
                gpu_info["total_memory_gb"] = device_props.total_memory / (1024**3)
                gpu_info["compute_capability"] = f"{device_props.major}.{device_props.minor}"
                
                # CUDA version (vereinfacht)
                gpu_info["cuda_version"] = "available"
                
                if self.debug:
                    logger.info(f"GPU detected: {gpu_info['name']} with {gpu_info['total_memory_gb']:.1f} GB")
                
        except Exception as e:
            logger.warning(f"GPU detection failed: {e}")
        
        return gpu_info
    
    def _print_gpu_info(self):
        """Print detailed GPU information"""
        print("🔥 GPU OPTIMIZATION REPORT")
        print("=" * 50)
        
        if self.gpu_info["available"]:
            print(f"✅ GPU Available: {self.gpu_info['name']}")
            print(f"📊 VRAM: {self.gpu_info['total_memory_gb']:.1f} GB")
            print(f"🔢 Compute Capability: {self.gpu_info['compute_capability']}")
            print(f"🐍 CUDA Version: {self.gpu_info['cuda_version']}")
            print(f"🔥 Device Count: {self.gpu_info['count']}")
            
            # Memory status
            if torch.cuda.is_available():
                memory_allocated = torch.cuda.memory_allocated(0) / (1024**3)
                memory_reserved = torch.cuda.memory_reserved(0) / (1024**3)
                print(f"💾 Memory Used: {memory_allocated:.2f} GB")
                print(f"🔒 Memory Reserved: {memory_reserved:.2f} GB")
        else:
            print("❌ No GPU available")
        
        print("=" * 50)
    
    def get_optimal_gpu_layers(self, model_size_estimate: Optional[float] = None) -> int:
        """
        Calculate optimal GPU layers based on available VRAM
        
        Args:
            model_size_estimate: Estimated model size in GB (optional)
        
        Returns:
            Optimal number of GPU layers
        """
        if not self.gpu_info["available"]:
            return 0
        
        # If override is set, use it
        if self.config.gpu_layers_override is not None:
            return self.config.gpu_layers_override
        
        # If configured to use all layers, return -1 (all layers)
        if self.config.use_all_gpu_layers:
            return -1  # -1 means use all available layers
        
        # Calculate based on available VRAM
        total_vram = self.gpu_info["total_memory_gb"]
        usable_vram = total_vram * self.config.gpu_memory_fraction
        
        # Conservative estimates for different VRAM amounts
        if usable_vram >= 20:
            return -1  # Use all layers for high-end GPUs
        elif usable_vram >= 16:
            return 80
        elif usable_vram >= 12:
            return 60
        elif usable_vram >= 8:
            return 40
        elif usable_vram >= 6:
            return 30
        else:
            return 20
    
    def get_optimal_embedding_config(self) -> Dict[str, Any]:
        """Get optimal embedding configuration for current GPU"""
        config = {
            "batch_size": 32,  # Conservative default
            "device": "cpu",
            "use_fp16": False,
            "max_workers": 1
        }
        
        if not self.gpu_info["available"]:
            return config
        
        # GPU-specific optimizations
        vram_gb = self.gpu_info["total_memory_gb"]
        
        # Batch size optimization based on VRAM
        if vram_gb >= 20:
            config["batch_size"] = 1024  # Optimal für RTX 4090 basierend auf Tests
        elif vram_gb >= 16:
            config["batch_size"] = 768
        elif vram_gb >= 12:
            config["batch_size"] = 512
        elif vram_gb >= 8:
            config["batch_size"] = 256
        elif vram_gb >= 6:
            config["batch_size"] = 128
        else:
            config["batch_size"] = 64
        
        # Device and precision settings
        config["device"] = "cuda"
        config["use_fp16"] = self.config.use_fp16
        config["max_workers"] = self.config.embedding_max_workers
        
        # Additional optimizations for newer GPUs
        if self.gpu_info["compute_capability"] and float(self.gpu_info["compute_capability"]) >= 7.5:
            config["use_fp16"] = True  # Enable FP16 for newer GPUs
        
        return config
    
    def optimize_model_loading_params(self, model_path: str) -> Dict[str, Any]:
        """Get optimized parameters for model loading"""
        params = {
            "n_gpu_layers": self.get_optimal_gpu_layers(),
            "use_mmap": True,
            "use_mlock": False,
            "n_threads": None,  # Auto-detect für GPU-Workloads (intelligenter als fix 16)
            "n_batch": 2048,  # Conservative stable default for llama.cpp prefill
            "rope_scaling_type": None,
            "rope_freq_base": 0.0,
            "yarn_ext_factor": -1.0,
            "yarn_attn_factor": 1.0,
            "yarn_beta_fast": 32.0,
            "yarn_beta_slow": 1.0,
            "f16_kv": True,      # FP16 für KV-Cache (RTX4090 Optimierung)
            "low_vram": False,   # RTX4090 hat 24GB - nutze es!
            "last_n_tokens_size": 512,  # Größerer Token-Buffer
        }
        
        # GPU-specific optimizations für RTX4090
        if self.gpu_info["available"]:
            vram_gb = self.gpu_info["total_memory_gb"]
            
            # RTX4090-spezifische Batch-Größen (stabilitätsgehärtet)
            # Avoid very large prefill batches that can trigger CUDA kernel
            # launch failures in some llama.cpp/ggml-cuda builds.
            if vram_gb >= 20:  # RTX4090 mit 24GB
                params["n_batch"] = 3072
                params["last_n_tokens_size"] = 1024
            elif vram_gb >= 16:
                params["n_batch"] = 2048  # Große Batches für High-End GPUs
                params["last_n_tokens_size"] = 512
            elif vram_gb >= 12:
                params["n_batch"] = 1024
                params["last_n_tokens_size"] = 256
            elif vram_gb >= 8:
                params["n_batch"] = 512
                params["last_n_tokens_size"] = 128
            
            # Memory-Optimierungen für High-End Hardware
            if vram_gb >= 16:
                params["use_mlock"] = True  # Lock memory für RTX4090 Performance
        
        return params
    
    def optimize_sentence_transformer(self, model_name: str = "BAAI/bge-large-en-v1.5") -> Dict[str, Any]:
        """Get optimized parameters for SentenceTransformer"""
        config = {
            "device": "cpu",
            "model_kwargs": {},
            "encode_kwargs": {}
        }
        
        if not self.gpu_info["available"]:
            return config
        
        # GPU optimization - SentenceTransformer takes device as direct parameter
        config["device"] = "cuda"
        
        # Model loading kwargs - empty for SentenceTransformer
        config["model_kwargs"] = {}
        
        # Encoding kwargs
        embedding_config = self.get_optimal_embedding_config()
        config["encode_kwargs"] = {
            "batch_size": embedding_config["batch_size"],
            "convert_to_tensor": True,
            "normalize_embeddings": True,
            "show_progress_bar": False
        }
        
        return config
    
    def monitor_gpu_usage(self) -> Dict[str, float]:
        """Monitor current GPU usage"""
        usage = {
            "memory_used_percent": 0.0,
            "memory_used_gb": 0.0,
            "memory_total_gb": 0.0,
            "temperature": 0.0,
            "utilization_percent": 0.0
        }
        
        if not self.gpu_info["available"]:
            return usage
        
        try:
            # PyTorch memory info
            if torch.cuda.is_available():
                memory_allocated = torch.cuda.memory_allocated(0)
                memory_reserved = torch.cuda.memory_reserved(0)
                memory_total = torch.cuda.get_device_properties(0).total_memory
                
                usage["memory_used_gb"] = memory_allocated / (1024**3)
                usage["memory_total_gb"] = memory_total / (1024**3)
                usage["memory_used_percent"] = (memory_allocated / memory_total) * 100
            
            # Try to get additional GPU info (temperature, utilization)
            try:
                import GPUtil
                gpus = GPUtil.getGPUs()
                if gpus:
                    gpu = gpus[0]
                    usage["temperature"] = gpu.temperature
                    usage["utilization_percent"] = gpu.load * 100
            except ImportError:
                pass  # GPUtil not available
                
        except Exception as e:
            logger.warning(f"GPU monitoring failed: {e}")
        
        return usage
    
    def clear_gpu_cache(self):
        """Clear GPU memory cache"""
        if self.gpu_info["available"] and torch.cuda.is_available():
            torch.cuda.empty_cache()
            if self.debug:
                print("🧹 GPU cache cleared")
    
    def get_temperature_warning(self) -> Optional[str]:
        """Check GPU temperature and return warning if too hot"""
        usage = self.monitor_gpu_usage()
        temp = usage.get("temperature", 0)
        
        if temp == 0:  # Temperature not available
            return None
        
        if temp > 85:
            return f"🔥 GPU sehr heiß: {temp}°C - Reduze die Last!"
        elif temp > 75:
            return f"🌡️ GPU warm: {temp}°C - Überwache die Temperatur"
        elif temp < 50:
            return f"❄️ GPU kühl: {temp}°C - Kann mehr Last vertragen!"
        
        return None

# Global optimizer instance
_gpu_optimizer: Optional[GPUOptimizer] = None

def get_gpu_optimizer(debug: bool = False) -> GPUOptimizer:
    """Get global GPU optimizer instance"""
    global _gpu_optimizer
    if _gpu_optimizer is None:
        _gpu_optimizer = GPUOptimizer(debug=debug)
    return _gpu_optimizer

def clear_gpu_cache() -> str:
    """Clear the CUDA GPU cache and return a status string.

    Single Source of Truth for GPU cache clearing. Replaces the duplicate
    implementation that previously lived in ``utils/gpu_helpers.py``.

    Returns one of:
    - ``"cleared"``      when the cache was successfully emptied.
    - ``"unavailable"``  when CUDA is not available on this system.
    - ``"error"``        when an unexpected exception occurred.
    """
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            return "cleared"
        return "unavailable"
    except (ImportError, RuntimeError) as exc:
        logger.debug("GPU cache clear failed: %s", exc, exc_info=True)
        return "error"


def apply_gpu_optimizations():
    """Apply GPU optimizations to the current process"""
    optimizer = get_gpu_optimizer()
    
    if optimizer.gpu_info["available"]:
        # Set memory growth if using TensorFlow
        try:
            import tensorflow as tf
            gpus = tf.config.experimental.list_physical_devices('GPU')
            if gpus:
                for gpu in gpus:
                    tf.config.experimental.set_memory_growth(gpu, True)
        except ImportError:
            pass
        
        # PyTorch optimizations
        if torch.cuda.is_available():
            # Enable TensorFloat-32 (TF32) for newer GPUs
            if torch.backends.cuda.matmul.allow_tf32:
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
            
            # Enable cuDNN benchmarking for consistent input sizes
            torch.backends.cudnn.benchmark = True

if __name__ == "__main__":
    # Test the GPU optimizer
    print("🔥 GPU Optimizer Test")
    print("=" * 30)
    
    optimizer = GPUOptimizer(debug=True)
    
    print(f"\n🎯 Recommended GPU layers: {optimizer.get_optimal_gpu_layers()}")
    
    embedding_config = optimizer.get_optimal_embedding_config()
    print(f"\n🧠 Optimal embedding config:")
    for key, value in embedding_config.items():
        print(f"   {key}: {value}")
    
    model_params = optimizer.optimize_model_loading_params("dummy_model.gguf")
    print(f"\n📦 Optimal model loading params:")
    for key, value in model_params.items():
        print(f"   {key}: {value}")
    
    usage = optimizer.monitor_gpu_usage()
    print(f"\n📊 Current GPU usage:")
    for key, value in usage.items():
        print(f"   {key}: {value}")
    
    temp_warning = optimizer.get_temperature_warning()
    if temp_warning:
        print(f"\n{temp_warning}")
    else:
        print("\n✅ GPU temperature OK")
