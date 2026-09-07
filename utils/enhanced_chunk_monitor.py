#!/usr/bin/env python3
"""
Erweiterte Chunk-Monitoring und Analytics
========================================

Bietet detaillierte Metriken und Visualisierungen für Chunk-Performance
"""

import os
import sys
import sqlite3
import json
import re
import time
from typing import List, Dict, Any, Optional
from collections import defaultdict, Counter
import statistics
from pathlib import Path

# Canonical ModelLoader lives in scripts.model_loader. The legacy
# top-level ``model_loader`` is a re-export shim kept only for notebooks.
import os, sys
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from scripts.model_loader import ModelLoader
from agent.llm_knowledge_graph import LLMKnowledgeGraphExtractor

class EnhancedChunkMonitor:
    """
    Erweiterte Überwachung und Analyse von Chunk-Performance
    """
    
    def __init__(self, db_path: str = "rag_store.db"):
        self.db_path = db_path
        self.model_loader = ModelLoader()
        self.kg_extractor = None
        self.chunk_data = []
        self.performance_metrics = {}
        
    def initialize_llm(self):
        """Initialisiert LLM für Tests"""
        if not self.model_loader.is_model_loaded():
            print("🔄 Lade Modell für erweiterte Chunk-Analyse...")
            
            _lm_base = Path.home() / ".cache" / "lm-studio" / "models" / "lmstudio-community"
            model_paths = [
                str(_lm_base / "Mistral-Small-3.2-24B-Instruct-2506-GGUF" / "Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf"),
                str(_lm_base / "Magistral-Small-2509-GGUF" / "Magistral-Small-2509-Q4_K_M.gguf")
            ]
            
            for model_path in model_paths:
                if os.path.exists(model_path):
                    print(f"   Lade: {os.path.basename(model_path)}")
                    success = self.model_loader.load_model(model_path)
                    if success:
                        break
            else:
                print("❌ Kein Modell gefunden!")
                return False
        
        # Initialisiere KG-Extraktor
        try:
            from agent.llm_knowledge_graph import LLMKnowledgeGraphExtractor
            self.kg_extractor = LLMKnowledgeGraphExtractor()
            print("✅ KG-Extraktor initialisiert")
            return True
        except Exception as e:
            print(f"❌ KG-Extraktor Fehler: {e}")
            return False
    
    def collect_chunk_metrics(self, chunk_limit: int = 20) -> Dict[str, Any]:
        """Sammelt umfassende Metriken für Chunks"""
        print(f"📊 Sammle erweiterte Metriken für {chunk_limit} Chunks...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Lade Chunks aus Datenbank
            cursor.execute('''
                SELECT chunk_id, doc_id, text, metadata
                FROM chunks 
                ORDER BY RANDOM()
                LIMIT ?
            ''', (chunk_limit,))
            
            chunks = cursor.fetchall()
            
            if not chunks:
                print("❌ Keine Chunks in der Datenbank gefunden!")
                return {}
            
            print(f"✅ {len(chunks)} Chunks geladen")
            
            # Sammle Performance-Daten
            performance_data = []
            text_metrics = []
            success_rates = defaultdict(int)
            processing_times = defaultdict(list)
            
            for i, (chunk_id, doc_id, text, metadata) in enumerate(chunks, 1):
                print(f"\r   Analysiere Chunk {i}/{len(chunks)}...", end="", flush=True)
                
                chunk_metrics = self._analyze_single_chunk({
                    'chunk_id': chunk_id,
                    'doc_id': doc_id, 
                    'text': text,
                    'metadata': metadata
                })
                
                performance_data.append(chunk_metrics)
                text_metrics.append(chunk_metrics['text_analysis'])
                
                # Sammle Erfolgsraten
                for test_name, result in chunk_metrics['test_results'].items():
                    if 'error' not in result:
                        if test_name == 'kg_extraction':
                            if result.get('success', False):
                                success_rates[test_name] += 1
                        else:
                            if not result.get('is_empty', True):
                                success_rates[test_name] += 1
                
                # Sammle Processing-Zeiten (falls verfügbar)
                for test_name, result in chunk_metrics['test_results'].items():
                    if 'processing_time' in result:
                        processing_times[test_name].append(result['processing_time'])
            
            print()  # Neue Zeile nach Progress
            
            # Berechne aggregierte Metriken
            aggregated_metrics = self._compute_aggregated_metrics(
                performance_data, text_metrics, success_rates, processing_times, len(chunks)
            )
            
            self.performance_metrics = aggregated_metrics
            self.chunk_data = performance_data
            
            return aggregated_metrics
            
        finally:
            conn.close()
    
    def _analyze_single_chunk(self, chunk: Dict) -> Dict[str, Any]:
        """Analysiert einen einzelnen Chunk umfassend"""
        text = chunk['text']
        
        # Text-Analyse
        text_analysis = {
            'length': len(text),
            'word_count': len(text.split()),
            'sentence_count': len([s for s in re.split(r'[.!?]+', text) if s.strip()]),
            'paragraph_count': len([p for p in text.split('\n\n') if p.strip()]),
            'avg_word_length': statistics.mean([len(word) for word in text.split()]) if text.split() else 0,
            'has_numbers': bool(re.search(r'\d', text)),
            'has_punctuation': bool(re.search(r'[^\w\s]', text)),
            'has_unicode': any(ord(char) > 127 for char in text),
            'has_urls': bool(re.search(r'https?://', text)),
            'has_email': bool(re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)),
            'has_dates': bool(re.search(r'\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b', text)),
            'uppercase_ratio': sum(1 for c in text if c.isupper()) / len(text) if text else 0,
            'digit_ratio': sum(1 for c in text if c.isdigit()) / len(text) if text else 0,
            'whitespace_ratio': sum(1 for c in text if c.isspace()) / len(text) if text else 0,
        }
        
        # LLM-Tests mit Zeitmessung
        test_results = {}
        
        # Test 1: Basis-Generation
        try:
            import time as time_module
            start_time = time_module.time()
            response = self.model_loader.generate_response(
                prompt=f"Fasse diesen Text kurz zusammen: {text[:500]}",
                max_tokens=100,
                temperature=0.2
            )
            processing_time = time_module.time() - start_time
            
            test_results['basic_generation'] = {
                'response': response,
                'is_empty': len(str(response).strip()) == 0,
                'length': len(str(response)),
                'processing_time': processing_time,
                'tokens_per_second': len(str(response).split()) / processing_time if processing_time > 0 else 0
            }
        except Exception as e:
            test_results['basic_generation'] = {'error': str(e)}
        
        # Test 2: KG-Extraktion
        if self.kg_extractor:
            try:
                import time as time_module
                start_time = time_module.time()
                kg_result = self.kg_extractor.extract_knowledge_graph(text)
                processing_time = time_module.time() - start_time
                
                test_results['kg_extraction'] = {
                    'success': kg_result is not None and len(kg_result) > 0,
                    'triples_count': len(kg_result) if kg_result else 0,
                    'processing_time': processing_time,
                    'triples_per_second': len(kg_result) / processing_time if processing_time > 0 and kg_result else 0
                }
            except Exception as e:
                test_results['kg_extraction'] = {'error': str(e)}
        
        # Test 3: Strukturierte Ausgabe
        try:
            import time as time_module
            start_time = time_module.time()
            structured_prompt = f"""
            Analysiere diesen Text und gib das Ergebnis im JSON-Format zurück:
            Text: {text[:300]}
            
            Format:
            {{"thema": "...", "hauptpunkte": ["...", "..."], "sentiment": "..."}}
            """
            response = self.model_loader.generate_response(
                prompt=structured_prompt,
                max_tokens=200,
                temperature=0.1
            )
            processing_time = time_module.time() - start_time
            
            # Versuche JSON zu parsen
            is_valid_json = False
            try:
                json.loads(str(response))
                is_valid_json = True
            except:
                pass
            
            test_results['structured_output'] = {
                'response': response,
                'is_empty': len(str(response).strip()) == 0,
                'is_valid_json': is_valid_json,
                'length': len(str(response)),
                'processing_time': processing_time
            }
        except Exception as e:
            test_results['structured_output'] = {'error': str(e)}
        
        return {
            'chunk_id': chunk['chunk_id'],
            'doc_id': chunk['doc_id'],
            'text_analysis': text_analysis,
            'test_results': test_results,
            'overall_success': self._calculate_chunk_success_score(test_results)
        }
    
    def _calculate_chunk_success_score(self, test_results: Dict) -> float:
        """Berechnet einen Erfolgs-Score für einen Chunk (0-1)"""
        scores = []
        
        for test_name, result in test_results.items():
            if 'error' in result:
                scores.append(0.0)
            elif test_name == 'kg_extraction':
                scores.append(1.0 if result.get('success', False) else 0.0)
            elif test_name == 'structured_output':
                if result.get('is_empty', True):
                    scores.append(0.0)
                elif result.get('is_valid_json', False):
                    scores.append(1.0)
                else:
                    scores.append(0.5)  # Partial success
            else:
                scores.append(0.0 if result.get('is_empty', True) else 1.0)
        
        return statistics.mean(scores) if scores else 0.0
    
    def _compute_aggregated_metrics(self, performance_data: List, text_metrics: List, 
                                  success_rates: Dict, processing_times: Dict, 
                                  total_chunks: int) -> Dict[str, Any]:
        """Berechnet aggregierte Metriken"""
        
        # Text-Statistiken
        lengths = [tm['length'] for tm in text_metrics]
        word_counts = [tm['word_count'] for tm in text_metrics]
        success_scores = [pd['overall_success'] for pd in performance_data]
        
        text_stats = {
            'length': {
                'mean': statistics.mean(lengths),
                'median': statistics.median(lengths),
                'min': min(lengths),
                'max': max(lengths),
                'std_dev': statistics.stdev(lengths) if len(lengths) > 1 else 0
            },
            'word_count': {
                'mean': statistics.mean(word_counts),
                'median': statistics.median(word_counts),
                'min': min(word_counts),
                'max': max(word_counts)
            },
            'characteristics': {
                'has_numbers': sum(1 for tm in text_metrics if tm['has_numbers']) / len(text_metrics),
                'has_unicode': sum(1 for tm in text_metrics if tm['has_unicode']) / len(text_metrics),
                'has_urls': sum(1 for tm in text_metrics if tm['has_urls']) / len(text_metrics),
                'has_email': sum(1 for tm in text_metrics if tm['has_email']) / len(text_metrics),
                'has_dates': sum(1 for tm in text_metrics if tm['has_dates']) / len(text_metrics),
                'avg_uppercase_ratio': statistics.mean([tm['uppercase_ratio'] for tm in text_metrics]),
                'avg_digit_ratio': statistics.mean([tm['digit_ratio'] for tm in text_metrics])
            }
        }
        
        # Erfolgsraten
        success_rates_percent = {
            test_name: (count / total_chunks * 100) 
            for test_name, count in success_rates.items()
        }
        
        # Performance-Statistiken
        performance_stats = {}
        for test_name, times in processing_times.items():
            if times:
                performance_stats[test_name] = {
                    'avg_time': statistics.mean(times),
                    'median_time': statistics.median(times),
                    'min_time': min(times),
                    'max_time': max(times)
                }
        
        # Top/Bottom Performer
        sorted_chunks = sorted(performance_data, key=lambda x: x['overall_success'], reverse=True)
        top_performers = sorted_chunks[:3]
        bottom_performers = sorted_chunks[-3:]
        
        return {
            'total_chunks': total_chunks,
            'overall_success_rate': statistics.mean(success_scores) * 100,
            'text_statistics': text_stats,
            'success_rates': success_rates_percent,
            'performance_statistics': performance_stats,
            'top_performers': top_performers,
            'bottom_performers': bottom_performers,
            'score_distribution': {
                'excellent': sum(1 for score in success_scores if score >= 0.8) / len(success_scores) * 100,
                'good': sum(1 for score in success_scores if 0.6 <= score < 0.8) / len(success_scores) * 100,
                'average': sum(1 for score in success_scores if 0.4 <= score < 0.6) / len(success_scores) * 100,
                'poor': sum(1 for score in success_scores if 0.2 <= score < 0.4) / len(success_scores) * 100,
                'failing': sum(1 for score in success_scores if score < 0.2) / len(success_scores) * 100
            }
        }
    
    def print_comprehensive_report(self):
        """Druckt umfassenden Analysebericht"""
        if not self.performance_metrics:
            print("❌ Keine Metriken verfügbar. Führe zuerst collect_chunk_metrics() aus.")
            return
        
        metrics = self.performance_metrics
        
        print(f"\n{'='*100}")
        print("🚀 UMFASSENDER CHUNK-PERFORMANCE BERICHT")
        print(f"{'='*100}")
        
        # Übersicht
        print(f"\n📋 ÜBERSICHT:")
        print(f"   Analysierte Chunks: {metrics['total_chunks']}")
        print(f"   Gesamterfolgrate: {metrics['overall_success_rate']:.1f}%")
        
        # Score-Verteilung
        print(f"\n🎯 ERFOLGS-VERTEILUNG:")
        dist = metrics['score_distribution']
        print(f"   Exzellent (≥80%): {dist['excellent']:.1f}%")
        print(f"   Gut (60-79%):     {dist['good']:.1f}%")
        print(f"   Durchschnitt:     {dist['average']:.1f}%")
        print(f"   Schwach (20-39%): {dist['poor']:.1f}%")
        print(f"   Versagend (<20%): {dist['failing']:.1f}%")
        
        # Text-Statistiken
        print(f"\n📊 TEXT-STATISTIKEN:")
        text_stats = metrics['text_statistics']
        print(f"   Durchschnittliche Länge: {text_stats['length']['mean']:.0f} Zeichen")
        print(f"   Median-Länge: {text_stats['length']['median']:.0f} Zeichen")
        print(f"   Längen-Bereich: {text_stats['length']['min']}-{text_stats['length']['max']} Zeichen")
        print(f"   Standard-Abweichung: {text_stats['length']['std_dev']:.0f}")
        
        print(f"   Durchschnittliche Wörter: {text_stats['word_count']['mean']:.0f}")
        print(f"   Wörter-Bereich: {text_stats['word_count']['min']}-{text_stats['word_count']['max']}")
        
        # Charakteristiken
        print(f"\n🔍 TEXT-CHARAKTERISTIKEN:")
        char = text_stats['characteristics']
        print(f"   Enthalten Zahlen: {char['has_numbers']*100:.1f}%")
        print(f"   Enthalten Unicode: {char['has_unicode']*100:.1f}%")
        print(f"   Enthalten URLs: {char['has_urls']*100:.1f}%")
        print(f"   Enthalten E-Mails: {char['has_email']*100:.1f}%")
        print(f"   Enthalten Daten: {char['has_dates']*100:.1f}%")
        print(f"   Durchschn. Großbuchstaben-Anteil: {char['avg_uppercase_ratio']*100:.1f}%")
        print(f"   Durchschn. Ziffern-Anteil: {char['avg_digit_ratio']*100:.1f}%")
        
        # Test-Erfolgsraten
        print(f"\n✅ TEST-ERFOLGSRATEN:")
        for test_name, rate in metrics['success_rates'].items():
            print(f"   {test_name:<20}: {rate:5.1f}%")
        
        # Performance-Statistiken
        if metrics['performance_statistics']:
            print(f"\n⚡ PERFORMANCE-STATISTIKEN:")
            for test_name, stats in metrics['performance_statistics'].items():
                print(f"   {test_name}:")
                print(f"      Durchschn. Zeit: {stats['avg_time']:.2f}s")
                print(f"      Median Zeit: {stats['median_time']:.2f}s")
                print(f"      Bereich: {stats['min_time']:.2f}-{stats['max_time']:.2f}s")
        
        # Top Performer
        print(f"\n🏆 TOP 3 PERFORMER:")
        for i, chunk in enumerate(metrics['top_performers'], 1):
            score = chunk['overall_success'] * 100
            length = chunk['text_analysis']['length']
            print(f"   {i}. Chunk {chunk['chunk_id']}: {score:.1f}% ({length} Zeichen)")
        
        # Bottom Performer  
        print(f"\n⚠️  BOTTOM 3 PERFORMER:")
        for i, chunk in enumerate(metrics['bottom_performers'], 1):
            score = chunk['overall_success'] * 100
            length = chunk['text_analysis']['length']
            print(f"   {i}. Chunk {chunk['chunk_id']}: {score:.1f}% ({length} Zeichen)")
        
        # Empfehlungen
        self._print_recommendations()
        
        print(f"\n{'='*100}")
    
    def _print_recommendations(self):
        """Druckt Empfehlungen basierend auf den Metriken"""
        print(f"\n💡 EMPFEHLUNGEN:")
        
        metrics = self.performance_metrics
        overall_rate = metrics['overall_success_rate']
        
        if overall_rate < 50:
            print("   🚨 KRITISCH: Sehr niedrige Erfolgsrate!")
            print("   → Überprüfe Modell-Konfiguration und Prompts")
            print("   → Implementiere robuste Fallback-Mechanismen")
        elif overall_rate < 75:
            print("   ⚠️  Mäßige Erfolgsrate - Optimierung empfohlen")
        else:
            print("   ✅ Gute Erfolgsrate - System arbeitet stabil")
        
        # Spezifische Empfehlungen
        char = metrics['text_statistics']['characteristics']
        
        if char['has_unicode'] > 0.3:
            print("   → Hoher Unicode-Anteil: Verbessere Text-Normalisierung")
        
        if char['avg_uppercase_ratio'] > 0.3:
            print("   → Viele Großbuchstaben: Implementiere Text-Normalisierung")
        
        length_stats = metrics['text_statistics']['length']
        if length_stats['std_dev'] > length_stats['mean']:
            print("   → Hohe Längen-Varianz: Implementiere adaptive Chunk-Größen")
        
        if length_stats['max'] > 5000:
            print("   → Sehr lange Chunks: Implementiere automatisches Splitting")
        
        # Performance-Empfehlungen
        if 'performance_statistics' in metrics:
            for test_name, stats in metrics['performance_statistics'].items():
                if stats['avg_time'] > 5.0:
                    print(f"   → {test_name} langsam: Optimiere Prompt oder Modell-Parameter")

def main():
    """Hauptfunktion"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Erweitertes Chunk-Monitoring')
    parser.add_argument('--chunks', type=int, default=20, help='Anzahl Chunks zu analysieren')
    parser.add_argument('--db-path', default='rag_store.db', help='Pfad zur RAG-Datenbank')
    
    args = parser.parse_args()
    
    monitor = EnhancedChunkMonitor(args.db_path)
    
    if not monitor.initialize_llm():
        print("❌ LLM-Initialisierung fehlgeschlagen!")
        return
    
    # Sammle Metriken
    monitor.collect_chunk_metrics(args.chunks)
    
    # Drucke umfassenden Bericht
    monitor.print_comprehensive_report()

if __name__ == "__main__":
    main()
