"""
GBNF Grammars for Structured LLM Output
=========================================

Stellt GBNF-Grammars bereit, die llama-cpp-python an create_completion(grammar=...)
übergeben werden. Erzwingt **garantiert valides** JSON statt hoffnungsvollem Regex-Parsing.

Model-agnostisch:
  1. MAGISTRAL_TOOL_CALL  -- Magistral Function-Calling: tool_name{"key":"val"}
  2. JSON_TOOL_CALL       -- Gemma/Generic JSON: [{"name":"func","arguments":{}}]
  3. JSON_OBJECT_GRAMMAR  -- Generisches valides JSON-Objekt
  4. REFLECTION_GRAMMAR   -- Reflection-Response {confidence, reasoning, ...}
  5. CRAG_ARRAY_GRAMMAR   -- CRAG-Evaluation [{id, verdict}]

Ref: https://github.com/ggerganov/llama.cpp/blob/master/grammars/README.md
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import List, Optional

logger = logging.getLogger(__name__)

# Cache für kompilierte Grammars (singleton pro String)
_grammar_cache: dict = {}


def get_grammar(grammar_str: str):
    """Gibt eine kompilierte LlamaGrammar zurück (gecached).
    
    Returns:
        LlamaGrammar-Instanz oder None bei Fehler
    """
    if grammar_str in _grammar_cache:
        return _grammar_cache[grammar_str]
    
    try:
        from llama_cpp import LlamaGrammar
        grammar = LlamaGrammar.from_string(grammar_str)
        _grammar_cache[grammar_str] = grammar
        return grammar
    except ImportError:
        logger.warning("[GRAMMAR] llama_cpp nicht verfügbar -- Grammar-Enforcement deaktiviert")
        return None
    except Exception as e:
        logger.error(f"[GRAMMAR] Kompilierung fehlgeschlagen: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Tool-Call Grammar
#    Format: tool_name{"key": "value", ...}
#    Oder reiner Text (wenn kein Tool-Call nötig)
# ═══════════════════════════════════════════════════════════════════════════════

def build_tool_call_grammar(tool_names: List[str]) -> str:
    """Baut eine GBNF-Grammar für Magistral-Format Tool-Calls.
    
    Erlaubte Outputs:
      - Freier Text (Antwort ohne Tool-Call)
      - tool_name{...json...}  (ein oder mehrere Tool-Calls)
      - Text gefolgt von tool_name{...json...}
    
    Args:
        tool_names: Liste der bekannten Tool-Namen
    
    Returns:
        GBNF Grammar String
    """
    # Escape tool names für GBNF (Tool-Namen als literal strings)
    tool_alternatives = " | ".join(
        f'"{name}"' for name in tool_names
    )
    
    return f'''
root        ::= free-text | tool-sequence | mixed-output
mixed-output ::= free-text tool-sequence
tool-sequence ::= tool-call (tool-call)*
tool-call   ::= tool-name json-object
tool-name   ::= {tool_alternatives}
free-text   ::= [^{{]+ 

json-object ::= "{{" ws members ws "}}"
members     ::= pair ("," ws pair)*
pair        ::= ws string ws ":" ws value
value       ::= string | number | json-object | array | "true" | "false" | "null"
array       ::= "[" ws (value ("," ws value)*)? ws "]"
string      ::= "\\"" char* "\\""
char        ::= [^"\\\\\\x00-\\x1f] | "\\\\" escape
escape      ::= ["\\\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number      ::= "-"? int frac? exp?
int         ::= "0" | [1-9] [0-9]*
frac        ::= "." [0-9]+
exp         ::= [eE] [+-]? [0-9]+
ws          ::= [ \\t\\n]*
'''


# ═══════════════════════════════════════════════════════════════════════════════
# 1b. JSON Tool-Call Grammar (Gemma, GPT, generisch)
#     Format: [{"name": "tool", "arguments": {"key": "val"}}]
#     Oder reiner Text (wenn kein Tool-Call nötig)
# ═══════════════════════════════════════════════════════════════════════════════

def build_json_tool_call_grammar(tool_names: List[str]) -> str:
    """Baut eine GBNF-Grammar für JSON-Array Tool-Calls (Gemma-Stil).
    
    Erlaubte Outputs:
      - Freier Text (Antwort ohne Tool-Call)
      - [{"name":"tool","arguments":{...}}]  (ein oder mehrere)
      - Text gefolgt von JSON-Array
    
    Args:
        tool_names: Liste der bekannten Tool-Namen
    
    Returns:
        GBNF Grammar String
    """
    tool_alternatives = " | ".join(
        f'"\\""  "{name}"  "\\""' for name in tool_names
    )
    
    return f'''
root          ::= free-text | tool-array | mixed-output
mixed-output  ::= free-text tool-array
tool-array    ::= "[" ws tool-obj ("," ws tool-obj)* ws "]"
tool-obj      ::= "{{" ws name-pair "," ws args-pair ws "}}"
name-pair     ::= "\\"name\\"" ws ":" ws tool-name-val
args-pair     ::= "\\"arguments\\"" ws ":" ws json-object
tool-name-val ::= {tool_alternatives}
free-text     ::= [^[{{]+ 

json-object ::= "{{" ws members ws "}}"
members     ::= pair ("," ws pair)*
pair        ::= ws string ws ":" ws value
value       ::= string | number | json-object | array | "true" | "false" | "null"
array       ::= "[" ws (value ("," ws value)*)? ws "]"
string      ::= "\\"" char* "\\""
char        ::= [^"\\\\\\x00-\\x1f] | "\\\\" escape
escape      ::= ["\\\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number      ::= "-"? int frac? exp?
int         ::= "0" | [1-9] [0-9]*
frac        ::= "." [0-9]+
exp         ::= [eE] [+-]? [0-9]+
ws          ::= [ \\t\\n]*
'''


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Generic JSON Object Grammar
# ═══════════════════════════════════════════════════════════════════════════════

JSON_OBJECT_GRAMMAR = r'''
root   ::= object
object ::= "{" ws members ws "}"
members ::= pair ("," ws pair)*
pair   ::= ws string ws ":" ws value
value  ::= string | number | object | array | "true" | "false" | "null"
array  ::= "[" ws (value ("," ws value)*)? ws "]"
string ::= "\"" char* "\""
char   ::= [^"\\\x00-\x1f] | "\\" escape
escape ::= ["\\bfnrt/] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number ::= "-"? int frac? exp?
int    ::= "0" | [1-9] [0-9]*
frac   ::= "." [0-9]+
exp    ::= [eE] [+-]? [0-9]+
ws     ::= [ \t\n]*
'''


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Reflection Grammar
#    Erzwingt: {"confidence": 0.X, "reasoning": "...", "needs_retry": bool, ...}
# ═══════════════════════════════════════════════════════════════════════════════

REFLECTION_GRAMMAR = r'''
root    ::= "{" ws confidence-pair "," ws reasoning-pair ("," ws extra-pair)* ws "}"

confidence-pair ::= "\"confidence\"" ws ":" ws number
reasoning-pair  ::= "\"reasoning\"" ws ":" ws string
extra-pair      ::= string ws ":" ws value

value  ::= string | number | object | array | "true" | "false" | "null"
object ::= "{" ws (pair ("," ws pair)*)? ws "}"
pair   ::= ws string ws ":" ws value
array  ::= "[" ws (value ("," ws value)*)? ws "]"
string ::= "\"" char* "\""
char   ::= [^"\\\x00-\x1f] | "\\" escape
escape ::= ["\\bfnrt/] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number ::= "-"? int frac? exp?
int    ::= "0" | [1-9] [0-9]*
frac   ::= "." [0-9]+
exp    ::= [eE] [+-]? [0-9]+
ws     ::= [ \t\n]*
'''


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CRAG Evaluation Grammar
#    Erzwingt: [{"id": N, "verdict": "relevant"|"ambiguous"|"irrelevant"}, ...]
# ═══════════════════════════════════════════════════════════════════════════════

CRAG_ARRAY_GRAMMAR = r'''
root    ::= "[" ws entry ("," ws entry)* ws "]"
entry   ::= "{" ws id-pair "," ws verdict-pair ws "}"

id-pair      ::= "\"id\"" ws ":" ws int
verdict-pair ::= "\"verdict\"" ws ":" ws verdict-value
verdict-value ::= "\"relevant\"" | "\"ambiguous\"" | "\"irrelevant\""

int    ::= "0" | [1-9] [0-9]*
ws     ::= [ \t\n]*
'''


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Date Validator Array Grammar
#    Erzwingt: [{"source_index": N, "final_score": 0.X, "warning_level": "...", "reasoning": "..."}, ...]
# ═══════════════════════════════════════════════════════════════════════════════

DATE_VALIDATOR_ARRAY_GRAMMAR = r'''
root    ::= "[" ws entry ("," ws entry)* ws "]"
entry   ::= "{" ws si-pair "," ws fs-pair "," ws wl-pair "," ws rs-pair ws "}"

si-pair ::= "\"source_index\"" ws ":" ws int
fs-pair ::= "\"final_score\"" ws ":" ws number
wl-pair ::= "\"warning_level\"" ws ":" ws wl-value
rs-pair ::= "\"reasoning\"" ws ":" ws string

wl-value ::= "\"none\"" | "\"low\"" | "\"medium\"" | "\"high\""

string ::= "\"" char* "\""
char   ::= [^"\\\x00-\x1f] | "\\" escape
escape ::= ["\\bfnrt/] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number ::= "-"? int frac? exp?
int    ::= "0" | [1-9] [0-9]*
frac   ::= "." [0-9]+
exp    ::= [eE] [+-]? [0-9]+
ws     ::= [ \t\n]*
'''


# ═══════════════════════════════════════════════════════════════════════════════
# 6. RAG Sufficiency Decision Grammar
#    Erzwingt exakt ein binäres Label: SUFFICIENT | INSUFFICIENT
# ═══════════════════════════════════════════════════════════════════════════════

RAG_SUFFICIENCY_DECISION_GRAMMAR = r'''
root     ::= ws decision ws
decision ::= "SUFFICIENT" | "INSUFFICIENT"
ws       ::= [ \t\n]*
'''


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience Functions
# ═══════════════════════════════════════════════════════════════════════════════

def get_tool_call_grammar(
    tool_names: List[str],
    model_family: str = "magistral",
):
    """Kompilierte Grammar für Tool-Calls (model-agnostisch).
    
    Args:
        tool_names: Liste der bekannten Tool-Namen
        model_family: 'magistral' → func_name{json}, 
                      'gemma'/andere → [{"name":...,"arguments":...}]
    """
    if model_family == "magistral":
        grammar_str = build_tool_call_grammar(tool_names)
    else:
        grammar_str = build_json_tool_call_grammar(tool_names)
    return get_grammar(grammar_str)


def get_json_grammar():
    """Kompilierte Grammar für generisches JSON-Objekt."""
    return get_grammar(JSON_OBJECT_GRAMMAR)


def get_reflection_grammar():
    """Kompilierte Grammar für Reflection-Output."""
    return get_grammar(REFLECTION_GRAMMAR)


def get_crag_grammar():
    """Kompilierte Grammar für CRAG-Evaluation."""
    return get_grammar(CRAG_ARRAY_GRAMMAR)


def get_date_validator_grammar():
    """Kompilierte Grammar für Date-Validator-Array."""
    return get_grammar(DATE_VALIDATOR_ARRAY_GRAMMAR)


def get_rag_sufficiency_grammar():
    """Kompilierte Grammar für RAG-Sufficiency-Gating (binäre Entscheidung)."""
    return get_grammar(RAG_SUFFICIENCY_DECISION_GRAMMAR)
