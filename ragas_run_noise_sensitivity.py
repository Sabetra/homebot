"""Führt nur NoiseSensitivity-Metrik aus (nutzt gespeicherte Pipeline-Outputs)."""
import os, sys, json, time, logging

sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("noise_eval")

def main():
    # 1. Lade Pipeline-Outputs
    outputs_path = os.path.join("ragas_results", "pipeline_outputs.json")
    if not os.path.exists(outputs_path):
        print("Keine pipeline_outputs.json gefunden. Zuerst ragas_sota_evaluation.py ausführen.")
        sys.exit(1)
    
    with open(outputs_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    samples_data = data["samples"]
    print(f"→ {len(samples_data)} Samples geladen\n")
    
    # 2. LLM laden
    print("Lade LLM...")
    from scripts.model_loader import DEFAULT_MODEL, get_model_loader
    model_loader = get_model_loader()
    if model_loader.llm is None:
        model_loader.load_model_by_config(DEFAULT_MODEL)
    print(f"→ LLM: {model_loader.current_model_id}\n")
    
    # 3. RAGAS Setup
    from ragas import evaluate
    from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics._noise_sensitivity import NoiseSensitivity
    from ragas.run_config import RunConfig
    
    # LLM Wrapper
    from ragas_sota_evaluation import LocalLlamaLLM
    llm_wrapper = LocalLlamaLLM(temperature=0.1, max_tokens=2048)
    ragas_llm = LangchainLLMWrapper(llm_wrapper)
    
    # Samples
    samples = []
    for s in samples_data:
        samples.append(SingleTurnSample(
            user_input=s["question"],
            response=s["answer"],
            retrieved_contexts=s["contexts"] if s["contexts"] else ["(kein Kontext)"],
            reference=s["reference"],
        ))
    
    eval_dataset = EvaluationDataset(samples=samples)
    noise_sensitivity = NoiseSensitivity(llm=ragas_llm)
    run_config = RunConfig(timeout=1800, max_retries=2, max_workers=1)
    
    # 4. Evaluate
    print("Starte NoiseSensitivity Evaluation...")
    t0 = time.time()
    result = evaluate(
        dataset=eval_dataset,
        metrics=[noise_sensitivity],
        llm=ragas_llm,
        show_progress=True,
        raise_exceptions=False,
        batch_size=1,
        run_config=run_config,
    )
    duration = time.time() - t0

    result_dict = getattr(result, "_repr_dict", None)
    if not isinstance(result_dict, dict):
        to_dict_fn = getattr(result, "to_dict", None)
        if callable(to_dict_fn):
            maybe_dict = to_dict_fn()
            result_dict = maybe_dict if isinstance(maybe_dict, dict) else {}
        else:
            result_dict = {}
    if not result_dict:
        scores_attr = getattr(result, "scores", None)
        if isinstance(scores_attr, list) and scores_attr:
            merged = {}
            for item in scores_attr:
                if isinstance(item, dict):
                    merged.update(item)
            result_dict = merged

    score = float(result_dict.get("noise_sensitivity_relevant", result_dict.get("noise_sensitivity", -1)))
    print(f"\n{'='*60}")
    print(f"  NoiseSensitivity Score: {score:.4f}")
    print(f"  Dauer: {duration:.1f}s")
    print(f"  SOTA-Schwelle: 0.80")
    if score >= 0.80:
        print(f"  Status: 🏆 SOTA")
    elif score >= 0.65:
        print(f"  Status: ✅ GUT")
    elif score >= 0.45:
        print(f"  Status: ⚠️ AKZEPTABEL")
    else:
        print(f"  Status: ❌ VERBESSERUNG NÖTIG")
    print(f"{'='*60}")
    
    # Alle Scores ausgeben
    print(f"\nAlle Scores: {result_dict}")
    
    # In Ergebnis-JSON ergänzen
    results_path = os.path.join("ragas_results", "ragas_evaluation_results.json")
    if os.path.exists(results_path):
        with open(results_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        for k, v in result_dict.items():
            existing["scores"][k] = v
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        print(f"→ Ergebnis in {results_path} ergänzt")

if __name__ == "__main__":
    main()
