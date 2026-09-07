"""
Adaptive Planner für Tool Reflection
=====================================

Implementiert Adaptive Bounded ReAct mit 2 Reflections:
- Reflection 1: Daten-Qualität (web_search, rag_search)
- Reflection 2: Tool-Vervollständigung (calculator, create_diagram, etc.)

Author: Implementation 2025-10-10
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, TYPE_CHECKING
import logging
import json
import time

if TYPE_CHECKING:
    from agent.agent_types import ToolResult, ToolCall

logger = logging.getLogger(__name__)

# Import Reflection Prompts
from agent.reflection_prompts import (
    REFLECTION_1_SYSTEM,
    REFLECTION_1_USER_TEMPLATE,
    REFLECTION_2_SYSTEM,
    REFLECTION_2_USER_TEMPLATE,
    build_results_summary,
    build_data_insights
)
from utils.token_manager import estimate_prompt_tokens, estimate_structured_output_tokens


class AdaptivePlanner:
    """
    Adaptive Tool Planning mit Reflection Loop.
    
    Verantwortlich für:
    - Reflection 1: Daten-Qualität bewerten
    - Reflection 2: Tool-Vervollständigung prüfen
    - Zusätzliche Tool-Calls planen
    - Confidence-basierte Early Exits
    """
    
    def __init__(self, orchestrator):
        """
        Args:
            orchestrator: AgentOrchestrator-Instanz (für LLM-Zugriff und Tool-Execution)
        """
        self.orchestrator = orchestrator
        self.max_reflections = 2  # Fest: 1x Daten-Qualität, 1x Tool-Completeness
        
        # Thresholds
        self.confidence_done_threshold = 0.85
        self.confidence_tools_threshold = 0.4
        
        logger.info("✅ AdaptivePlanner initialisiert (max_reflections=2)")
    
    def adaptive_tool_loop(
        self,
        query: str,
        initial_results: List[Any],  # ToolResult
        history: List[Dict[str, Any]],
        max_reflections: Optional[int] = None
    ) -> List[Any]:  # ToolResult
        """
        Führt Adaptive Reflection Loop durch.
        
        Args:
            query: User-Query
            initial_results: Initiale Tool-Results
            history: Chat-History
            max_reflections: Optionale Überschreibung (default: 2)
            
        Returns:
            Alle Tool-Results (initial + additional)
        """
        max_iter = max_reflections if max_reflections is not None else self.max_reflections
        logger.info(f"🔄 Adaptive Planning Loop startet (max_reflections={max_iter})")
        
        all_results = list(initial_results)
        
        # ===================================================================
        # REFLECTION 1: DATEN-QUALITÄT
        # ===================================================================
        if max_iter >= 1:
            logger.info("🔍 Reflection 1: Daten-Qualität bewerten...")
            
            reflection_1 = self._reflect_data_quality(query, all_results, iteration=1)
            
            # Early Exit Check
            if reflection_1.get("confidence_done", 0) > self.confidence_done_threshold:
                logger.info(f"✅ Reflection 1: Early Exit (confidence_done={reflection_1['confidence_done']:.2f})")
            elif reflection_1.get("confidence_more_tools", 0) < self.confidence_tools_threshold:
                logger.info(f"⚠️ Reflection 1: Keine Tools nötig (confidence_tools={reflection_1['confidence_more_tools']:.2f})")
            else:
                # Plan & Execute Additional Tools
                additional_calls = self._plan_additional_tools_from_reflection(query, reflection_1)
                
                if additional_calls:
                    logger.info(f"🔧 Reflection 1: Führe {len(additional_calls)} zusätzliche Tools aus")
                    additional_results = self.orchestrator.tools.run(additional_calls)
                    all_results.extend(additional_results)
                else:
                    logger.info("ℹ️ Reflection 1: Keine zusätzlichen Tools vorgeschlagen")
        
        # ===================================================================
        # REFLECTION 2: TOOL-VERVOLLSTÄNDIGUNG
        # ===================================================================
        if max_iter >= 2:
            logger.info("🔍 Reflection 2: Tool-Vervollständigung prüfen...")
            
            reflection_2 = self._reflect_tool_completeness(query, all_results, iteration=2)
            
            # Early Exit Check
            if reflection_2.get("confidence_done", 0) > self.confidence_done_threshold:
                logger.info(f"✅ Reflection 2: Early Exit (confidence_done={reflection_2['confidence_done']:.2f})")
            elif reflection_2.get("confidence_more_tools", 0) < self.confidence_tools_threshold:
                logger.info(f"⚠️ Reflection 2: Keine Tools nötig (confidence_tools={reflection_2['confidence_more_tools']:.2f})")
            else:
                # Plan & Execute Additional Tools
                additional_calls = self._plan_additional_tools_from_reflection(query, reflection_2)
                
                if additional_calls:
                    logger.info(f"🔧 Reflection 2: Führe {len(additional_calls)} zusätzliche Tools aus")
                    additional_results = self.orchestrator.tools.run(additional_calls)
                    all_results.extend(additional_results)
                else:
                    logger.info("ℹ️ Reflection 2: Keine zusätzlichen Tools vorgeschlagen")
        
        logger.info(f"✅ Adaptive Planning Loop abgeschlossen: {len(initial_results)} → {len(all_results)} Results")
        
        return all_results
    
    def _reflect_data_quality(
        self,
        query: str,
        current_results: List[Any],
        iteration: int = 1
    ) -> Dict[str, Any]:
        """
        REFLECTION 1: Prüft Daten-Qualität.
        
        Returns:
            {
                "confidence_done": 0.0-1.0,
                "confidence_more_tools": 0.0-1.0,
                "reasoning": str,
                "suggested_tools": [...]
            }
        """
        # Build Prompt
        results_summary = build_results_summary(current_results)
        
        user_prompt = REFLECTION_1_USER_TEMPLATE.format(
            query=query,
            iteration=iteration,
            results_summary=results_summary
        )
        
        # LLM Call
        messages = [
            {"role": "system", "content": REFLECTION_1_SYSTEM},
            {"role": "user", "content": user_prompt}
        ]

        prompt_text = "\n".join(
            str(m.get("content", "")) for m in messages if isinstance(m.get("content", ""), str)
        )
        prompt_tokens = estimate_prompt_tokens(prompt_text)
        model_n_ctx = getattr(self.orchestrator.model_loader, "get_max_context_tokens", lambda: 16384)() or 16384
        max_tokens_dynamic = estimate_structured_output_tokens(
            prompt_tokens=prompt_tokens,
            model_context_window=model_n_ctx,
            min_output_tokens=384,
            max_output_tokens=3072,
        )
        
        try:
            response = self.orchestrator.model_loader.generate_response(
                messages=messages,
                max_tokens=max_tokens_dynamic,
                temperature=0.2
            )
            
            # Parse JSON
            result = self._parse_json_response(response)
            
            logger.info(
                f"🔍 Reflection 1 Result: done={result.get('confidence_done', 0):.2f}, "
                f"more_tools={result.get('confidence_more_tools', 0):.2f}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Reflection 1 failed: {e}, using safe defaults")
            return {
                "confidence_done": 0.5,
                "confidence_more_tools": 0.3,
                "reasoning": f"Reflection failed: {e}",
                "suggested_tools": []
            }
    
    def _reflect_tool_completeness(
        self,
        query: str,
        current_results: List[Any],
        iteration: int = 2
    ) -> Dict[str, Any]:
        """
        REFLECTION 2: Prüft Tool-Vervollständigung.
        
        Returns:
            {
                "confidence_done": 0.0-1.0,
                "confidence_more_tools": 0.0-1.0,
                "reasoning": str,
                "suggested_tools": [...]
            }
        """
        # Build Insights
        data_insights = build_data_insights(current_results)
        
        # Tools already used
        tools_used = ", ".join({r.tool for r in current_results})
        
        user_prompt = REFLECTION_2_USER_TEMPLATE.format(
            query=query,
            data_insights=data_insights,
            tools_used=tools_used
        )
        
        # LLM Call
        messages = [
            {"role": "system", "content": REFLECTION_2_SYSTEM},
            {"role": "user", "content": user_prompt}
        ]

        prompt_text = "\n".join(
            str(m.get("content", "")) for m in messages if isinstance(m.get("content", ""), str)
        )
        prompt_tokens = estimate_prompt_tokens(prompt_text)
        model_n_ctx = getattr(self.orchestrator.model_loader, "get_max_context_tokens", lambda: 16384)() or 16384
        max_tokens_dynamic = estimate_structured_output_tokens(
            prompt_tokens=prompt_tokens,
            model_context_window=model_n_ctx,
            min_output_tokens=384,
            max_output_tokens=3072,
        )
        
        try:
            response = self.orchestrator.model_loader.generate_response(
                messages=messages,
                max_tokens=max_tokens_dynamic,
                temperature=0.2
            )
            
            # Parse JSON
            result = self._parse_json_response(response)
            
            logger.info(
                f"🔍 Reflection 2 Result: done={result.get('confidence_done', 0):.2f}, "
                f"more_tools={result.get('confidence_more_tools', 0):.2f}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Reflection 2 failed: {e}, using safe defaults")
            return {
                "confidence_done": 0.7,
                "confidence_more_tools": 0.2,
                "reasoning": f"Reflection failed: {e}",
                "suggested_tools": []
            }
    
    def _plan_additional_tools_from_reflection(
        self,
        query: str,
        reflection: Dict[str, Any]
    ) -> List[Any]:  # ToolCall
        """
        Plant zusätzliche Tools basierend auf Reflection-Result.
        
        Args:
            query: User-Query
            reflection: Reflection-Result mit suggested_tools
            
        Returns:
            Liste von ToolCall-Objekten
        """
        from agent.agent_types import ToolCall
        
        suggested_tools = reflection.get("suggested_tools", [])
        
        if not suggested_tools:
            return []
        
        tool_calls = []
        for suggestion in suggested_tools:
            tool_name = suggestion.get("tool", "")
            params = suggestion.get("params", {})
            reason = suggestion.get("reason", "")
            
            if not tool_name:
                continue
            
            # Normalisiere Parameter
            normalized_params = self._normalize_tool_params(tool_name, params, query)
            
            tool_calls.append(ToolCall(tool=tool_name, parameters=normalized_params))
            logger.info(f"📋 Geplant: {tool_name} ({reason})")
        
        return tool_calls
    
    def _normalize_tool_params(
        self,
        tool_name: str,
        suggested_params: Dict,
        query: str
    ) -> Dict[str, Any]:
        """
        Normalisiert Tool-Parameter aus Reflection-Suggestions.
        
        Args:
            tool_name: Name des Tools
            suggested_params: Von Reflection vorgeschlagene Parameter
            query: Original-Query als Fallback
            
        Returns:
            Normalisierte Parameter für Tool-Execution
        """
        if tool_name == "web_search":
            return {
                "query": suggested_params.get("query", query),
                "num_results": suggested_params.get("num_results", 5)
            }
        
        elif tool_name == "rag_search":
            return {
                "query": suggested_params.get("query", query),
                "k": suggested_params.get("k", self.orchestrator.rag_k if hasattr(self.orchestrator, 'rag_k') else 5)
            }
        
        elif tool_name == "calculator":
            return {
                "expression": suggested_params.get("expression", query)
            }
        
        elif tool_name in {"create_diagram", "canvas"}:
            # Keep params compatible with AgentToolkit._create_diagram schema.
            description = suggested_params.get("description")
            if not description:
                description = {
                    "type": suggested_params.get("type", "network"),
                    "title": suggested_params.get("title", query)
                }
            return {
                "description": description,
                "output_filename": suggested_params.get("output_filename", "diagram.png")
            }
        
        elif tool_name == "file_writer":
            return {
                "file_path": suggested_params.get("file_path", "output.txt"),
                "content": suggested_params.get("content", "")
            }
        
        elif tool_name == "file_reader":
            return {
                "file_path": suggested_params.get("file_path", "")
            }
        
        else:
            # Fallback: Return params as-is
            return suggested_params if suggested_params else {"query": query}
    
    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """
        Parst JSON-Response vom LLM (robust gegen Markdown-Wrapping).
        
        Args:
            response: LLM-Response (möglicherweise mit ```json``` wrapped)
            
        Returns:
            Geparster Dict
        """
        response_clean = response.strip()
        
        # Remove markdown code blocks
        if "```json" in response_clean:
            response_clean = response_clean.split("```json")[1].split("```")[0]
        elif "```" in response_clean:
            response_clean = response_clean.split("```")[1].split("```")[0]
        
        # Parse JSON
        result: Dict[str, Any] = json.loads(response_clean.strip())
        return result
