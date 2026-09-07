"""
Feedback Analysis Script
========================

Analysiert gesammelte User-Feedbacks und generiert Reports.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List
import sys

# Add parent directory for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from utils.feedback_logger import FeedbackLogger


def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


def analyze_feedbacks(feedback_file: str = "user_feedback.jsonl"):
    """Comprehensive feedback analysis."""
    
    logger = FeedbackLogger(feedback_file)
    
    print_header("📊 USER FEEDBACK ANALYSIS")
    
    # Overall statistics
    stats = logger.get_statistics()
    
    if "error" in stats:
        print(f"❌ Error: {stats['error']}")
        return
    
    if stats.get("total", 0) == 0:
        print("ℹ️  No feedback data available yet.")
        return
    
    # Overall metrics
    print("📈 OVERALL METRICS")
    print("-" * 60)
    print(f"Total Feedbacks:      {stats['total']}")
    print(f"Positive:             {stats['positive']} ({stats['positive']/stats['total']*100:.1f}%)")
    print(f"Negative:             {stats['negative']} ({stats['negative']/stats['total']*100:.1f}%)")
    print(f"Satisfaction Rate:    {stats['satisfaction_rate']:.1f}%")
    
    if stats.get('avg_response_time_ms'):
        print(f"Avg Response Time:    {stats['avg_response_time_ms']:.2f}ms")
    
    # By search depth
    if stats.get('by_search_depth'):
        print_header("🔍 SATISFACTION BY SEARCH DEPTH (k)")
        
        print(f"{'k':<5} {'Satisfaction':<15} {'Visual'}")
        print("-" * 60)
        
        for k, satisfaction in sorted(stats['by_search_depth'].items()):
            bar = "█" * int(satisfaction / 5) + "░" * (20 - int(satisfaction / 5))
            print(f"{k:<5} {satisfaction:>6.1f}%        {bar}")
        
        # Find best k
        best_k = max(stats['by_search_depth'].items(), key=lambda x: x[1])
        print(f"\n✅ Best performing k: {best_k[0]} ({best_k[1]:.1f}% satisfaction)")
    
    # Negative feedback reasons
    if stats.get('negative_reasons'):
        print_header("⚠️  NEGATIVE FEEDBACK REASONS")
        
        total_negative = sum(stats['negative_reasons'].values())
        
        print(f"{'Reason':<35} {'Count':<8} {'%':<8} {'Visual'}")
        print("-" * 60)
        
        for reason, count in sorted(stats['negative_reasons'].items(), key=lambda x: -x[1]):
            percentage = count / total_negative * 100
            bar = "█" * int(percentage / 5) + "░" * (20 - int(percentage / 5))
            print(f"{reason:<35} {count:<8} {percentage:>6.1f}%  {bar}")
        
        # Recommendations
        print("\n💡 RECOMMENDATIONS:")
        top_reason = max(stats['negative_reasons'].items(), key=lambda x: x[1])
        
        if top_reason[0] == "Irrelevante Ergebnisse":
            print("   → Consider increasing confidence thresholds")
            print("   → Review RAG search algorithm")
        elif top_reason[0] == "Zu langsam":
            print("   → Optimize search performance")
            print("   → Consider using fast path more often for low k values")
        elif top_reason[0] == "Zu wenig Ergebnisse":
            print("   → Consider lowering confidence thresholds")
            print("   → Review adaptive confidence scaling")
    
    # Recent feedbacks
    print_header("🕒 RECENT FEEDBACKS (Last 5)")
    
    recent = logger.get_recent_feedbacks(limit=5)
    
    for i, fb in enumerate(recent, 1):
        emoji = "👍" if fb['feedback'] == 'positive' else "👎"
        timestamp = fb['timestamp'][:19]  # Remove microseconds
        query = fb['query'][:50] + "..." if len(fb['query']) > 50 else fb['query']
        k = fb.get('search_depth', 'N/A')
        
        print(f"{i}. {emoji} [{timestamp}] k={k}")
        print(f"   Query: {query}")
        
        if fb['feedback'] == 'negative' and fb.get('reason'):
            print(f"   Reason: {fb['reason']}")
        
        print()
    
    # Export summary
    print_header("💾 EXPORT SUMMARY")
    
    summary_file = f"feedback_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Summary exported to: {summary_file}")
    
    print("\n" + "=" * 60 + "\n")


def compare_periods(
    feedback_file: str = "user_feedback.jsonl",
    days_ago: int = 7
):
    """Compare feedback from last N days vs. before."""
    
    print_header(f"📅 PERIOD COMPARISON (Last {days_ago} days vs. Before)")
    
    if not os.path.exists(feedback_file):
        print("❌ No feedback file found")
        return
    
    cutoff_date = datetime.now() - timedelta(days=days_ago)
    
    recent_feedbacks = []
    older_feedbacks = []
    
    with open(feedback_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                fb = json.loads(line)
                fb_date = datetime.fromisoformat(fb['timestamp'])
                
                if fb_date >= cutoff_date:
                    recent_feedbacks.append(fb)
                else:
                    older_feedbacks.append(fb)
    
    def calc_satisfaction(feedbacks):
        if not feedbacks:
            return 0
        positive = sum(1 for f in feedbacks if f['feedback'] == 'positive')
        return positive / len(feedbacks) * 100
    
    recent_sat = calc_satisfaction(recent_feedbacks)
    older_sat = calc_satisfaction(older_feedbacks)
    
    print(f"Recent ({days_ago} days):")
    print(f"  - Feedbacks: {len(recent_feedbacks)}")
    print(f"  - Satisfaction: {recent_sat:.1f}%")
    
    print(f"\nOlder (before {days_ago} days):")
    print(f"  - Feedbacks: {len(older_feedbacks)}")
    print(f"  - Satisfaction: {older_sat:.1f}%")
    
    if len(recent_feedbacks) > 0 and len(older_feedbacks) > 0:
        diff = recent_sat - older_sat
        emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
        print(f"\n{emoji} Change: {diff:+.1f}%")
        
        if abs(diff) >= 5:
            if diff > 0:
                print("✅ Significant improvement!")
            else:
                print("⚠️  Significant decline - review recent changes")
    
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Analyze user feedback")
    parser.add_argument(
        '--file',
        default='user_feedback.jsonl',
        help='Path to feedback JSONL file'
    )
    parser.add_argument(
        '--compare',
        type=int,
        metavar='DAYS',
        help='Compare last N days vs. before'
    )
    
    args = parser.parse_args()
    
    # Run analysis
    analyze_feedbacks(args.file)
    
    # Run comparison if requested
    if args.compare:
        compare_periods(args.file, args.compare)
