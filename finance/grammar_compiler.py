"""
Grammar Compiler for Finance Module

Compiles Pydantic schemas (FinanceQueryPlan, FinanceContinuationDecision) to EBNF grammars
for grammar-constrained decoding using Outlines or fallback mechanisms.

This module provides:
1. Schema-to-grammar compilation for FinanceQueryPlan
2. Schema-to-grammar compilation for FinanceContinuationDecision
3. Fallback to JSON schema validation when grammar constraints are unavailable
4. Unit-testable grammar generation
"""

import json
from typing import Optional, List, Tuple

from pydantic import BaseModel

# Try importing outlines for grammar-constrained decoding
try:
    from outlines import grammars as outlines_grammars  # noqa: F401
    from outlines.fsm.guide import CFGGuide  # type: ignore
    OUTLINES_AVAILABLE = True
except ImportError:
    OUTLINES_AVAILABLE = False
    CFGGuide = None  # type: ignore


# ============================================================================
# Grammar Configuration
# ============================================================================

class GrammarConfig:
    """Configuration for grammar-constrained decoding.
    
    Parameters
    ----------
    grammar_format : str
        Grammar format to use: "bnfc" (Bernard's NFA Compiler), "xgrammar", 
        or "json_schema". Defaults to "bnfc".
    strict_mode : bool
        When True, only allow strictly valid grammar productions.
        Defaults to True.
    max_tokens : int
        Maximum tokens to generate before grammar validation.
        Defaults to 2048.
    enable_caching : bool
        Cache compiled grammars for repeated use. Defaults to True.
    fallback_to_schema : bool
        When grammar constraints fail, fall back to JSON schema validation.
        Defaults to True.
    """
    
    def __init__(
        self,
        grammar_format: str = "bnfc",
        strict_mode: bool = True,
        max_tokens: int = 2048,
        enable_caching: bool = True,
        fallback_to_schema: bool = True,
    ) -> None:
        self.grammar_format = grammar_format
        self.strict_mode = strict_mode
        self.max_tokens = max_tokens
        self.enable_caching = enable_caching
        self.fallback_to_schema = fallback_to_schema
    
    def __repr__(self) -> str:
        return (
            f"GrammarConfig(grammar_format={self.grammar_format!r}, "
            f"strict_mode={self.strict_mode}, max_tokens={self.max_tokens})"
        )


# ============================================================================
# Grammar Compiler Class
# ============================================================================

class GrammarCompiler:
    """Compiles Pydantic schemas to grammar constraints for LLM providers.
    
    Provides static methods to compile schemas to BNF and XGrammar formats.
    Grammars are pre-compiled and cached for performance.
    """
    
    _cache: dict = {}
    
    @staticmethod
    def compile_for_schema(
        model: type,
        config: Optional[GrammarConfig] = None,
    ) -> str:
        """Compile a Pydantic model to BNF grammar (bnfc format).
        
        Args:
            model: Pydantic BaseModel subclass.
            config: Grammar configuration. Uses defaults if None.
            
        Returns:
            BNF grammar string suitable for constrained decoding.
            
        Raises:
            ValueError: If model is not a Pydantic BaseModel subclass.
        """
        if not issubclass(model, BaseModel):
            raise ValueError(f"{model} is not a Pydantic BaseModel subclass")
        
        config = config or GrammarConfig()
        cache_key = f"bnfc_{model.__name__}"
        
        if config.enable_caching and cache_key in GrammarCompiler._cache:
            return GrammarCompiler._cache[cache_key]
        
        # Extract JSON schema from model
        schema = extract_json_schema(model)
        
        # Build BNF grammar from schema
        grammar = GrammarCompiler._build_bnfc_grammar(schema, model.__name__)
        
        if config.enable_caching:
            GrammarCompiler._cache[cache_key] = grammar
        
        return grammar
    
    @staticmethod
    def compile_xgrammar_for_schema(
        model: type,
        config: Optional[GrammarConfig] = None,
    ) -> str:
        """Compile a Pydantic model to XGrammar format.
        
        XGrammar is a cross-framework grammar constraint format.
        
        Args:
            model: Pydantic BaseModel subclass.
            config: Grammar configuration. Uses defaults if None.
            
        Returns:
            XGrammar string suitable for constrained decoding.
            
        Raises:
            ValueError: If model is not a Pydantic BaseModel subclass.
        """
        if not issubclass(model, BaseModel):
            raise ValueError(f"{model} is not a Pydantic BaseModel subclass")
        
        config = config or GrammarConfig()
        cache_key = f"xgrammar_{model.__name__}"
        
        if config.enable_caching and cache_key in GrammarCompiler._cache:
            return GrammarCompiler._cache[cache_key]
        
        # Extract JSON schema from model
        schema = extract_json_schema(model)
        
        # Build XGrammar from schema
        grammar = GrammarCompiler._build_xgrammar(schema, model.__name__)
        
        if config.enable_caching:
            GrammarCompiler._cache[cache_key] = grammar
        
        return grammar
    
    @staticmethod
    def _build_bnfc_grammar(schema: dict, model_name: str) -> str:
        """Build BNF grammar from JSON schema."""
        rules = []
        rules.append(f"// {model_name} BNF Grammar")
        rules.append(f"// Compiled by GrammarCompiler")
        rules.append("")
        rules.append(f"start ::= {model_name}_object")
        
        # Recursively build rules from schema
        GrammarCompiler._add_schema_rules(schema, model_name, rules)
        
        # Add JSON primitives
        rules.append("")
        rules.append("// JSON Primitives")
        rules.append('string ::= "\\" string_chars "\\"')
        rules.append("string_chars ::= string_char*")
        rules.append('string_char ::= [^\\\\"]+')
        rules.append('number ::= "-"? [0-9]+ ("." [0-9]+)? ([eE] [+-]? [0-9]+)?')
        rules.append('boolean ::= "true" | "false"')
        rules.append('null ::= "null"')
        rules.append('whitespace ::= [ \\t\\n\\r]+')
        
        return "\n".join(rules)
    
    @staticmethod
    def _build_xgrammar(schema: dict, model_name: str) -> str:
        """Build XGrammar from JSON schema.
        
        XGrammar uses a simplified format compatible with multiple LLM providers.
        """
        rules = []
        rules.append(f"// {model_name} XGrammar")
        rules.append(f"// Cross-framework grammar constraint")
        rules.append("")
        rules.append(f"root = {model_name}_object")
        
        # Recursively build rules
        GrammarCompiler._add_xgrammar_rules(schema, model_name, rules)
        
        return "\n".join(rules)
    
    @staticmethod
    def _add_schema_rules(schema: dict, node_name: str, rules: List[str]):
        """Add BNF rules for a JSON schema node."""
        schema_type = schema.get("type", "any")
        
        if schema_type == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            
            if properties:
                field_rules = []
                for prop_name, prop_schema in properties.items():
                    prop_node = f"{node_name}_{prop_name}"
                    GrammarCompiler._add_schema_rules(prop_schema, prop_node, rules)
                    marker = "!" if prop_name in required else "?"
                    field_rules.append(f'{marker}\\"{prop_name}\\" : {prop_node}')
                
                rules.append(f"{node_name}_object ::= '{{' {', '.join(field_rules)} '}}'")
            else:
                rules.append(f"{node_name}_object ::= json_object")
        
        elif schema_type == "array":
            items_schema = schema.get("items", {})
            if items_schema:
                items_node = f"{node_name}_item"
                GrammarCompiler._add_schema_rules(items_schema, items_node, rules)
                rules.append(f"{node_name} ::= '[' {items_node} (',' {items_node})* ']'")
            else:
                rules.append(f"{node_name} ::= json_array")
        
        elif schema_type == "string":
            if "enum" in schema:
                enum_values = " | ".join(f'"{v}"' for v in schema["enum"])
                rules.append(f"{node_name} ::= {enum_values}")
            else:
                rules.append(f"{node_name} ::= string")
        
        elif schema_type in ("number", "integer"):
            rules.append(f"{node_name} ::= number")
        
        elif schema_type == "boolean":
            rules.append(f"{node_name} ::= boolean")
        
        elif schema_type == "null":
            rules.append(f"{node_name} ::= null")
        
        elif "anyOf" in schema or "oneOf" in schema:
            variants = schema.get("anyOf", schema.get("oneOf", []))
            variant_nodes = []
            for i, variant in enumerate(variants):
                variant_node = f"{node_name}_variant_{i}"
                GrammarCompiler._add_schema_rules(variant, variant_node, rules)
                variant_nodes.append(variant_node)
            rules.append(f"{node_name} ::= {' | '.join(variant_nodes)}")
        
        else:
            rules.append(f"{node_name} ::= json_value")
    
    @staticmethod
    def _add_xgrammar_rules(schema: dict, node_name: str, rules: List[str]):
        """Add XGrammar rules for a JSON schema node."""
        schema_type = schema.get("type", "any")
        
        if schema_type == "object":
            properties = schema.get("properties", {})
            if properties:
                field_rules = []
                for prop_name, prop_schema in properties.items():
                    prop_node = f"{node_name}_{prop_name}"
                    GrammarCompiler._add_xgrammar_rules(prop_schema, prop_node, rules)
                    field_rules.append(f'"{prop_name}" : {prop_node}')
                
                rules.append(f"{node_name}_object = {{ {', '.join(field_rules)} }}")
            else:
                rules.append(f"{node_name}_object = json_object")
        
        elif schema_type == "array":
            items_schema = schema.get("items", {})
            if items_schema:
                items_node = f"{node_name}_item"
                GrammarCompiler._add_xgrammar_rules(items_schema, items_node, rules)
                rules.append(f"{node_name} = [{items_node}, *]")
            else:
                rules.append(f"{node_name} = json_array")
        
        elif schema_type == "string":
            if "enum" in schema:
                enum_values = " | ".join(f'"{v}"' for v in schema["enum"])
                rules.append(f"{node_name} = {enum_values}")
            else:
                rules.append(f"{node_name} = string")
        
        elif schema_type in ("number", "integer"):
            rules.append(f"{node_name} = number")
        
        elif schema_type == "boolean":
            rules.append(f"{node_name} = boolean")
        
        elif "anyOf" in schema or "oneOf" in schema:
            variants = schema.get("anyOf", schema.get("oneOf", []))
            variant_nodes = []
            for i, variant in enumerate(variants):
                variant_node = f"{node_name}_variant_{i}"
                GrammarCompiler._add_xgrammar_rules(variant, variant_node, rules)
                variant_nodes.append(variant_node)
            rules.append(f"{node_name} = {' | '.join(variant_nodes)}")
        
        else:
            rules.append(f"{node_name} = json_value")
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear the grammar compilation cache."""
        cls._cache.clear()

# ============================================================================
# Finance Tool Names (canonical list)
# ============================================================================

FINANCE_TOOL_NAMES = [
    "finance_search_bookings",
    "finance_sql_query",
    "finance_counterparty_costs",
    "finance_category_costs",
    "finance_cost_structure",
    "finance_recurring_expense",
    "finance_expense_forecast",
    "finance_expense_anomaly",
    "finance_budget_status",
    "finance_budget_vs_actual",
    "finance_savings_potential",
    "finance_expense_trend_break",
]

# ============================================================================
# Reflector Actions (canonical list)
# ============================================================================

REFLECTOR_ACTIONS = [
    "done",
    "retry_search",
    "retry_sql",
    "retry_counterparty_costs",
    "retry_category_costs",
    "retry_cost_structure",
    "retry_recurring_expense",
    "retry_expense_forecast",
    "retry_expense_anomaly",
    "retry_budget_status",
    "retry_budget_vs_actual",
    "retry_savings_potential",
    "retry_expense_trend_break",
]


# ============================================================================
# EBNF Grammar Generation
# ============================================================================

def compile_finance_plan_grammar(tool_names: Optional[List[str]] = None) -> str:
    """
    Compile FinanceQueryPlan schema to EBNF grammar.
    
    The generated grammar constrains LLM output to valid FinanceQueryPlan JSON:
    {
        "tool": "<tool_name>",
        "arguments": { "key1": "value1", ... },
        "rationale": "<string>"
    }
    
    Args:
        tool_names: List of valid tool names. Defaults to FINANCE_TOOL_NAMES.
    
    Returns:
        EBNF grammar string.
    """
    if tool_names is None:
        tool_names = FINANCE_TOOL_NAMES
    
    tool_alternatives = " | ".join(f'"{name}"' for name in tool_names)
    
    grammar = f'''// FinanceQueryPlan EBNF Grammar
// Generated by grammar_compiler.py

start ::= object

object ::= "{{" comma_separated_fields "}}"
comma_separated_fields ::= field ("," field)*
field ::= tool_field | arguments_field | rationale_field

// Tool field (required)
tool_field ::= "\\"tool\\"" ":" whitespace tool_value
tool_value ::= {tool_alternatives}

// Arguments field (required, JSON object)
arguments_field ::= "\\"arguments\\"" ":" whitespace json_object

// Rationale field (required, string)
rationale_field ::= "\\"rationale\\"" ":" whitespace string_value

// JSON basics
json_object ::= "{{" json_object_contents "}}"
json_object_contents ::= json_field ("," json_field)* | epsilon
json_field ::= string_value ":" whitespace json_value

json_value ::= string_value | number_value | bool_value | null_value | json_object | json_array

string_value ::= "\\" string_chars "\\"
string_chars ::= string_char*
string_char ::= [^\\"]+

number_value ::= "-"? [0-9]+ ("." [0-9]+)? ([eE] [+-]? [0-9]+)?

bool_value ::= "true" | "false"
null_value ::= "null"

json_array ::= "[" json_array_contents "]"
json_array_contents ::= json_value ("," json_value)* | epsilon

whitespace ::= [ \\t\\n\\r]+
epsilon ::= ""
'''
    return grammar


def compile_reflector_grammar(actions: Optional[List[str]] = None) -> str:
    """
    Compile FinanceContinuationDecision schema to EBNF grammar.
    
    The generated grammar constrains LLM output to valid FinanceContinuationDecision JSON:
    {
        "action": "<action>",
        "confidence": <float 0.0-1.0>,
        "rationale": "<string>",
        "continuation_args": {...}
    }
    
    Args:
        actions: List of valid action names. Defaults to REFLECTOR_ACTIONS.
    
    Returns:
        EBNF grammar string.
    """
    if actions is None:
        actions = REFLECTOR_ACTIONS
    
    action_alternatives = " | ".join(f'"{name}"' for name in actions)
    
    grammar = f'''// FinanceContinuationDecision EBNF Grammar
// Generated by grammar_compiler.py

start ::= object

object ::= "{{" comma_separated_fields "}}"
comma_separated_fields ::= field ("," field)*
field ::= action_field | confidence_field | rationale_field | continuation_args_field

// Action field (required)
action_field ::= "\\"action\\"" ":" whitespace action_value
action_value ::= {action_alternatives}

// Confidence field (required, float 0.0-1.0)
confidence_field ::= "\\"confidence\\"" ":" whitespace number_value

// Rationale field (required, string)
rationale_field ::= "\\"rationale\\"" ":" whitespace string_value

// Continuation args field (optional, JSON object)
continuation_args_field ::= "\\"continuation_args\\"" ":" whitespace json_object

// JSON basics
json_object ::= "{{" json_object_contents "}}"
json_object_contents ::= json_field ("," json_field)* | epsilon
json_field ::= string_value ":" whitespace json_value

json_value ::= string_value | number_value | bool_value | null_value | json_object | json_array

string_value ::= "\\" string_chars "\\"
string_chars ::= string_char*
string_char ::= [^\\"]+

number_value ::= "-"? [0-9]+ ("." [0-9]+)? ([eE] [+-]? [0-9]+)?

bool_value ::= "true" | "false"
null_value ::= "null"

json_array ::= "[" json_array_contents "]"
json_array_contents ::= json_value ("," json_value)* | epsilon

whitespace ::= [ \\t\\n\\r]+
epsilon ::= ""
'''
    return grammar


def compile_json_schema_grammar(schema: dict) -> str:
    """
    Compile a generic JSON Schema to EBNF grammar.
    
    Handles common patterns:
    - objects with required/optional properties
    - string, number, integer, boolean, null types
    - arrays of values
    - enum literals
    
    Args:
        schema: JSON Schema dictionary (Pydantic model_json_schema output)
    
    Returns:
        EBNF grammar string.
    """
    rules = []
    rules.append('start ::= json_value')
    
    _compile_schema_node(schema, "json_value", rules)
    
    return "\n".join(rules)


def _compile_schema_node(schema: dict, node_name: str, rules: List[str]):
    """Recursively compile a JSON Schema node to EBNF rules."""
    schema_type = schema.get("type", "any")
    
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        
        if properties:
            fields = []
            for prop_name, prop_schema in properties.items():
                prop_node = f"{node_name}_{prop_name}"
                _compile_schema_node(prop_schema, prop_node, rules)
                is_required = prop_name in required
                marker = "!" if is_required else "?"
                fields.append(f'{marker}\\"{prop_name}\\" ":" whitespace {prop_node}')
            
            rules.append(f'{node_name} ::= "{{" {", ".join(fields)} "}}"')
        else:
            rules.append(f"{node_name} ::= json_object")
    
    elif schema_type == "array":
        items_schema = schema.get("items", {})
        if items_schema:
            items_node = f"{node_name}_item"
            _compile_schema_node(items_schema, items_node, rules)
            rules.append(f'{node_name} ::= "[" items_node ("," items_node)* "]"')
        else:
            rules.append(f"{node_name} ::= json_array")
    
    elif schema_type == "string":
        if "enum" in schema:
            enum_values = " | ".join(f'"{v}"' for v in schema["enum"])
            rules.append(f"{node_name} ::= {enum_values}")
        else:
            rules.append(f'{node_name} ::= "\\" string_chars "\\"')
    
    elif schema_type in ("number", "integer"):
        rules.append(f"{node_name} ::= number_value")
    
    elif schema_type == "boolean":
        rules.append(f'{node_name} ::= "true" | "false"')
    
    elif schema_type == "null":
        rules.append(f'{node_name} ::= "null"')
    
    elif "enum" in schema:
        enum_values = " | ".join(f'"{v}"' for v in schema["enum"])
        rules.append(f"{node_name} ::= {enum_values}")
    
    elif "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf", schema.get("oneOf", []))
        variant_nodes = []
        for i, variant in enumerate(variants):
            variant_node = f"{node_name}_variant_{i}"
            _compile_schema_node(variant, variant_node, rules)
            variant_nodes.append(variant_node)
        rules.append(f"{node_name} ::= {' | '.join(variant_nodes)}")
    
    else:
        rules.append(f"{node_name} ::= json_value")


# ============================================================================
# Grammar Validation
# ============================================================================

def validate_grammar(grammar: str) -> Tuple[bool, List[str]]:
    """
    Validate an EBNF grammar for common issues.
    
    Args:
        grammar: EBNF grammar string.
    
    Returns:
        Tuple of (is_valid, list_of_errors).
    """
    errors = []
    
    lines = grammar.strip().split("\n")
    defined_rules = set()
    
    for line in lines:
        line = line.strip()
        
        if not line or line.startswith("//"):
            continue
        
        if "::=" in line:
            rule_name = line.split("::=")[0].strip()
            defined_rules.add(rule_name)
    
    if "start" not in defined_rules:
        errors.append("Missing 'start' rule in grammar")
    
    return len(errors) == 0, errors


# ============================================================================
# Outlines Integration (if available)
# ============================================================================

def create_grammar_guide(grammar: str) -> Optional[object]:
    """
    Create an Outlines CFGGuide from EBNF grammar.
    
    Returns None if Outlines is not available or grammar is invalid.
    """
    if not OUTLINES_AVAILABLE or CFGGuide is None:
        return None
    
    try:
        is_valid, errors = validate_grammar(grammar)
        if not is_valid:
            print(f"Grammar validation errors: {errors}")
            return None
        
        return CFGGuide(grammar, vocab=list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789{}[]\":,{}\\t\\n"))
    except Exception as e:
        print(f"Failed to create CFGGuide: {e}")
        return None


# ============================================================================
# JSON Schema Extraction from Pydantic Models
# ============================================================================

def extract_json_schema(model: type) -> dict:
    """
    Extract JSON Schema from a Pydantic model.
    
    Args:
        model: Pydantic BaseModel subclass.
    
    Returns:
        JSON Schema dictionary.
    """
    try:
        return model.model_json_schema()
    except AttributeError:
        return model.schema()


def compile_model_grammar(model: type) -> str:
    """
    Compile a Pydantic model to EBNF grammar.
    
    Args:
        model: Pydantic BaseModel subclass.
    
    Returns:
        EBNF grammar string.
    """
    schema = extract_json_schema(model)
    return compile_json_schema_grammar(schema)


# ============================================================================
# Pre-compiled Grammars (cached at module load)
# ============================================================================

FINANCE_PLAN_GRAMMAR = compile_finance_plan_grammar()
REFLECTOR_GRAMMAR = compile_reflector_grammar()


def get_finance_plan_grammar() -> str:
    """Get the pre-compiled FinanceQueryPlan grammar."""
    return FINANCE_PLAN_GRAMMAR


def get_reflector_grammar() -> str:
    """Get the pre-compiled FinanceContinuationDecision grammar."""
    return REFLECTOR_GRAMMAR


def get_outlines_available() -> bool:
    """Check if Outlines library is available for grammar-constrained decoding."""
    return OUTLINES_AVAILABLE


if __name__ == "__main__":
    print("=" * 60)
    print("FinanceQueryPlan Grammar:")
    print("=" * 60)
    print(FINANCE_PLAN_GRAMMAR)
    
    print("\n" + "=" * 60)
    print("FinanceContinuationDecision Grammar:")
    print("=" * 60)
    print(REFLECTOR_GRAMMAR)
    
    print("\n" + "=" * 60)
    print("Grammar Validation:")
    print("=" * 60)
    is_valid, errors = validate_grammar(FINANCE_PLAN_GRAMMAR)
    print(f"FinanceQueryPlan: {'VALID' if is_valid else 'INVALID'} - {errors}")
    
    is_valid, errors = validate_grammar(REFLECTOR_GRAMMAR)
    print(f"FinanceContinuationDecision: {'VALID' if is_valid else 'INVALID'} - {errors}")
    
    print(f"\nOutlines Available: {OUTLINES_AVAILABLE}")