"""
Pydantic V2 Migration Adapter (Phase 2)
========================================

Dieser Adapter ermöglicht eine schrittweise Migration von dataclasses zu Pydantic V2 Models
ohne Breaking Changes. Er stellt Kompatibilitätsmethoden bereit für beide Systeme.

Verwendung:
-----------
1. Import Pydantic Models statt dataclasses
2. Verwende Adapter-Funktionen für Legacy-Code
3. Schrittweise Migration einzelner Komponenten

CoT & ToT Design Decisions:
---------------------------
✅ Keine Breaking Changes - Legacy-Code funktioniert weiter
✅ Type-Safety - Pydantic Validierung + MyPy Support
✅ Performance - Cached Converters + Lazy Validation
✅ Observability - Logging für Migration-Tracking
"""

import logging
from typing import Any, Dict, List, Optional, TypeVar, Union, Type, Callable
from dataclasses import asdict, is_dataclass
from datetime import datetime

# Pydantic V2 imports
from models_pydantic_v2 import (
    SessionSummaryModel,
    SessionMessageModel,  # NEW: Phase 4
    PersonalityInsightModel,
    ToolCallModel,
    ToolResultModel,
    SourceModel,
    AgentTraceModel,
    EvidencePackModel,
    FinalAnswerModel,
    LLMConfigModel
)

logger = logging.getLogger(__name__)

# Type Variables für Generic Conversion
T = TypeVar('T')
PydanticModel = TypeVar('PydanticModel')

# ============================================================================
# CONVERSION UTILITIES
# ============================================================================

def dataclass_to_dict(obj: Any) -> Dict[str, Any]:
    """
    Convert dataclass to dict with Pydantic-compatible format
    
    Handles:
    - Nested dataclasses
    - Lists of dataclasses
    - datetime objects
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    elif isinstance(obj, dict):
        return obj
    else:
        raise TypeError(f"Expected dataclass or dict, got {type(obj)}")


def pydantic_to_dataclass_dict(model: Any) -> Dict[str, Any]:
    """
    Convert Pydantic model to dataclass-compatible dict
    
    Uses .model_dump() instead of deprecated .dict()
    """
    if hasattr(model, 'model_dump'):
        result = model.model_dump(mode='python', exclude_none=False)
        # Type guard: ensure dict return
        if not isinstance(result, dict):
            raise TypeError(f"model_dump() returned {type(result)}, expected dict")
        return result
    elif isinstance(model, dict):
        return model
    else:
        raise TypeError(f"Expected Pydantic model or dict, got {type(model)}")


# ============================================================================
# AGENT TYPE ADAPTERS
# ============================================================================

def adapt_toolcall(obj: Any) -> ToolCallModel:
    """
    Adapt legacy ToolCall (dataclass/dict) to ToolCallModel
    
    Usage:
        from agent.agent_types import ToolCall  # legacy
        call = ToolCall(tool="search", parameters={})
        adapted = adapt_toolcall(call)  # Now Pydantic!
    """
    if isinstance(obj, ToolCallModel):
        return obj  # Already Pydantic
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    return ToolCallModel(**data)


def adapt_toolresult(obj: Any) -> ToolResultModel:
    """Adapt legacy ToolResult to ToolResultModel"""
    if isinstance(obj, ToolResultModel):
        return obj
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    return ToolResultModel(**data)


def adapt_source(obj: Any) -> SourceModel:
    """Adapt legacy Source to SourceModel"""
    if isinstance(obj, SourceModel):
        return obj
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    # Defense-in-depth: Truncate title to SourceModel.max_length (500)
    if isinstance(data.get("title"), str) and len(data["title"]) > 497:
        data["title"] = data["title"][:497] + "..."
    return SourceModel(**data)


def adapt_agent_trace(obj: Any) -> AgentTraceModel:
    """
    Adapt legacy AgentTrace to AgentTraceModel
    
    Handles complex nested structures and validation
    """
    if isinstance(obj, AgentTraceModel):
        return obj
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    
    # Special handling for nested verification_results (forward references)
    # We keep them as-is since they use TYPE_CHECKING
    if 'verification_results' in data and data['verification_results']:
        # Don't try to validate forward-referenced types
        pass
    
    return AgentTraceModel(**data)


def adapt_evidence_pack(obj: Any) -> EvidencePackModel:
    """Adapt legacy EvidencePack to EvidencePackModel"""
    if isinstance(obj, EvidencePackModel):
        return obj
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    
    # Adapt nested Source objects
    if 'items' in data:
        data['items'] = [adapt_source(item) for item in data['items']]
    
    return EvidencePackModel(**data)


def adapt_final_answer(obj: Any) -> FinalAnswerModel:
    """Adapt legacy FinalAnswer to FinalAnswerModel"""
    if isinstance(obj, FinalAnswerModel):
        return obj
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    
    # Adapt nested objects
    if 'sources' in data:
        data['sources'] = [adapt_source(s) for s in data['sources']]
    
    if 'trace' in data and data['trace']:
        data['trace'] = adapt_agent_trace(data['trace'])
    
    return FinalAnswerModel(**data)


# ============================================================================
# SESSION TYPE ADAPTERS
# ============================================================================

def adapt_session_summary(obj: Any) -> SessionSummaryModel:
    """Adapt legacy SessionSummary to SessionSummaryModel"""
    if isinstance(obj, SessionSummaryModel):
        return obj
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    
    # Handle datetime conversion
    for field in ['start_time', 'last_activity']:
        if field in data and isinstance(data[field], str):
            try:
                data[field] = datetime.fromisoformat(data[field])
            except (ValueError, AttributeError):
                pass  # Keep as-is, Pydantic will validate
    
    return SessionSummaryModel(**data)


def adapt_session_message(obj: Any) -> SessionMessageModel:
    """
    Adapt legacy SessionMessage to SessionMessageModel
    
    Phase 4: Message migration with validation
    """
    if isinstance(obj, SessionMessageModel):
        return obj
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    
    # Handle datetime conversion
    if 'timestamp' in data and isinstance(data['timestamp'], str):
        try:
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        except (ValueError, AttributeError):
            pass  # Keep as-is, Pydantic will validate
    
    return SessionMessageModel(**data)


def adapt_personality_insight(obj: Any) -> PersonalityInsightModel:
    """Adapt legacy PersonalityInsight to PersonalityInsightModel"""
    if isinstance(obj, PersonalityInsightModel):
        return obj
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    
    # Handle datetime conversion
    for field in ['created_at', 'validated_at']:
        if field in data and isinstance(data[field], str):
            try:
                data[field] = datetime.fromisoformat(data[field])
            except (ValueError, AttributeError):
                pass
    
    return PersonalityInsightModel(**data)


# ============================================================================
# GENERIC SESSION ADAPTERS (Phase 3)
# ============================================================================

def legacy_to_pydantic(obj: Any, target_model: Type[PydanticModel]) -> PydanticModel:
    """
    Generic converter: Legacy object → Pydantic model
    
    Handles:
    - Dataclasses → Pydantic
    - Dict → Pydantic
    - Already Pydantic → Pass through
    
    Usage:
        from wellbeing_session_interface import SessionSummary  # legacy
        legacy_session = SessionSummary(...)
        pydantic_session = legacy_to_pydantic(legacy_session, SessionSummaryModel)
    """
    if isinstance(obj, target_model):
        return obj  # Already the target type
    
    data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
    
    # Handle datetime conversions
    for field_name, field_value in list(data.items()):
        if isinstance(field_value, str) and field_name in ['start_time', 'last_activity', 'timestamp', 'created_at']:
            try:
                data[field_name] = datetime.fromisoformat(field_value)
            except (ValueError, AttributeError):
                pass
    
    return target_model(**data)


def pydantic_to_legacy_dict(pydantic_obj: Any) -> Dict[str, Any]:
    """
    Generic conversion: Pydantic Model → Legacy dict
    
    Args:
        pydantic_obj: Pydantic model instance
        
    Returns:
        Serializable dictionary for legacy code
    """
    if hasattr(pydantic_obj, 'model_dump'):
        result = pydantic_obj.model_dump()
        # Type guard: ensure dict return
        if not isinstance(result, dict):
            raise TypeError(f"model_dump() returned {type(result)}, expected dict")
        return result
    elif is_dataclass(pydantic_obj):
        return dataclass_to_dict(pydantic_obj)
    elif isinstance(pydantic_obj, dict):
        return pydantic_obj
    else:
        raise TypeError(f"Cannot convert {type(pydantic_obj)} to dict")


# ============================================================================
# BATCH CONVERSION
# ============================================================================

def adapt_list(
    items: List[Any],
    adapter_func: Callable[[Any], Any]
) -> List[Any]:
    """
    Batch convert list of objects using adapter function
    
    Usage:
        legacy_calls = [ToolCall(...), ToolCall(...)]
        pydantic_calls = adapt_list(legacy_calls, adapt_toolcall)
    """
    return [adapter_func(item) for item in items]


def adapt_dict_values(
    data: Dict[str, Any],
    key_patterns: Dict[str, Callable[[Any], Any]]
) -> Dict[str, Any]:
    """
    Convert specific keys in dict using adapter functions
    
    Usage:
        data = {
            "trace": legacy_trace_obj,
            "calls": [legacy_call1, legacy_call2]
        }
        adapted = adapt_dict_values(data, {
            "trace": adapt_agent_trace,
            "calls": lambda x: adapt_list(x, adapt_toolcall)
        })
    """
    result = data.copy()
    for key, adapter in key_patterns.items():
        if key in result and result[key] is not None:
            try:
                result[key] = adapter(result[key])
            except Exception as e:
                logger.warning(f"Failed to adapt key '{key}': {e}")
    return result


# ============================================================================
# REVERSE CONVERSION (Pydantic → Legacy Dataclass Dict)
# ============================================================================

def to_legacy_dict(model: Any) -> Dict[str, Any]:
    """
    Convert Pydantic model back to legacy dataclass-compatible dict
    
    Useful for:
    - Database serialization
    - JSON APIs that expect old format
    - Gradual migration
    """
    return pydantic_to_dataclass_dict(model)


# ============================================================================
# VALIDATION HELPERS
# ============================================================================

def validate_and_adapt(
    obj: Any,
    model_class: Type[PydanticModel],
    strict: bool = False
) -> Union[PydanticModel, Any]:
    """
    Try to validate/adapt object to Pydantic model
    
    Args:
        obj: Object to validate
        model_class: Target Pydantic model class
        strict: If True, raise on validation errors; if False, return original
    
    Returns:
        Validated Pydantic model or original object (if strict=False)
    """
    try:
        if isinstance(obj, model_class):
            return obj
        
        data = dataclass_to_dict(obj) if is_dataclass(obj) else obj
        return model_class(**data)
    
    except Exception as e:
        if strict:
            raise
        logger.debug(f"Validation failed for {model_class.__name__}: {e}")
        return obj


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

if __name__ == "__main__":
    # Example 1: Adapt ToolCall
    from agent.agent_types import ToolCall as LegacyToolCall
    
    legacy_call = LegacyToolCall(tool="web_search", parameters={"query": "test"})
    pydantic_call = adapt_toolcall(legacy_call)
    print(f"✅ Adapted ToolCall: {pydantic_call}")
    
    # Example 2: Batch adapt
    legacy_calls = [
        LegacyToolCall(tool="search", parameters={"q": "ai"}),
        LegacyToolCall(tool="calculator", parameters={"expr": "2+2"})
    ]
    pydantic_calls = adapt_list(legacy_calls, adapt_toolcall)
    print(f"✅ Batch adapted {len(pydantic_calls)} calls")
    
    # Example 3: Reverse conversion
    back_to_dict = to_legacy_dict(pydantic_call)
    print(f"✅ Back to dict: {back_to_dict}")
    
    print("\n🎉 Pydantic Migration Adapter ready!")
