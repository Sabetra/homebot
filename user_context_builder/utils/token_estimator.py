"""
User Context Builder V2 - Token Budget Estimator

Utility for estimating token usage of context data.
"""
from typing import Dict, Any, List


class TokenBudgetEstimator:
    """
    Estimates token usage for context data.
    
    Target CC: <4
    """
    
    # Token estimation factors
    TOKENS_PER_TRIPLE = 35
    CHARS_PER_TOKEN = 4
    TOKENS_PER_INSIGHT = 25
    TOKENS_PER_GOAL = 70
    MOOD_PROGRESSION_TOKENS = 150
    
    @staticmethod
    def estimate(context_data: Dict[str, Any]) -> int:
        """
        Estimate total tokens for context data.
        
        Args:
            context_data: Dictionary with context components
            
        Returns:
            Estimated token count
        """
        total = 0
        
        # Knowledge graph triples
        kg_triples = context_data.get('knowledge_graph', [])
        total += len(kg_triples) * TokenBudgetEstimator.TOKENS_PER_TRIPLE
        
        # Session summaries
        summaries = context_data.get('session_summaries', [])
        for summary in summaries:
            summary_text = summary.get('summary', '')
            total += len(summary_text) // TokenBudgetEstimator.CHARS_PER_TOKEN
        
        # Mood progression
        mood_data = context_data.get('mood_progression', {})
        if mood_data and mood_data.get('current_mood'):
            total += TokenBudgetEstimator.MOOD_PROGRESSION_TOKENS
        
        # User insights
        insights = context_data.get('user_insights', [])
        total += len(insights) * TokenBudgetEstimator.TOKENS_PER_INSIGHT
        
        # Care goals
        goals = context_data.get('care_goals', [])
        total += len(goals) * TokenBudgetEstimator.TOKENS_PER_GOAL
        
        # Persistent profile (estimate ~200 tokens)
        if context_data.get('persistent_profile'):
            total += 200
        
        return total
