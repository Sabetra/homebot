"""
License-Frische- & Policy-Check (Gate)
======================================

Prueft gegen die Produktiv-Venv:
  1. Frische: `LICENSES.md` entspricht byte-exakt dem Ergebnis des
     Generators (deterministisch; s. scripts/generate_licenses.py).
  2. Policy: keine UNKNOWN/needs-review-Lizenzen in der Runtime-Scope.
     Dev-only-Pakete werden nur gewarnt (sie werden nie verteilt).

Exit-Codes:
  0  ok (ggf. mit Warnungen ohne --strict)
  1  Stale Inventory oder (mit --strict) unklare Runtime-Lizenzen

Usage:
    python scripts/check_licenses.py             # Frische-Gate
    python scripts/check_licenses.py --strict    # + Policy-Gate (CI/Hook)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_licenses as gl  # noqa: E402


def run_check(
    venv: Path,
    requirements: Path,
    dev_requirements: Path,
    out_path: Path,
    strict: bool = False,
) -> tuple[bool, list[str]]:
    """Fuehrt Frische- und Policy-Check aus; liefert (ok, Meldungen)."""
    messages: list[str] = []
    ok = True

    # 1) Frische: erwarteter vs. gespeicherter LICENSES.md-Inhalt
    expected = gl.generate(venv, requirements, dev_requirements)
    actual = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
    if actual != expected:
        ok = False
        messages.append(
            "LICENSES.md ist veraltet (weicht vom Generator-Stand ab). "
            "Aktion: python scripts/generate_licenses.py"
        )

    # 2) Policy: unklare Lizenzen in der Runtime-Scope
    pkgs = gl.collect_packages(venv)
    gl.assign_scopes(
        pkgs,
        gl.parse_requirements(requirements),
        gl.parse_requirements(dev_requirements),
    )
    for p in pkgs:
        if p.classification not in ("unknown", "needs-review"):
            continue
        if p.scope == "dev-only":
            messages.append(
                f"Dev-only (kein Verteilungsrisiko): {p.name} {p.version} "
                f"-> {p.license_label or 'keine Metadaten'}"
            )
            continue
        messages.append(
            f"RUNTIME-Paket mit unklarer Lizenz: {p.name} {p.version} "
            f"-> {p.license_label or 'keine Metadaten'}. "
            "Aktion: Lizenz pruefen und ggf. MANUAL_OVERRIDES in "
            "scripts/generate_licenses.py ergaenzen (mit Quelle+Datum)."
        )
        if strict:
            ok = False

    return ok, messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="License-Frische- & Policy-Check (lokal, stdlib)."
    )
    parser.add_argument("--venv", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=gl.DEFAULT_OUT)
    parser.add_argument("--requirements", type=Path, default=gl.DEFAULT_REQUIREMENTS)
    parser.add_argument(
        "--dev-requirements", type=Path, default=gl.DEFAULT_DEV_REQUIREMENTS
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="unklare Runtime-Lizenzen als Fehler werten (CI/Pre-Commit)",
    )
    args = parser.parse_args(argv)

    ok, messages = run_check(
        gl.resolve_venv(args.venv),
        args.requirements,
        args.dev_requirements,
        args.out,
        args.strict,
    )
    for m in messages:
        print(f"  - {m}")
    if ok and not messages:
        print("license-check: OK (Frische + Policy, keine offenen Punkte)")
    elif ok:
        print("license-check: OK mit Warnungen (mit --strict hartes Gate)")
    else:
        print("license-check: FEHLGESCHLAGEN (s. Meldungen oben)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())