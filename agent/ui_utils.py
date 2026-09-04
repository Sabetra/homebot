"""UI utilities shared by different GUIs.

Contains helper functions for rendering agent traces.
Do NOT include any model or image processing logic here.
"""
from __future__ import annotations

from typing import Any
import html as _html
import json as _json


def _esc(s: Any) -> str:
    return _html.escape(str(s))


def build_trace_html(trace: Any, *, developer: bool = False) -> str:
    """Return an HTML string for a trace object.

    The trace object is expected to expose attributes used below, but
    we access them with getattr to be defensive.
    """
    parts: list[str] = []
    parts.append('<div style="font-family:Consolas,Menlo,monospace; font-size:12px;">')

    # Plan
    parts.append('<h3 style="color:#9cdcfe;">Plan</h3>')
    planner = getattr(trace, 'planner_output', None)
    if planner:
        parts.append('<pre style="white-space:pre-wrap; background:#111; padding:8px; border:1px solid #333;">' + _esc(planner) + '</pre>')
    else:
        parts.append('<i>Kein Planer-Output.</i>')

    # Reasoning
    parts.append('<h3 style="color:#9cdcfe;">Reasoning</h3>')
    reasoning = getattr(trace, 'reasoning', None)
    if reasoning:
        parts.append('<pre style="white-space:pre-wrap; background:#111; padding:8px; border:1px solid #333;">' + _esc(reasoning) + '</pre>')
    else:
        parts.append('<i>Kein Reasoning vom Planner ausgegeben.</i>')

    # Critique
    parts.append('<h3 style="color:#9cdcfe;">Critique</h3>')
    critique = getattr(trace, 'critique', None)
    if critique:
        parts.append('<pre style="white-space:pre-wrap; background:#111; padding:8px; border:1px solid #333;">' + _esc(critique) + '</pre>')
    else:
        parts.append('<i>Kein Critique vom Planner ausgegeben.</i>')

    # Heuristics
    parts.append('<h3 style="color:#9cdcfe;">Heuristik</h3>')
    trig = 'Ja' if getattr(trace, 'heuristic_triggered', False) else 'Nein'
    reason = getattr(trace, 'heuristic_reason', None)
    parts.append(f"- Ausgelöst: <b>{trig}</b>" + (f" (Grund: {_esc(reason)})" if reason else ""))

    # Tools
    parts.append('<h3 style="color:#9cdcfe;">Tools</h3>')
    planned = getattr(trace, 'planned_tools', []) or []
    ran = getattr(trace, 'ran_tools', []) or []
    summaries = getattr(trace, 'tool_summaries', []) or []
    parts.append("- Geplante Tools: " + (", ".join(_esc(t) for t in planned) if planned else "--"))
    parts.append("<br>")
    parts.append("- Ausgeführte Tools: " + (", ".join(_esc(t) for t in ran) if ran else "--"))
    parts.append("<br>")
    if summaries:
        parts.append("- Tool-Zusammenfassungen:<ul>")
        for s in summaries:
            parts.append("<li>" + _esc(s) + "</li>")
        parts.append("</ul>")
    domains = getattr(trace, 'evidence_domains', []) or []
    extras_count = getattr(trace, 'extras_count', 0)
    if domains:
        parts.append("- Evidenz-Domains: " + ", ".join(_esc(d) for d in domains))
        parts.append("<br>")
    parts.append(f"- Zusätzliche Textquellen (Extras): {extras_count}")

    # --- NEW: Detailed Tool Results ---
    tool_results = getattr(trace, 'tool_results', {}) or {}
    if tool_results and developer:
        parts.append('<h3 style="color:#9cdcfe;">Tool-Ergebnisse (Developer Mode)</h3>')
        for tool_key, tool_data in tool_results.items():
            tool_name = tool_data.get('tool', tool_key)
            query = tool_data.get('query', '')
            results_count = tool_data.get('results_count', 0)
            error = tool_data.get('error', None)
            results = tool_data.get('results', [])
            
            parts.append(f'<h4 style="color:#dcdcaa;">{_esc(tool_name)}</h4>')
            parts.append(f"<b>Query:</b> {_esc(query)}<br>")
            parts.append(f"<b>Ergebnisse:</b> {results_count}<br>")
            if error:
                parts.append(f"<b>Fehler:</b> <span style=\"color:#ff6666;\">{_esc(error)}</span><br>")
            
            if results:
                parts.append("<b>Top Ergebnisse:</b><ul>")
                for i, result in enumerate(results[:5], 1):  # Show max 5 results
                    if 'title' in result and 'url' in result:
                        # Web search result
                        title = result.get('title', '') or ''
                        url = result.get('url', '') or ''
                        snippet = result.get('snippet', '') or ''
                        parts.append(f"<li><b>{_esc(title)}</b><br>")
                        parts.append(f"<a href=\"{_esc(url)}\" style=\"color:#9cdcfe;\">{_esc(url)}</a><br>")
                        if snippet:
                            parts.append(f"<em>{_esc(snippet)}</em>")
                        parts.append("</li>")
                    elif 'content' in result:
                        # RAG search result
                        source = result.get('source', '') or ''
                        content = result.get('content', '') or ''
                        score = result.get('score', 0.0)
                        parts.append(f"<li><b>Score: {score:.3f}</b><br>")
                        if source:
                            parts.append(f"<b>Quelle:</b> {_esc(source)}<br>")
                        if content:
                            parts.append(f"<em>{_esc(content)}</em>")
                        parts.append("</li>")
                    elif 'data' in result:
                        # Generic result
                        data = result.get('data', '') or ''
                        parts.append(f"<li>{_esc(data)}</li>")
                parts.append("</ul>")
            parts.append("<br>")

    # --- New: RAG diagnostics ---
    parts.append('<h3 style="color:#9cdcfe;">RAG</h3>')
    rag_enabled = bool(getattr(trace, 'rag_enabled', False))
    parts.append(f"- Aktiviert: <b>{'Ja' if rag_enabled else 'Nein'}</b><br>")
    if rag_enabled:
        parts.append(
            f"- K: {getattr(trace,'rag_k',0)}, min_score: {getattr(trace,'rag_min_score',0.0)}<br>"
        )
        # Multi-Query
        mq_en = bool(getattr(trace, 'multiquery_enabled', False))
        parts.append(f"- Multi-Query: <b>{'Ja' if mq_en else 'Nein'}</b>")
        if mq_en:
            parts.append(
                f" (N={getattr(trace,'mq_n',0)}, K={getattr(trace,'mq_k',0)})"
            )
        parts.append("<br>")
        # Subqueries
        subqs = getattr(trace, 'subqueries', None) or []
        if subqs:
            # Show compact unless developer mode
            if developer:
                parts.append("- Teilfragen:<ul>")
                for s in subqs:
                    parts.append("<li>" + _esc(s) + "</li>")
                parts.append("</ul>")
            else:
                parts.append("- Teilfragen: " + _esc(", ".join(subqs[:6])) + (" …" if len(subqs) > 6 else ""))
        # Store stats
        stats = getattr(trace, 'rag_stats', None) or {}
        if stats:
            docs = stats.get('docs', stats.get('documents', 0))
            chunks = stats.get('chunks', 0)
            tables = stats.get('tables', 0)
            triples = stats.get('triples', 0)
            dbp = stats.get('db', '')
            dim = stats.get('dim', None)
            np_ok = bool(stats.get('numpy', False))
            parts.append(f"- Store: docs={docs}, chunks={chunks}, tables={tables}, triples={triples}")
            # Extra environment and config details
            extra_bits = []
            if dbp:
                extra_bits.append(f"DB={_esc(dbp)}")
            if dim is not None:
                extra_bits.append(f"dim={_esc(dim)}")
            extra_bits.append(f"NumPy={'Ja' if np_ok else 'Nein'}")
            if extra_bits:
                parts.append("<br>- Details: " + ", ".join(extra_bits))
            # Warnings
            if not np_ok:
                parts.append("<br><span style=\"color:#ff6666;\">Warnung: NumPy ist nicht verfügbar – Vektor-Suche/Einbettungen sind deaktiviert.</span>")
            if not chunks:
                parts.append("<br><span style=\"color:#ff6666;\">Hinweis: RAG-Store enthält keine Chunks. Importiere PDFs oder aktiviere Persistenz von Web-Ergebnissen.</span>")
        else:
            parts.append("- Store: (keine Statistiken verfügbar)")

    # --- NEW: Source Validation (2025) ---
    source_validation = getattr(trace, 'source_validation', {}) or {}
    if source_validation:
        parts.append('<h3 style="color:#9cdcfe;">🔍 Quellenvalidierung (2025)</h3>')
        initial = source_validation.get('initial_sources', 0)
        final = source_validation.get('final_sources', 0)
        iterations = source_validation.get('iterations', 0)
        web_searches = source_validation.get('web_searches', 0)
        rag_searches = source_validation.get('rag_searches', 0)
        rejected = source_validation.get('rejected_sources', 0)
        reason = source_validation.get('validation_reason', 'unknown')
        
        # Status indicator
        if final >= initial * 1.5:
            status_color = "#4ec9b0"  # Teal - significant improvement
            status_icon = "✅"
        elif final > initial:
            status_color = "#9cdcfe"  # Light blue - some improvement
            status_icon = "📈"
        elif final == initial:
            status_color = "#dcdcaa"  # Yellow - no change
            status_icon = "➖"
        else:
            status_color = "#ff6666"  # Red - sources lost
            status_icon = "⚠️"
        
        parts.append(f'<div style="background:#1e1e1e; padding:8px; border-left:3px solid {status_color};">')
        parts.append(f'{status_icon} <b>Validierung:</b> {initial} → <span style="color:{status_color};">{final}</span> Quellen<br>')
        parts.append(f'<b>Iterationen:</b> {iterations}<br>')
        parts.append(f'<b>Zusätzliche Suchen:</b> {web_searches} Web + {rag_searches} RAG<br>')
        if rejected > 0:
            parts.append(f'<b>Aussortiert:</b> <span style="color:#ff6666;">{rejected}</span> irrelevante Quellen<br>')
        parts.append(f'<b>Grund:</b> {_esc(reason)}<br>')
        parts.append('</div>')
    
    # Add developer info if available
    if source_validation and developer:
        parts.append('<details>')
        parts.append('<summary style="color:#dcdcaa; cursor:pointer;">📋 Validierungsdetails</summary>')
        parts.append('<pre style="white-space:pre-wrap; background:#111; padding:8px; border:1px solid #333;">')
        parts.append(_esc(_json.dumps(source_validation, indent=2, ensure_ascii=False)))
        parts.append('</pre>')
        parts.append('</details>')
    # Timings
    parts.append('<h3 style="color:#9cdcfe;">Timings (ms)</h3>')
    planner_ms = getattr(trace, 'planner_ms', 0)
    tools_ms = getattr(trace, 'tools_ms', 0)
    summarize_ms = getattr(trace, 'summarize_ms', 0)
    verify_ms = getattr(trace, 'verify_ms', 0)
    total_ms = (planner_ms or 0) + (tools_ms or 0) + (summarize_ms or 0) + (verify_ms or 0)
    parts.append(
        f"- Planner: {planner_ms} ms<br>- Tools: {tools_ms} ms<br>- Zusammenfassung: {summarize_ms} ms<br>- Verifikation: {verify_ms} ms<br>- Gesamt: <b>{total_ms} ms</b>"
    )

    # Token/context metrics
    parts.append('<h3 style="color:#9cdcfe;">Token/Context</h3>')
    hist_trimmed = getattr(trace, 'hist_trimmed_count', 0)
    hist_tokens = getattr(trace, 'hist_tokens_used', 0)
    budget_used = getattr(trace, 'budget_used', 0)
    draft_chars = getattr(trace, 'summarizer_draft_chars', 0)
    parts.append(
        f"- Historie gekürzt: {hist_trimmed} Einträge<br>- Tokens (Basis): {hist_tokens}<br>- Budget genutzt: {budget_used}<br>- Draft-Länge: {draft_chars} Zeichen"
    )

    # Settings
    parts.append('<h3 style="color:#9cdcfe;">Generierungs-Settings</h3>')
    if developer:
        parts.append(
            f"- Planner: temp={getattr(trace,'planner_temp',0.2)}, max_tokens={getattr(trace,'planner_max_tokens','?')}<br>"
        )
    parts.append(
        f"- Summarizer: temp={getattr(trace,'summarizer_temp',0.2)}, max_tokens={getattr(trace,'summarizer_max_tokens',1024)}<br>"
    )
    parts.append(
        f"- Verifier: temp={getattr(trace,'verifier_temp',0.0)}, max_tokens={getattr(trace,'verifier_max_tokens',1024)}"
    )

    # Verification
    parts.append('<h3 style="color:#9cdcfe;">Verifikation</h3>')
    changed = getattr(trace, 'verifier_changed', False)
    delta = getattr(trace, 'verifier_delta_chars', 0)
    ratio = getattr(trace, 'verifier_changed_ratio', 0.0)
    parts.append(f"- Geändert: <b>{'Ja' if changed else 'Nein'}</b><br>- Delta: {delta} Zeichen<br>- Verhältnis: {ratio:.3f}")

    # Developer extras: compact JSON snapshot
    if developer:
        try:
            snap = {
                'planned_tools': planned,
                'ran_tools': ran,
                'tool_summaries': summaries,
                'evidence_domains': domains,
                'metrics': {
                    'planner_ms': planner_ms,
                    'tools_ms': tools_ms,
                    'summarize_ms': summarize_ms,
                    'verify_ms': verify_ms,
                    'hist_trimmed_count': hist_trimmed,
                    'hist_tokens_used': hist_tokens,
                    'budget_used': budget_used,
                    'summarizer_draft_chars': draft_chars,
                },
                # Include RAG snapshot for troubleshooting
                'rag': {
                    'enabled': rag_enabled,
                    'rag_k': getattr(trace, 'rag_k', None),
                    'rag_min_score': getattr(trace, 'rag_min_score', None),
                    'multiquery_enabled': getattr(trace, 'multiquery_enabled', None),
                    'mq_n': getattr(trace, 'mq_n', None),
                    'mq_k': getattr(trace, 'mq_k', None),
                    'subqueries': getattr(trace, 'subqueries', None),
                    'stats': getattr(trace, 'rag_stats', None),
                }
            }
            pretty = _json.dumps(snap, ensure_ascii=False, indent=2)
            parts.append('<h3 style="color:#9cdcfe;">Advanced</h3>')
            parts.append('<pre style="white-space:pre-wrap; background:#111; padding:8px; border:1px solid #333;">' + _esc(pretty) + '</pre>')
        except Exception:
            pass

    parts.append('</div>')
    return "".join(parts)


def build_trace_text(trace: Any, *, developer: bool = False) -> str:
    """Return a plain text representation of the trace for clipboard use."""
    lines: list[str] = []
    def add(s: str = "") -> None:
        lines.append(s)

    add("=== PLAN ===")
    planner = getattr(trace, 'planner_output', None)
    add(planner or "(kein Plan)")
    add("")

    add("=== HEURISTIK ===")
    add(f"Ausgelöst: {'Ja' if getattr(trace,'heuristic_triggered',False) else 'Nein'}")
    reason = getattr(trace, 'heuristic_reason', None)
    if reason:
        add(f"Grund: {reason}")
    add("")

    add("=== TOOLS ===")
    add("Geplante Tools: " + ", ".join(getattr(trace,'planned_tools',[]) or []))
    ran = getattr(trace, 'ran_tools', []) or []
    add("Ausgeführte Tools: " + (", ".join(ran) if ran else "--"))
    summaries = getattr(trace, 'tool_summaries', []) or []
    if summaries:
        add("Tool-Zusammenfassungen:")
        for s in summaries:
            add(" - " + str(s))
    domains = getattr(trace, 'evidence_domains', []) or []
    if domains:
        add("Evidenz-Domains: " + ", ".join(str(d) for d in domains))
    add(f"Extras: {getattr(trace,'extras_count',0)}")
    add("")

    # --- NEW: Detailed Tool Results ---
    tool_results = getattr(trace, 'tool_results', {}) or {}
    if tool_results and developer:
        add("=== TOOL-ERGEBNISSE (Developer Mode) ===")
        for tool_key, tool_data in tool_results.items():
            tool_name = tool_data.get('tool', tool_key)
            query = tool_data.get('query', '')
            results_count = tool_data.get('results_count', 0)
            error = tool_data.get('error', None)
            results = tool_data.get('results', [])
            
            add(f"--- {tool_name} ---")
            add(f"Query: {query}")
            add(f"Ergebnisse: {results_count}")
            if error:
                add(f"Fehler: {error}")
            
            if results:
                add("Top Ergebnisse:")
                for i, result in enumerate(results[:5], 1):  # Show max 5 results
                    if 'title' in result and 'url' in result:
                        # Web search result
                        title = result.get('title', '') or ''
                        url = result.get('url', '') or ''
                        snippet = result.get('snippet', '') or ''
                        add(f"  {i}. {title}")
                        add(f"     URL: {url}")
                        if snippet:
                            add(f"     {snippet}")
                    elif 'content' in result:
                        # RAG search result
                        source = result.get('source', '') or ''
                        content = result.get('content', '') or ''
                        score = result.get('score', 0.0)
                        add(f"  {i}. Score: {score:.3f}")
                        if source:
                            add(f"     Quelle: {source}")
                        if content:
                            add(f"     {content}")
                    elif 'data' in result:
                        # Generic result
                        data = result.get('data', '') or ''
                        add(f"  {i}. {data}")
            add("")

    # RAG diagnostics
    add("=== RAG ===")
    rag_enabled = bool(getattr(trace, 'rag_enabled', False))
    add(f"Aktiviert: {'Ja' if rag_enabled else 'Nein'}")
    if rag_enabled:
        add(f"K={getattr(trace,'rag_k',0)}, min_score={getattr(trace,'rag_min_score',0.0)}")
        mq_en = bool(getattr(trace, 'multiquery_enabled', False))
        add(f"Multi-Query: {'Ja' if mq_en else 'Nein'}" + (f" (N={getattr(trace,'mq_n',0)}, K={getattr(trace,'mq_k',0)})" if mq_en else ""))
        subqs = getattr(trace, 'subqueries', None) or []
        if subqs:
            add("Teilfragen:")
            for s in (subqs if developer else subqs[:6]):
                add(" - " + str(s))
            if not developer and len(subqs) > 6:
                add(" - …")
        stats = getattr(trace, 'rag_stats', None) or {}
        if stats:
            docs = stats.get('docs', stats.get('documents', 0))
            chunks = stats.get('chunks', 0)
            tables = stats.get('tables', 0)
            triples = stats.get('triples', 0)
            dbp = stats.get('db', '')
            dim = stats.get('dim', None)
            np_ok = bool(stats.get('numpy', False))
            add(f"Store: docs={docs}, chunks={chunks}, tables={tables}, triples={triples}")
            extras = []
            if dbp:
                extras.append(f"DB={dbp}")
            if dim is not None:
                extras.append(f"dim={dim}")
            extras.append(f"NumPy={'Ja' if np_ok else 'Nein'}")
            if extras:
                add("Details: " + ", ".join(extras))
            if not np_ok:
                add("Warnung: NumPy ist nicht verfügbar – Vektor-Suche/Einbettungen sind deaktiviert.")
            if not chunks:
                add("Hinweis: RAG-Store enthält keine Chunks. Importiere PDFs oder aktiviere Persistenz von Web-Ergebnissen.")
        else:
            add("Store: (keine Statistiken verfügbar)")
    add("")

    add("=== TIMINGS (ms) ===")
    planner_ms = getattr(trace, 'planner_ms', 0)
    tools_ms = getattr(trace, 'tools_ms', 0)
    summarize_ms = getattr(trace, 'summarize_ms', 0)
    verify_ms = getattr(trace, 'verify_ms', 0)
    total_ms = (planner_ms or 0) + (tools_ms or 0) + (summarize_ms or 0) + (verify_ms or 0)
    add(f"Planner: {planner_ms}")
    add(f"Tools: {tools_ms}")
    add(f"Summarizer: {summarize_ms}")
    add(f"Verifier: {verify_ms}")
    add(f"Gesamt: {total_ms}")
    add("")

    add("=== TOKEN/CONTEXT ===")
    add(f"Hist gekürzt: {getattr(trace,'hist_trimmed_count',0)}")
    add(f"Hist Tokens: {getattr(trace,'hist_tokens_used',0)}")
    add(f"Budget genutzt: {getattr(trace,'budget_used',0)}")
    add(f"Draft-Länge: {getattr(trace,'summarizer_draft_chars',0)}")
    add("")

    add("=== SETTINGS ===")
    if developer:
        add(f"Planner: temp={getattr(trace,'planner_temp',0.2)}, max_tokens={getattr(trace,'planner_max_tokens','?')}")
    add(f"Summarizer: temp={getattr(trace,'summarizer_temp',0.2)}, max_tokens={getattr(trace,'summarizer_max_tokens',1024)}")
    add(f"Verifier: temp={getattr(trace,'verifier_temp',0.0)}, max_tokens={getattr(trace,'verifier_max_tokens',1024)}")
    add("")

    add("=== VERIFIKATION ===")
    add(f"Geändert: {'Ja' if getattr(trace,'verifier_changed',False) else 'Nein'}")
    add(f"Delta: {getattr(trace,'verifier_delta_chars',0)}")
    add(f"Verhältnis: {getattr(trace,'verifier_changed_ratio',0.0):.3f}")

    return "\n".join(lines)


__all__ = [
    "build_trace_html",
    "build_trace_text",
]
