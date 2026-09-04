"""
SOTA Code Executor Engine
=========================
Production-grade Python code execution with:

1. AST-based security analysis (not regex)
2. Persistent sessions (subprocess with JSON-line protocol)
3. LLM-powered auto-retry on errors (Reflexion, Shinn et al. 2023)
4. Structured error parsing with line-level context
5. Multi-plot (matplotlib + Plotly) and file output support
6. Whitelisted pip install with auto-detection
7. Jupyter-like auto-display of last expression
8. Research-Augmented Code Fixing (Web Search + RAG on retry escalation)

References:
- OpenAI Code Interpreter architecture
- Shinn et al. 2023 (Reflexion)
- E2B Code Interpreter SDK design
- Schick et al. 2023 (Toolformer)
- Paranjape et al. 2023 (ART: Automatic multi-step Reasoning and Tool-use)
"""

import ast
import sys
import os
import json
import time
import subprocess
import tempfile
import textwrap
import threading
import base64
import re
import logging
import atexit
import uuid
from pathlib import Path
from typing import Callable, Dict, Any, List, Optional, Tuple, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 1. STRUCTURED ERROR
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class StructuredError:
    """Parsed error from code execution with line-level context."""
    error_type: str
    message: str
    traceback_str: str
    line: Optional[int] = None
    code_context: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StructuredError":
        return cls(
            error_type=d.get("type", "Unknown"),
            message=d.get("message", ""),
            traceback_str=d.get("traceback", ""),
            line=d.get("line"),
            code_context=d.get("code_context"),
        )

    def to_llm_prompt(self, code: str) -> str:
        """Format for LLM code-fix request."""
        parts = [
            f"FEHLER-TYP: {self.error_type}",
            f"NACHRICHT: {self.message}",
        ]
        if self.line is not None:
            parts.append(f"ZEILE: {self.line}")
        if self.code_context:
            parts.append(f"FEHLER-KONTEXT: {self.code_context}")
        parts.append(f"\nTRACEBACK:\n{self.traceback_str}")
        parts.append(f"\nCODE:\n```python\n{code}\n```")
        return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════
# 2. EXECUTION RESULT
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionResult:
    """Rich result from code execution -- single source of truth for all outputs."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: Optional[StructuredError] = None
    plots: List[Dict[str, Any]] = field(default_factory=list)   # [{path, base64, format}]
    files: List[Dict[str, Any]] = field(default_factory=list)   # [{path, name, size}]
    variables: Dict[str, str] = field(default_factory=dict)     # {name: type_str}
    execution_time: float = 0.0
    retries_used: int = 0
    code_versions: List[str] = field(default_factory=list)      # All code versions (original + fixes)
    auto_installed: List[str] = field(default_factory=list)     # Packages auto-installed
    # ── Detached mode fields ──
    detached: bool = False                                       # True if launched as background process
    pid: Optional[int] = None                                    # PID of detached process
    script_path: Optional[str] = None                            # Path to the detached script file

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for agent_toolkit compatibility."""
        d: Dict[str, Any] = {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "execution_time": self.execution_time,
            "retries_used": self.retries_used,
        }
        # ── Detached mode ──
        if self.detached:
            d["detached"] = True
            d["pid"] = self.pid
            d["script_path"] = self.script_path
        if self.error:
            d["error"] = self.error.message
            d["error_type"] = self.error.error_type
            d["error_class"] = self.error.error_type.lower()
            d["error_traceback"] = self.error.traceback_str
            if self.error.line is not None:
                d["error_line"] = self.error.line
        if self.plots:
            d["plots"] = self.plots
            # Backwards compatibility -- first plot as "plot" key
            d["plot"] = self.plots[0].get("base64", "")
            d["plot_base64"] = self.plots[0].get("base64", "")
            d["plot_format"] = self.plots[0].get("format", "png")
        if self.files:
            d["files"] = self.files
        if self.variables:
            d["variables"] = self.variables
        if self.auto_installed:
            d["auto_installed"] = self.auto_installed
        d["message"] = self._build_message()
        return d

    def _build_message(self) -> str:
        parts = [f"Code ausgeführt ({self.execution_time:.4f}s)"]
        if self.retries_used > 0:
            parts.append(f"nach {self.retries_used} Auto-Fix(es)")
        if self.auto_installed:
            parts.append(f"auto-installiert: {', '.join(self.auto_installed)}")
        if self.plots:
            parts.append(f"{len(self.plots)} Plot(s) erstellt")
        if self.files:
            parts.append(f"{len(self.files)} Datei(en) erzeugt")
        if not self.success and self.error:
            parts.append(f"Fehler: {self.error.error_type}")
        return " -- ".join(parts)


# ══════════════════════════════════════════════════════════════════════════
# 3. AST-BASED SECURITY ANALYZER
# ══════════════════════════════════════════════════════════════════════════

class SecurityViolation(Exception):
    """Raised when code violates security policy."""
    pass


class CodeSecurityAnalyzer(ast.NodeVisitor):
    """AST-based code security analysis.

    Analyzes the actual parse tree instead of regex patterns, avoiding:
    - False positives from comments/strings containing blocked words
    - False negatives from obfuscation (e.g. 'sub' + 'process')
    """

    BLOCKED_MODULES = {
        # Process & System
        "subprocess", "multiprocessing", "shutil", "ctypes", "signal",
        # Network
        "socket", "requests", "urllib", "http", "httplib",
        "smtplib", "ftplib", "telnetlib", "paramiko", "ssl",
        "xmlrpc", "asyncio",
        # Code execution & introspection
        "code", "codeop", "compileall", "importlib",
        # System interaction
        "pty", "resource", "sysconfig", "winreg",
    }

    BLOCKED_OS_ATTRS = {
        "system", "popen", "exec", "execl", "execle", "execlp",
        "execv", "execve", "execvp", "execvpe",
        "fork", "forkpty", "kill", "killpg",
        "spawn", "spawnl", "spawnle", "spawnlp", "spawnv",
        "startfile", "remove", "unlink", "rmdir", "removedirs",
    }

    BLOCKED_BUILTINS = {
        "eval", "exec", "compile", "__import__",
        "breakpoint", "exit", "quit",
    }

    _REASONS = {
        "subprocess": "Prozess-Erstellung",
        "multiprocessing": "Prozess-Erstellung",
        "shutil": "Dateisystem-Manipulation",
        "ctypes": "Nativer Code-Zugriff",
        "socket": "Netzwerk-Zugriff",
        "requests": "HTTP-Requests",
        "urllib": "HTTP-Requests",
        "http": "HTTP-Server/Client",
        "smtplib": "E-Mail-Versand",
        "ftplib": "FTP-Zugriff",
        "paramiko": "SSH-Zugriff",
        "ssl": "SSL-Verbindungen",
        "importlib": "Dynamische Imports",
        "winreg": "Windows-Registry",
        "asyncio": "Netzwerk/Async-Zugriff",
        "signal": "Systemsignal-Manipulation",
    }

    def __init__(self):
        self.violations: List[str] = []

    def analyze(self, code: str) -> List[str]:
        """Analyze code and return list of violations (empty = safe)."""
        self.violations = []
        try:
            tree = ast.parse(code)
            self.visit(tree)
        except SyntaxError:
            # Syntax errors are OK -- they'll be caught at execution time
            pass
        return self.violations

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in self.BLOCKED_MODULES:
                reason = self._REASONS.get(top, "Sicherheitsrisiko")
                self.violations.append(
                    f"Import blockiert: '{alias.name}' ({reason})"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top = node.module.split(".")[0]
            if top in self.BLOCKED_MODULES:
                reason = self._REASONS.get(top, "Sicherheitsrisiko")
                self.violations.append(
                    f"Import blockiert: 'from {node.module}' ({reason})"
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check for blocked builtins
        if isinstance(node.func, ast.Name):
            if node.func.id in self.BLOCKED_BUILTINS:
                self.violations.append(
                    f"Aufruf blockiert: '{node.func.id}()' (Code-Injection-Risiko)"
                )
            # getattr can bypass security
            if node.func.id == "getattr":
                self.violations.append(
                    "getattr() blockiert (kann für Sicherheitsumgehung genutzt werden)"
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Block os.system, os.popen etc.
        if isinstance(node.value, ast.Name) and node.value.id == "os":
            if node.attr in self.BLOCKED_OS_ATTRS:
                self.violations.append(
                    f"Zugriff blockiert: 'os.{node.attr}' (Prozess/Dateisystem-Operation)"
                )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name in ("__import__", "__builtins__"):
            self.violations.append(
                f"Funktionsdefinition blockiert: '{node.name}' (Builtins-Manipulation)"
            )
        self.generic_visit(node)


# ══════════════════════════════════════════════════════════════════════════
# 4. SUBPROCESS WORKER SCRIPT (embedded)
# ══════════════════════════════════════════════════════════════════════════

_WORKER_SCRIPT = r'''
"""Persistent Python execution worker.

Protocol: reads JSON lines from stdin, writes JSON responses to stdout.
User code stdout/stderr are captured separately via StringIO redirection.
"""

import sys
import os
import io
import json
import base64
import traceback
import re as _re

# Save real stdout/stdin BEFORE anything can overwrite them
_REAL_STDIN = sys.stdin
_REAL_STDOUT = sys.stdout

# Setup matplotlib backend
try:
    import matplotlib
    matplotlib.use("Agg")
except ImportError:
    matplotlib = None

# Persistent namespace
_namespace = {"__builtins__": __builtins__}

# Sandbox directory (passed as argv[1])
_sandbox_dir = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(_sandbox_dir, exist_ok=True)


def _auto_display_last_expr(code):
    """Transform code so that the last expression is auto-displayed (Jupyter-like).

    If the last statement is a bare expression (e.g. ``df.head()``),
    assign it to ``__result__`` and print it if non-None.
    """
    try:
        tree = __import__("ast").parse(code)
    except SyntaxError:
        return code  # let exec() raise it with proper traceback

    ast_mod = __import__("ast")
    if tree.body and isinstance(tree.body[-1], ast_mod.Expr):
        last_expr = tree.body.pop()
        # __result__ = <last_expr>
        assign = ast_mod.Assign(
            targets=[ast_mod.Name(id="__result__", ctx=ast_mod.Store())],
            value=last_expr.value,
            lineno=last_expr.lineno,
            col_offset=last_expr.col_offset,
        )
        assign.end_lineno = getattr(last_expr, "end_lineno", last_expr.lineno)
        assign.end_col_offset = getattr(last_expr, "end_col_offset", last_expr.col_offset)

        display = ast_mod.parse(
            "if __result__ is not None:\n    print(repr(__result__))"
        ).body[0]
        tree.body.append(assign)
        tree.body.append(display)
        ast_mod.fix_missing_locations(tree)
        return compile(tree, "<code>", "exec")
    return code


def execute_code(code: str) -> dict:
    """Execute code in persistent namespace; capture all outputs."""
    # Snapshot sandbox files
    pre_files = set()
    if os.path.exists(_sandbox_dir):
        pre_files = set(os.listdir(_sandbox_dir))

    # Redirect stdout/stderr for user code
    old_stdout, old_stderr = sys.stdout, sys.stderr
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    sys.stdout = captured_out
    sys.stderr = captured_err

    # Inject sandbox dir into namespace
    _namespace["__sandbox_dir__"] = _sandbox_dir
    _namespace["__output_dir__"] = _sandbox_dir

    success = True
    error_info = None

    try:
        transformed = _auto_display_last_expr(code)
        if isinstance(transformed, str):
            compiled = compile(transformed, "<code>", "exec")
        else:
            compiled = transformed
        exec(compiled, _namespace)
    except Exception as e:
        success = False
        tb = traceback.format_exc()
        error_info = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": tb,
        }
        line_match = _re.search(r'File "<code>", line (\d+)', tb)
        if line_match:
            error_info["line"] = int(line_match.group(1))
            lines = code.split("\n")
            idx = error_info["line"] - 1
            if 0 <= idx < len(lines):
                error_info["code_context"] = lines[idx].strip()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # ── Capture matplotlib plots ──
    plots = []
    try:
        import matplotlib.pyplot as plt
        for fig_num in plt.get_fignums():
            fig = plt.figure(fig_num)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            plot_path = os.path.join(_sandbox_dir, f"plot_{fig_num}.png")
            with open(plot_path, "wb") as f:
                f.write(buf.getvalue())
            plots.append({
                "path": plot_path,
                "base64": base64.b64encode(buf.getvalue()).decode("utf-8"),
                "format": "png",
            })
        plt.close("all")
    except ImportError:
        captured_err.write("\n[Plot capture skipped: matplotlib not installed]\n")
    except Exception as pe:
        captured_err.write(f"\n[Plot capture error: {pe}]")

    # ── Capture Plotly figures from namespace ──
    try:
        import plotly.graph_objects as _go
        for vname, vobj in list(_namespace.items()):
            if isinstance(vobj, _go.Figure):
                html_path = os.path.join(_sandbox_dir, f"plotly_{vname}.html")
                vobj.write_html(html_path)
                plots.append({
                    "path": html_path,
                    "format": "html",
                    "base64": "",  # HTML files use path instead
                })
    except ImportError:
        captured_err.write("\n[Plotly capture skipped: plotly not installed]\n")
    except Exception as plotly_exc:
        captured_err.write(f"\n[Plotly capture error: {plotly_exc}]")

    # ── Detect new files in sandbox ──
    new_files = []
    if os.path.exists(_sandbox_dir):
        post_files = set(os.listdir(_sandbox_dir))
        created = post_files - pre_files
        for fname in sorted(created):
            fpath = os.path.join(_sandbox_dir, fname)
            if os.path.isfile(fpath) and not fname.startswith("plot_") and not fname.startswith("plotly_"):
                new_files.append({
                    "path": fpath,
                    "name": fname,
                    "size": os.path.getsize(fpath),
                })

    # ── Collect user-defined variables (SOTA: Values + Types) ──
    # Erfasse sowohl Typen als auch Werte. Wenn stdout leer ist (LLM hat
    # print() vergessen), werden die Werte als auto_display surfacet.
    # Root Cause Fix für H04: Code berechnet Ergebnis korrekt, aber ohne
    # print() geht der Wert verloren. Die Daten existieren in _namespace.
    user_vars = {}
    user_var_values = {}  # Für auto_display bei leerem stdout
    _MAX_VAR_REPR_LEN = 2000  # Limit für repr() pro Variable
    for k, v in _namespace.items():
        if not k.startswith("_") and k != "__builtins__":
            try:
                user_vars[k] = type(v).__name__
                # Werte nur für serialisierbare, nicht-zu-große Typen erfassen
                if isinstance(v, (str, int, float, bool, complex, list, tuple, dict, set, frozenset)):
                    r = repr(v)
                    if len(r) <= _MAX_VAR_REPR_LEN:
                        user_var_values[k] = r
                elif hasattr(v, '__len__') and not callable(v):
                    # DataFrames, Arrays etc. -- Kurzform
                    try:
                        r = repr(v)
                        if len(r) <= _MAX_VAR_REPR_LEN:
                            user_var_values[k] = r
                        else:
                            user_var_values[k] = f"<{type(v).__name__} len={len(v)}>"
                    except Exception as repr_exc:
                        user_var_values[k] = f"<{type(v).__name__}>"
            except Exception as var_exc:
                user_vars[k] = "unknown"

    # ── Auto-Display: Stdout leer + Variablen vorhanden → Werte surfacen ──
    # SOTA: Jupyter-artiges Verhalten -- wenn der Code kein Output produziert
    # aber Variablen definiert, zeige die zuletzt definierten Werte.
    stdout_val = captured_out.getvalue()
    if success and not stdout_val.strip() and user_var_values:
        # Bestimme die "interessanten" Variablen (keine Funktionen/Module/Klassen)
        display_vars = {
            k: v for k, v in user_var_values.items()
            if user_vars.get(k) not in ("function", "module", "type", "classmethod", "staticmethod")
        }
        if display_vars:
            auto_lines = []
            for k, v in display_vars.items():
                auto_lines.append(f"{k} = {v}")
            auto_display = "\n".join(auto_lines)
            # Schreibe in stdout, damit der Agent das Ergebnis sieht
            stdout_val = f"[Auto-Display: Variablen-Werte]\n{auto_display}\n"

    return {
        "success": success,
        "stdout": stdout_val,
        "stderr": captured_err.getvalue(),
        "error": error_info,
        "plots": plots,
        "files": new_files,
        "variables": user_vars,
    }


# ══════════════════════════════════════════════════════════════
# Main loop: JSON-line protocol
# ══════════════════════════════════════════════════════════════
for line in _REAL_STDIN:
    line = line.strip()
    if not line:
        continue
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        _REAL_STDOUT.write(json.dumps({
            "success": False,
            "error": {"type": "JSONDecodeError", "message": "Invalid request"},
            "stdout": "", "stderr": "", "plots": [], "files": [], "variables": {},
        }) + "\n")
        _REAL_STDOUT.flush()
        continue

    cmd = request.get("cmd", "execute")

    if cmd == "execute":
        result = execute_code(request.get("code", ""))
        _REAL_STDOUT.write(json.dumps(result, ensure_ascii=False) + "\n")
        _REAL_STDOUT.flush()
    elif cmd == "reset":
        _namespace.clear()
        _namespace["__builtins__"] = __builtins__
        _REAL_STDOUT.write(json.dumps({
            "success": True, "message": "Session reset",
            "stdout": "", "stderr": "", "error": None,
            "plots": [], "files": [], "variables": {},
        }) + "\n")
        _REAL_STDOUT.flush()
    elif cmd == "variables":
        user_vars = {
            k: type(v).__name__
            for k, v in _namespace.items()
            if not k.startswith("_") and k != "__builtins__"
        }
        _REAL_STDOUT.write(json.dumps({
            "success": True, "variables": user_vars,
            "stdout": "", "stderr": "", "error": None,
            "plots": [], "files": [],
        }) + "\n")
        _REAL_STDOUT.flush()
    elif cmd == "quit":
        break
    else:
        _REAL_STDOUT.write(json.dumps({
            "success": False,
            "error": {"type": "UnknownCommand", "message": f"Unknown: {cmd}"},
            "stdout": "", "stderr": "", "plots": [], "files": [], "variables": {},
        }) + "\n")
        _REAL_STDOUT.flush()
'''


# ══════════════════════════════════════════════════════════════════════════
# 5. PERSISTENT SESSION MANAGER
# ══════════════════════════════════════════════════════════════════════════

class PersistentSession:
    """Manages a persistent Python subprocess with state across calls.

    Communication: JSON-line protocol over stdin/stdout.
    If the subprocess dies or times out, it is automatically restarted
    (session state is lost, user is informed).
    """

    def __init__(self, sandbox_dir: str, python_executable: Optional[str] = None):
        self.sandbox_dir = sandbox_dir
        self.python_exe = python_executable or sys.executable
        self._process: Optional[subprocess.Popen] = None
        self._worker_file: Optional[str] = None
        self._lock = threading.Lock()
        self._started = False
        os.makedirs(sandbox_dir, exist_ok=True)

    def _ensure_started(self) -> None:
        """Lazy-start the worker subprocess."""
        if self._started and self._process and self._process.poll() is None:
            return  # Already running

        # Write worker script to temp file
        if self._worker_file and os.path.exists(self._worker_file):
            try:
                os.unlink(self._worker_file)
            except OSError as exc:
                logger.debug(f"Konnte alte Worker-Datei nicht loeschen ({self._worker_file}): {exc}")

        fd, self._worker_file = tempfile.mkstemp(suffix="_code_worker.py", prefix="ce_")
        os.close(fd)
        with open(self._worker_file, "w", encoding="utf-8") as f:
            f.write(_WORKER_SCRIPT)

        self._process = subprocess.Popen(
            [self.python_exe, self._worker_file, self.sandbox_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # Line-buffered
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self._started = True
        logger.info(f"[PersistentSession] Worker started (PID={self._process.pid})")

    def execute(self, code: str, timeout: int = 30) -> Dict[str, Any]:
        """Send code to the persistent worker and return the result."""
        with self._lock:
            self._ensure_started()
            assert self._process is not None
            assert self._process.stdin is not None
            assert self._process.stdout is not None

            request = json.dumps({"cmd": "execute", "code": code}, ensure_ascii=False)
            try:
                self._process.stdin.write(request + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                logger.warning(f"[PersistentSession] Write failed: {e} -- restarting")
                self._kill()
                return self._session_reset_error("Schreibfehler -- Session zurückgesetzt")

            # Read response with timeout using a thread
            result_container: List[Optional[str]] = [None]
            read_error: List[Optional[str]] = [None]

            def _read():
                try:
                    line = self._process.stdout.readline()  # type: ignore[union-attr]
                    result_container[0] = line
                except Exception as e:
                    read_error[0] = str(e)

            reader = threading.Thread(target=_read, daemon=True)
            reader.start()
            reader.join(timeout=timeout + 5)  # Extra buffer for cleanup

            if reader.is_alive():
                # Timeout -- kill subprocess (session state lost)
                logger.warning(f"[PersistentSession] Timeout after {timeout}s -- killing worker")
                self._kill()
                return self._session_reset_error(
                    f"Code-Ausführung Timeout nach {timeout}s -- Session zurückgesetzt"
                )

            if read_error[0]:
                logger.warning(f"[PersistentSession] Read error: {read_error[0]}")
                self._kill()
                return self._session_reset_error("Lesefehler -- Session zurückgesetzt")

            response_line = result_container[0]
            if not response_line or not response_line.strip():
                # Process may have crashed
                if self._process.poll() is not None:
                    logger.warning("[PersistentSession] Worker crashed -- restarting")
                    self._kill()
                    return self._session_reset_error("Worker-Prozess abgestürzt -- Session zurückgesetzt")
                return self._session_reset_error("Leere Antwort vom Worker")

            try:
                return json.loads(response_line.strip())
            except json.JSONDecodeError as e:
                logger.warning(f"[PersistentSession] Invalid JSON from worker: {e}")
                return self._session_reset_error(f"Ungültige Worker-Antwort: {response_line[:200]}")

    def reset(self) -> None:
        """Reset the session namespace."""
        with self._lock:
            if self._process and self._process.poll() is None:
                try:
                    self._process.stdin.write(json.dumps({"cmd": "reset"}) + "\n")  # type: ignore
                    self._process.stdin.flush()  # type: ignore
                    self._process.stdout.readline()  # type: ignore  # consume response
                except Exception:
                    self._kill()

    def _kill(self) -> None:
        """Kill the worker subprocess and clean up."""
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=5)
            except Exception as kill_exc:
                logger.warning(f"Worker kill fehlgeschlagen: {kill_exc}")
                try:
                    self._process.terminate()
                    self._process.wait(timeout=2)
                except Exception as term_exc:
                    logger.warning(f"Worker terminate fallback fehlgeschlagen: {term_exc}")
            self._process = None
        self._started = False

    def close(self) -> None:
        """Shut down gracefully."""
        with self._lock:
            if self._process and self._process.poll() is None:
                try:
                    self._process.stdin.write(json.dumps({"cmd": "quit"}) + "\n")  # type: ignore
                    self._process.stdin.flush()  # type: ignore
                    self._process.wait(timeout=3)
                except Exception:
                    self._kill()
            else:
                self._kill()
            # Clean up worker file
            if self._worker_file and os.path.exists(self._worker_file):
                try:
                    os.unlink(self._worker_file)
                except OSError as exc:
                    logger.debug(f"Konnte Worker-Datei beim Close nicht loeschen ({self._worker_file}): {exc}")

    @staticmethod
    def _session_reset_error(msg: str) -> Dict[str, Any]:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "error": {
                "type": "SessionReset",
                "message": msg,
                "traceback": "",
            },
            "plots": [],
            "files": [],
            "variables": {},
        }


# ══════════════════════════════════════════════════════════════════════════
# 6. PIP INSTALL WHITELIST
# ══════════════════════════════════════════════════════════════════════════

PIP_INSTALL_WHITELIST = {
    # Data Science
    "numpy", "pandas", "scipy", "scikit-learn", "sklearn",
    "statsmodels", "sympy",
    # Visualization
    "matplotlib", "seaborn", "plotly", "bokeh", "altair",
    "wordcloud",
    # Data formats
    "openpyxl", "xlsxwriter", "xlrd", "pyarrow",
    "beautifulsoup4", "bs4", "lxml", "html5lib",
    "pyyaml", "toml",
    # Text & NLP
    "textblob", "nltk", "spacy",
    # Table formatting
    "tabulate", "prettytable",
    # Image
    "pillow", "imageio",
    # Utilities
    "tqdm", "colorama", "rich",
    "pytz", "python-dateutil",
    # Network analysis
    "networkx",
    # Game / GUI (for detached interactive mode)
    "pygame", "arcade", "pyglet", "pyxel",
    "kivy", "dearpygui",
    # Web micro-frameworks (detached)
    "flask", "gradio", "dash", "bottle",
}

# Mapping of import names to pip package names
_IMPORT_TO_PIP = {
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
}


# ══════════════════════════════════════════════════════════════════════════
# 6b. INTERACTIVE / GUI MODULE DETECTION
# ══════════════════════════════════════════════════════════════════════════

# Modules whose presence implies the code needs to run detached (no timeout,
# own process group, no stdout capture).  Grouped by category.
_INTERACTIVE_MODULES: Set[str] = {
    # Game frameworks
    "pygame", "arcade", "pyglet", "pyxel",
    # GUI toolkits
    "tkinter", "tk", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "wx", "kivy", "dearpygui",
    # Web servers (blocking event loops)
    "flask", "streamlit", "gradio", "dash", "fastapi", "uvicorn",
    "bottle", "tornado", "cherrypy",
    # Graphics / Animation
    "turtle", "manim", "vpython", "ursina",
}

# Additional call-pattern heuristic: method calls that signal interactivity
_INTERACTIVE_CALL_PATTERNS: Set[str] = {
    "mainloop", "exec_", "run_forever", "app.run", "serve_forever",
}


# ══════════════════════════════════════════════════════════════════════════
# 7. CODE EXECUTOR ENGINE
# ══════════════════════════════════════════════════════════════════════════

class CodeExecutorEngine:
    """SOTA Code Executor with LLM auto-retry, persistent sessions, multi-plot, AST security.

    Usage:
        engine = CodeExecutorEngine(model_loader=ml)
        result = engine.execute("import numpy as np; print(np.sqrt(144))")
        print(result.to_dict())
    """

    # Error types where web search is most likely to help
    _RESEARCH_WORTHY_ERRORS: Set[str] = {
        "AttributeError",     # API changes, wrong method name
        "TypeError",          # Wrong argument signature
        "ImportError",        # Can't find correct import path
        "ModuleNotFoundError",# Package naming confusion
        "RuntimeError",       # Library-specific runtime issues
        "FileNotFoundError",  # Path/resource issues
        "ConnectionError",    # Network/API issues
        "KeyError",           # Dict structure changes in libraries
        "NotImplementedError",# Deprecated API
        "PermissionError",    # OS-level permission issues
    }

    # Errors where web search is unlikely to help (pure logic/typo)
    _SKIP_RESEARCH_ERRORS: Set[str] = {
        "SyntaxError",        # LLM should handle syntax
        "IndentationError",   # LLM should handle indentation
        "TabError",           # LLM should handle tabs
        "RecursionError",     # Logic error, search won't help
        "MemoryError",        # Resource limit, search won't help
        "SystemExit",         # Intentional exit
        "KeyboardInterrupt",  # User interrupt
    }

    def __init__(
        self,
        model_loader=None,
        sandbox_base_dir: Optional[str] = None,
        max_retries: int = 3,
        default_timeout: int = 30,
        web_search_fn: Optional[Callable[[str], Optional[str]]] = None,
        rag_search_fn: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.model_loader = model_loader
        self.max_retries = max_retries
        self.default_timeout = default_timeout
        self.security = CodeSecurityAnalyzer()

        # Research-Augmented Code Fixing (SOTA: ART, Paranjape et al. 2023)
        # Optional callables for escalated error research.
        # web_search_fn(query) → str|None (search results text)
        # rag_search_fn(query) → str|None (RAG results text)
        self.web_search_fn = web_search_fn
        self.rag_search_fn = rag_search_fn

        # Sandbox root directory
        if sandbox_base_dir:
            self.sandbox_base = Path(sandbox_base_dir)
        else:
            self.sandbox_base = Path(tempfile.gettempdir()) / "code_executor_sandbox"
        self.sandbox_base.mkdir(parents=True, exist_ok=True)

        # Session pool: session_id → PersistentSession
        self._sessions: Dict[str, PersistentSession] = {}
        self._session_lock = threading.Lock()

        # Detached processes: (pid, script_path) for cleanup
        self._detached_pids: List[Tuple[int, str]] = []
        self._detached_lock = threading.Lock()

        # Cleanup on exit
        atexit.register(self.cleanup_all)

        _research_status = []
        if self.web_search_fn:
            _research_status.append("web")
        if self.rag_search_fn:
            _research_status.append("rag")
        _rs = ", ".join(_research_status) if _research_status else "none"
        logger.info(
            f"[CodeExecutorEngine] Initialized (sandbox={self.sandbox_base}, "
            f"max_retries={self.max_retries}, research={_rs})"
        )

    # ──────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────────────

    def save_user_program(self, code: str, artifact_name: Optional[str] = None) -> Dict[str, Any]:
        """Persist successfully executed source as a user-downloadable artifact."""
        requested_name = Path(artifact_name or "program.py").name
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(requested_name).stem).strip("_")
        safe_stem = stem or "program"
        output_dir = self.sandbox_base / "user_programs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{safe_stem}_{uuid.uuid4().hex[:8]}.py"
        output_path.write_text(code, encoding="utf-8")
        return {
            "path": str(output_path.resolve()),
            "name": output_path.name,
            "size": output_path.stat().st_size,
            "media_type": "text/x-python",
        }

    def execute(
        self,
        code: str,
        timeout: Optional[int] = None,
        session_id: Optional[str] = None,
        auto_retry: bool = True,
        auto_install: bool = True,
        detached: Optional[bool] = None,
    ) -> ExecutionResult:
        """Execute Python code with AST security, auto-retry, and multi-plot support.

        Args:
            code: Python source code to execute
            timeout: Execution timeout in seconds (default: 30)
            session_id: If provided, use/create a persistent session
            auto_retry: LLM-based auto-fix on errors (requires model_loader)
            auto_install: Auto-install whitelisted packages on ImportError
            detached: If True, launch as a background process (no timeout, no stdout capture).
                      If None, auto-detect based on interactive module imports.

        Returns:
            ExecutionResult with all outputs, plots, files, errors.
            For detached mode: success=True, detached=True, pid, script_path.
        """
        timeout = timeout or self.default_timeout
        start = time.perf_counter()

        if not code or not code.strip():
            return ExecutionResult(
                success=False,
                error=StructuredError(
                    error_type="EmptyCode",
                    message="Leerer Code -- nichts auszuführen.",
                    traceback_str="",
                ),
                code_versions=[code],
            )

        # ── 1. AST Security Check ──
        violations = self.security.analyze(code)
        if violations:
            return ExecutionResult(
                success=False,
                error=StructuredError(
                    error_type="SecurityViolation",
                    message=f"🔒 SICHERHEIT: {'; '.join(violations)}",
                    traceback_str="",
                ),
                code_versions=[code],
            )

        # ── 2. Detached mode: explicit or auto-detected ──
        use_detached = detached if detached is not None else self._detect_interactive_code(code)
        if use_detached:
            # Auto-install required packages BEFORE launching detached
            auto_installed: List[str] = []
            if auto_install:
                auto_installed = self._auto_install_for_detached(code)

            # ── Detached retry loop (same logic as non-detached) ──
            # ROOT-CAUSE FIX: The original detached path had NO retry loop --
            # a single LLM typo (e.g. GAME_AA_REA_LEFT) caused an immediate
            # crash with no recovery. The non-detached path has up to 3 LLM-based
            # code-fix retries; detached now gets the same treatment.
            code_versions: List[str] = [code]
            current_code = code
            last_result: Optional[ExecutionResult] = None

            has_llm = auto_retry and self.model_loader is not None
            max_attempts = (self.max_retries + 1) if has_llm else 1

            for attempt in range(max_attempts):
                if attempt > 0:
                    logger.info(f"[DETACHED-RETRY] Attempt {attempt + 1}/{max_attempts}")

                result = self._execute_detached(current_code, start)
                last_result = result

                if result.success:
                    result.retries_used = attempt
                    result.code_versions = code_versions
                    result.auto_installed = auto_installed
                    return result

                # LLM-based code fix -- same mechanism as non-detached
                if attempt < max_attempts - 1 and self.model_loader and result.error:
                    fixed_code = self._fix_code_with_llm(
                        current_code, result.error, attempt, code_versions
                    )
                    if fixed_code and fixed_code.strip() != current_code.strip():
                        current_code = fixed_code
                        code_versions.append(fixed_code)
                        logger.info(
                            f"[DETACHED-RETRY] LLM generated fix "
                            f"(attempt {attempt + 1})"
                        )
                    else:
                        logger.warning(
                            "[DETACHED-RETRY] LLM could not produce a "
                            "different fix -- stopping retries"
                        )
                        break

            # All retries exhausted
            if last_result:
                last_result.retries_used = len(code_versions) - 1
                last_result.code_versions = code_versions
                last_result.auto_installed = auto_installed
            return last_result or ExecutionResult(
                success=False,
                code_versions=code_versions,
            )

        # ── 3. Prepare sandbox directory ──
        sandbox_dir = self._get_session_sandbox(session_id)

        # ── 4. Execute with retry loop ──
        code_versions: List[str] = [code]
        current_code = code
        last_result: Optional[ExecutionResult] = None
        auto_installed: List[str] = []

        has_llm = auto_retry and self.model_loader is not None
        max_attempts = (self.max_retries + 1) if has_llm else 1

        for attempt in range(max_attempts):
            if attempt > 0:
                logger.info(f"[CODE-RETRY] Attempt {attempt + 1}/{max_attempts}")

            # Execute
            raw = self._execute_code(current_code, timeout, sandbox_dir, session_id)
            result = self._raw_to_result(raw, time.perf_counter() - start)

            last_result = result

            if result.success:
                result.retries_used = attempt
                result.code_versions = code_versions
                result.auto_installed = auto_installed
                return result

            # ── Auto-install on ModuleNotFoundError ──
            if auto_install and result.error and result.error.error_type in (
                "ModuleNotFoundError", "ImportError"
            ):
                module_name = self._extract_module_name(result.error.message)
                if module_name:
                    pip_name = _IMPORT_TO_PIP.get(module_name, module_name)
                    if pip_name in PIP_INSTALL_WHITELIST or module_name in PIP_INSTALL_WHITELIST:
                        if self._try_auto_install(pip_name):
                            auto_installed.append(pip_name)
                            logger.info(f"[AUTO-INSTALL] Installed '{pip_name}' -- retrying same code")
                            # Retry same code (don't count as LLM retry)
                            raw2 = self._execute_code(current_code, timeout, sandbox_dir, session_id)
                            result2 = self._raw_to_result(raw2, time.perf_counter() - start)
                            if result2.success:
                                result2.retries_used = attempt
                                result2.code_versions = code_versions
                                result2.auto_installed = auto_installed
                                return result2
                            last_result = result2

            # ── LLM-based code fix ──
            if attempt < max_attempts - 1 and self.model_loader and result.error:
                fixed_code = self._fix_code_with_llm(
                    current_code, result.error, attempt, code_versions
                )
                if fixed_code and fixed_code.strip() != current_code.strip():
                    current_code = fixed_code
                    code_versions.append(fixed_code)
                    logger.info(f"[CODE-FIX] LLM generated fix (attempt {attempt + 1})")
                else:
                    logger.warning("[CODE-FIX] LLM could not produce a different fix -- stopping retries")
                    break

        # All retries exhausted
        if last_result:
            last_result.retries_used = len(code_versions) - 1
            last_result.code_versions = code_versions
            last_result.auto_installed = auto_installed
        return last_result or ExecutionResult(success=False)

    def get_session(self, session_id: str) -> PersistentSession:
        """Get or create a persistent session."""
        with self._session_lock:
            if session_id not in self._sessions:
                sandbox = str(self.sandbox_base / f"session_{session_id}")
                self._sessions[session_id] = PersistentSession(sandbox)
            return self._sessions[session_id]

    def reset_session(self, session_id: str) -> None:
        """Reset a persistent session's namespace."""
        with self._session_lock:
            session = self._sessions.get(session_id)
            if session:
                session.reset()

    def cleanup_all(self) -> None:
        """Clean up all sessions AND detached processes (called on exit)."""
        with self._session_lock:
            for sid, session in self._sessions.items():
                try:
                    session.close()
                except Exception as e:
                    logger.debug(f"[CLEANUP] Session {sid}: {e}")
            self._sessions.clear()

        # Terminate all detached processes
        with self._detached_lock:
            for pid, script_path in self._detached_pids:
                try:
                    os.kill(pid, 9)  # SIGKILL
                    logger.info(f"[CLEANUP] Terminated detached PID {pid}")
                except OSError as exc:
                    logger.debug(f"[CLEANUP] Detached PID {pid} konnte nicht beendet werden: {exc}")
            self._detached_pids.clear()

    # ──────────────────────────────────────────────────────────────────
    # DETACHED (INTERACTIVE) EXECUTION
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_interactive_code(code: str) -> bool:
        """Detect if code uses interactive/GUI modules that need detached execution.

        Uses AST analysis (not regex) to find imports of known interactive modules,
        plus heuristic detection of event-loop call patterns.

        Returns True if the code should be launched detached.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False

        imported_modules: Set[str] = set()

        for node in ast.walk(tree):
            # import pygame / import tkinter
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    imported_modules.add(top)
            # from pygame import ... / from flask import ...
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    imported_modules.add(top)

        # Check for known interactive modules
        if imported_modules & _INTERACTIVE_MODULES:
            detected = imported_modules & _INTERACTIVE_MODULES
            logger.info(f"[DETACHED] Auto-detected interactive modules: {detected}")
            return True

        # Heuristic: check call patterns (mainloop, exec_, run_forever, etc.)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in _INTERACTIVE_CALL_PATTERNS:
                    logger.info(f"[DETACHED] Auto-detected interactive call: .{node.func.attr}()")
                    return True

        return False

    def _auto_install_for_detached(self, code: str) -> List[str]:
        """Pre-install any required whitelisted packages for detached code.

        Unlike normal execution where auto-install happens on ImportError,
        detached processes have no stdout capture -- so we install proactively.
        """
        installed: List[str] = []
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return installed

        needed_modules: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    needed_modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                needed_modules.add(node.module.split(".")[0])

        for mod in needed_modules:
            pip_name = _IMPORT_TO_PIP.get(mod, mod)
            if pip_name not in PIP_INSTALL_WHITELIST and mod not in PIP_INSTALL_WHITELIST:
                continue
            # Check if already importable
            try:
                __import__(mod)
                continue
            except ImportError:
                continue
            # Install
            if self._try_auto_install(pip_name):
                installed.append(pip_name)
                logger.info(f"[DETACHED-INSTALL] Pre-installed '{pip_name}' for detached execution")

        return installed

    # Startup grace period: how long to wait before checking if detached process survived
    _DETACHED_STARTUP_WAIT: float = 3.0

    @staticmethod
    def _build_detached_wrapper(user_code: str, error_log_path: str) -> str:
        """Build a wrapper script that catches errors and keeps the window open.

        Without this wrapper, any crash (ImportError, SyntaxError, runtime error)
        causes the window to flash and immediately close -- the user sees nothing.

        The wrapper:
        1. Writes errors to an error log file (for programmatic reading)
        2. Runs the user code inside try/except
        3. On error: prints the full traceback to the console AND the log file,
           then waits for user input so the window stays open
        """
        # Use raw string for Windows paths (backslashes preserved)
        escaped_log = error_log_path.replace("\\", "\\\\")

        # Build wrapper WITHOUT textwrap.dedent to avoid indentation corruption.
        # The user code is indented by 4 spaces to sit inside the try-block.
        indented_user_code = textwrap.indent(user_code.strip(), "    ")

        wrapper = (
            "# ── Detached Execution Wrapper ──\n"
            "# Auto-generated -- do not edit. User code starts after the wrapper.\n"
            "import sys as _sys\n"
            "import traceback as _tb\n"
            "import os as _os\n"
            "\n"
            f'_error_log = "{escaped_log}"\n'
            "\n"
            "try:\n"
            "    # ════ USER CODE START ════\n"
            f"{indented_user_code}\n"
            "    # ════ USER CODE END ════\n"
            "except SystemExit:\n"
            "    pass  # Normal exit (e.g. pygame.quit() -> sys.exit)\n"
            "except BaseException as _exc:\n"
            "    _msg = _tb.format_exc()\n"
            "    # Write to error log (for programmatic reading by the engine)\n"
            "    try:\n"
            '        with open(_error_log, "w", encoding="utf-8") as _f:\n'
            "            _f.write(_msg)\n"
            "    except Exception:\n"
            "        pass\n"
            "    # Print to console so the user can read it\n"
            '    print("\\n" + "=" * 60, file=_sys.stderr)\n'
            '    print("FEHLER beim Ausführen des Programms:", file=_sys.stderr)\n'
            '    print("=" * 60, file=_sys.stderr)\n'
            "    print(_msg, file=_sys.stderr)\n"
            '    print("=" * 60, file=_sys.stderr)\n'
            "    # Keep window open so the user can read the error\n"
            "    try:\n"
            '        input("\\nDrücke Enter zum Schließen...")\n'
            "    except (EOFError, KeyboardInterrupt):\n"
            "        pass\n"
        )
        return wrapper

    def _execute_detached(self, code: str, start_time: float) -> ExecutionResult:
        """Launch code as a detached background process with error capture.

        The code is wrapped in a try/except handler that:
        - Logs errors to a .error.log file (for programmatic reading)
        - Keeps the console window open on crash (so user can see the error)

        After launch, waits a few seconds and checks if the process survived.
        If it died during startup, reads the error log and returns a FAILED result
        with the actual error message -- instead of falsely reporting success.

        On Windows: uses CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE
        so the process gets its own window (important for GUI apps like pygame).
        """
        sandbox_dir = str(self.sandbox_base / "detached")
        os.makedirs(sandbox_dir, exist_ok=True)

        # Write wrapped script
        script_name = f"detached_{uuid.uuid4().hex[:8]}.py"
        script_path = os.path.join(sandbox_dir, script_name)
        error_log_path = script_path + ".error.log"

        wrapped_code = self._build_detached_wrapper(code, error_log_path)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(wrapped_code)
        logger.info(f"[DETACHED] Script written to: {script_path}")

        try:
            # Platform-specific creation flags
            if sys.platform == "win32":
                # CREATE_NEW_PROCESS_GROUP (0x200) + CREATE_NEW_CONSOLE (0x10)
                # This gives the detached process its own console window -- essential
                # for GUI frameworks that expect a window context.
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE
            else:
                flags = 0

            proc = subprocess.Popen(
                [sys.executable, script_path],
                stdin=None,      # Needs stdin for "Press Enter..." on error
                stdout=None,     # Let it use the new console window
                stderr=None,     # Errors visible in the console window
                cwd=sandbox_dir,
                creationflags=flags,
                start_new_session=(sys.platform != "win32"),  # setsid on Unix
            )

            pid = proc.pid
            logger.info(f"[DETACHED] Launched PID {pid}: {script_path}")

            # ── Startup validation: wait, then check if process survived ──
            time.sleep(self._DETACHED_STARTUP_WAIT)

            exit_code = proc.poll()
            process_died = exit_code is not None

            # Check error log -- it may exist even if process is still alive
            # (the wrapper's input("Press Enter...") keeps the process running
            #  after the error, but the log is written BEFORE the input call)
            error_msg = ""
            if os.path.exists(error_log_path):
                try:
                    with open(error_log_path, "r", encoding="utf-8") as f:
                        error_msg = f.read().strip()
                except Exception as read_exc:
                    logger.debug(f"Fehlerlog konnte nicht gelesen werden ({error_log_path}): {read_exc}")

            if error_msg:
                # Code crashed -- error log has the traceback
                logger.warning(
                    f"[DETACHED] Process PID {pid} crashed during startup. "
                    f"Error: {error_msg[:200]}"
                )
                # Kill the process if it's still alive (waiting on input)
                if not process_died:
                    try:
                        proc.kill()
                    except OSError as exc:
                        logger.debug(f"[DETACHED] PID {pid} konnte nach Crash nicht gekillt werden: {exc}")
                # Clean up error log
                try:
                    os.unlink(error_log_path)
                except OSError as exc:
                    logger.debug(f"[DETACHED] Fehlerlog konnte nicht geloescht werden ({error_log_path}): {exc}")

                return ExecutionResult(
                    success=False,
                    stderr=error_msg,
                    error=StructuredError(
                        error_type="DetachedStartupError",
                        message=(
                            f"Das Programm ist beim Start abgestürzt:\n\n"
                            f"{error_msg}"
                        ),
                        traceback_str=error_msg,
                    ),
                    code_versions=[code],
                    execution_time=time.perf_counter() - start_time,
                )

            if process_died:
                # Process died but no error log -- unexpected crash
                logger.warning(
                    f"[DETACHED] Process PID {pid} died during startup "
                    f"(exit code {exit_code}, no error log)"
                )
                return ExecutionResult(
                    success=False,
                    error=StructuredError(
                        error_type="DetachedStartupError",
                        message=(
                            f"Das Programm wurde gestartet, ist aber sofort "
                            f"beendet worden (Exit-Code {exit_code}).\n"
                            f"Mögliche Ursachen: fehlende Pakete, Syntax-Fehler, "
                            f"oder das Programm beendet sich selbst."
                        ),
                        traceback_str="",
                    ),
                    code_versions=[code],
                    execution_time=time.perf_counter() - start_time,
                )

            # Process is still alive -- success
            with self._detached_lock:
                self._detached_pids.append((pid, script_path))

            elapsed = time.perf_counter() - start_time
            return ExecutionResult(
                success=True,
                stdout=(
                    f"✅ Interaktiver Prozess gestartet (PID: {pid})\n"
                    f"📁 Script: {script_path}\n"
                    f"Das Programm läuft in einem eigenen Fenster.\n"
                    f"Zum Beenden: Fenster schließen oder terminate_process({pid}) aufrufen."
                ),
                detached=True,
                pid=pid,
                script_path=script_path,
                execution_time=elapsed,
                code_versions=[code],
            )

        except Exception as e:
            logger.error(f"[DETACHED] Launch failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                error=StructuredError(
                    error_type="DetachedLaunchError",
                    message=f"Detached-Prozess konnte nicht gestartet werden: {e}",
                    traceback_str="",
                ),
                code_versions=[code],
                execution_time=time.perf_counter() - start_time,
            )

    def terminate_detached(self, pid: int) -> bool:
        """Terminate a previously launched detached process by PID.

        Returns True if successfully terminated, False otherwise.
        """
        try:
            os.kill(pid, 9)  # SIGKILL / TerminateProcess
            logger.info(f"[DETACHED] Terminated PID {pid}")
            # Remove from tracking
            with self._detached_lock:
                self._detached_pids = [
                    (p, s) for p, s in self._detached_pids if p != pid
                ]
            return True
        except OSError as e:
            logger.warning(f"[DETACHED] Could not terminate PID {pid}: {e}")
            return False

    def list_detached(self) -> List[Dict[str, Any]]:
        """List all tracked detached processes and their status."""
        result = []
        with self._detached_lock:
            alive = []
            for pid, script_path in self._detached_pids:
                try:
                    os.kill(pid, 0)  # Signal 0 = check if alive
                    is_alive = True
                except OSError:
                    is_alive = False
                result.append({
                    "pid": pid,
                    "script_path": script_path,
                    "alive": is_alive,
                })
                if is_alive:
                    alive.append((pid, script_path))
            # Prune dead processes from tracking
            self._detached_pids = alive
        return result

    # ──────────────────────────────────────────────────────────────────
    # EXECUTION BACKENDS
    # ──────────────────────────────────────────────────────────────────

    def _execute_code(
        self,
        code: str,
        timeout: int,
        sandbox_dir: str,
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        """Execute code using persistent session or ephemeral subprocess."""
        if session_id:
            return self._execute_persistent(code, timeout, session_id)
        else:
            return self._execute_ephemeral(code, timeout, sandbox_dir)

    def _execute_persistent(
        self, code: str, timeout: int, session_id: str
    ) -> Dict[str, Any]:
        """Execute in a persistent session subprocess."""
        session = self.get_session(session_id)
        try:
            return session.execute(code, timeout=timeout)
        except Exception as e:
            logger.error(f"[PERSISTENT] Execution error: {e}")
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": {
                    "type": type(e).__name__,
                    "message": str(e),
                    "traceback": "",
                },
                "plots": [],
                "files": [],
                "variables": {},
            }

    def _execute_ephemeral(
        self, code: str, timeout: int, sandbox_dir: str
    ) -> Dict[str, Any]:
        """Execute in a fresh subprocess (stateless)."""
        os.makedirs(sandbox_dir, exist_ok=True)

        # Write worker script
        worker_fd, worker_path = tempfile.mkstemp(suffix="_worker.py", prefix="ce_")
        os.close(worker_fd)
        with open(worker_path, "w", encoding="utf-8") as f:
            f.write(_WORKER_SCRIPT)

        try:
            proc = subprocess.Popen(
                [sys.executable, worker_path, sandbox_dir],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                ),
            )

            request = json.dumps(
                {"cmd": "execute", "code": code}, ensure_ascii=False
            ) + "\n"
            # Also send quit to terminate after execution
            request += json.dumps({"cmd": "quit"}) + "\n"

            try:
                stdout_data, stderr_data = proc.communicate(
                    input=request, timeout=timeout + 5
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": "",
                    "error": {
                        "type": "TimeoutError",
                        "message": f"Code-Ausführung Timeout nach {timeout}s",
                        "traceback": "",
                    },
                    "plots": [],
                    "files": [],
                    "variables": {},
                }

            # Parse the first JSON line from stdout (the execution result)
            if stdout_data and stdout_data.strip():
                first_line = stdout_data.strip().split("\n")[0]
                try:
                    return json.loads(first_line)
                except json.JSONDecodeError:
                    return {
                        "success": False,
                        "stdout": stdout_data,
                        "stderr": stderr_data,
                        "error": {
                            "type": "WorkerProtocolError",
                            "message": f"Worker returned invalid JSON: {first_line[:200]}",
                            "traceback": "",
                        },
                        "plots": [],
                        "files": [],
                        "variables": {},
                    }
            else:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": stderr_data or "",
                    "error": {
                        "type": "WorkerError",
                        "message": "Worker produced no output",
                        "traceback": stderr_data or "",
                    },
                    "plots": [],
                    "files": [],
                    "variables": {},
                }
        finally:
            # ✅ SOTA: Windows-safe temp file cleanup with retry on PermissionError
            for _attempt in range(3):
                try:
                    os.unlink(worker_path)
                    break
                except FileNotFoundError:
                    break
                except OSError as exc:
                    if _attempt == 2:
                        logger.warning(
                            f"⚠️ Failed to remove worker temp file {worker_path}: {exc}"
                        )
                    else:
                        time.sleep(0.05)

    # ──────────────────────────────────────────────────────────────────
    # RESULT CONVERSION
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _raw_to_result(raw: Dict[str, Any], elapsed: float) -> ExecutionResult:
        """Convert raw worker dict to ExecutionResult."""
        error = None
        if raw.get("error"):
            error = StructuredError.from_dict(raw["error"])

        return ExecutionResult(
            success=raw.get("success", False),
            stdout=raw.get("stdout", ""),
            stderr=raw.get("stderr", ""),
            error=error,
            plots=raw.get("plots", []),
            files=raw.get("files", []),
            variables=raw.get("variables", {}),
            execution_time=elapsed,
        )

    # ──────────────────────────────────────────────────────────────────
    # RESEARCH-AUGMENTED CODE FIXING
    # (ART: Paranjape et al. 2023 + Reflexion: Shinn et al. 2023)
    # ──────────────────────────────────────────────────────────────────

    def _build_error_search_query(self, error: StructuredError, code: str) -> str:
        """Build an effective web search query from the error.

        Strategy:
        - Extract the core error type + message
        - Detect library names from imports in the code
        - Construct a focused query like:
          'python pygame AttributeError module has no attribute init'
        """
        # Extract library names from imports
        libraries: List[str] = []
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        libraries.append(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        libraries.append(node.module.split(".")[0])
        except SyntaxError:
            # If code has syntax errors, try regex fallback
            import_matches = re.findall(
                r"^\s*(?:import|from)\s+([\w.]+)", code, re.MULTILINE
            )
            libraries = [m.split(".")[0] for m in import_matches]

        # Deduplicate, skip builtins
        _BUILTINS = {"os", "sys", "re", "json", "math", "time", "datetime",
                     "collections", "itertools", "functools", "pathlib",
                     "typing", "abc", "io", "copy", "string", "hashlib"}
        libraries = list(dict.fromkeys(
            lib for lib in libraries if lib not in _BUILTINS
        ))[:3]  # Max 3 libraries to keep query focused

        # Build query
        parts = ["python"]
        if libraries:
            parts.extend(libraries[:2])  # Top 2 most relevant libraries

        # Core error info -- truncate long messages
        error_msg = error.message[:120] if error.message else ""
        parts.append(error.error_type)
        if error_msg:
            parts.append(error_msg)

        query = " ".join(parts)
        # Cap at 200 chars for search engine compatibility
        return query[:200].strip()

    def _should_research_error(
        self, error: StructuredError, attempt: int, code: str
    ) -> bool:
        """Decide whether this error warrants web/RAG research.

        Heuristic (escalation strategy):
        - Attempt 0: Never research (fast blind fix first)
        - Attempt 1+: Research if error type benefits from it
        - Always skip for trivial errors (SyntaxError, IndentationError)
        - Always research for library-specific errors on retry
        """
        # First attempt: always try blind fix first (fast path)
        if attempt < 1:
            return False

        # No research tools available
        if not self.web_search_fn and not self.rag_search_fn:
            return False

        # Skip research for trivial errors
        if error.error_type in self._SKIP_RESEARCH_ERRORS:
            return False

        # NameError: Usually a typo -- but if the blind fix already failed,
        # it might be a library constant the LLM doesn't know
        if error.error_type == "NameError" and attempt >= 2:
            return True

        # Research-worthy errors: always search from attempt 1+
        if error.error_type in self._RESEARCH_WORTHY_ERRORS:
            return True

        # For any other error: research if we're on attempt 2+ (desperation)
        return attempt >= 2

    def _research_error(
        self,
        error: StructuredError,
        code: str,
        attempt: int,
    ) -> str:
        """Research an error via web search and/or RAG.

        Returns a formatted context string to inject into the LLM fix prompt,
        or empty string if no useful results found.

        SOTA: Escalation pattern (ART: Paranjape et al. 2023)
        - Uses web search for general programming knowledge
        - Uses RAG for project-specific patterns and documentation
        """
        if not self._should_research_error(error, attempt, code):
            return ""

        query = self._build_error_search_query(error, code)
        research_parts: List[str] = []

        # ── Web Search: General programming knowledge ──
        if self.web_search_fn:
            try:
                logger.info(f"[CODE-RESEARCH] Web search: '{query[:80]}'")
                web_result = self.web_search_fn(query)
                if web_result and len(web_result.strip()) > 50:
                    # Truncate to keep prompt manageable
                    web_text = web_result.strip()[:3000]
                    research_parts.append(
                        f"WEB-RECHERCHE-ERGEBNISSE (Suche: '{query[:80]}'):\n"
                        f"{web_text}"
                    )
                    logger.info(
                        f"[CODE-RESEARCH] Web search returned "
                        f"{len(web_text)} chars of context"
                    )
                else:
                    logger.debug("[CODE-RESEARCH] Web search returned no useful results")
            except Exception as e:
                logger.warning(f"[CODE-RESEARCH] Web search failed: {e}")

        # ── RAG Search: Project-specific knowledge ──
        if self.rag_search_fn:
            # Build a more code-specific query for RAG
            rag_query = f"{error.error_type} {error.message[:80]}"
            try:
                logger.info(f"[CODE-RESEARCH] RAG search: '{rag_query[:80]}'")
                rag_result = self.rag_search_fn(rag_query)
                if rag_result and len(rag_result.strip()) > 50:
                    rag_text = rag_result.strip()[:2000]
                    research_parts.append(
                        f"RAG-WISSENSBASIS-ERGEBNISSE:\n{rag_text}"
                    )
                    logger.info(
                        f"[CODE-RESEARCH] RAG search returned "
                        f"{len(rag_text)} chars of context"
                    )
                else:
                    logger.debug("[CODE-RESEARCH] RAG search returned no useful results")
            except Exception as e:
                logger.warning(f"[CODE-RESEARCH] RAG search failed: {e}")

        if not research_parts:
            return ""

        return (
            "\n\n── RECHERCHE-ERGEBNISSE (nutze diese als Referenz) ──\n"
            + "\n\n".join(research_parts)
            + "\n── ENDE RECHERCHE ──"
        )

    def _fix_code_with_llm(
        self,
        code: str,
        error: StructuredError,
        attempt: int,
        previous_versions: List[str],
    ) -> Optional[str]:
        """Ask the LLM to fix the code based on structured error info.

        Uses a focused prompt with:
        - Error type, message, line number, code context
        - Full traceback
        - Previous fix attempts (to avoid repeating)
        - SOTA: Web/RAG research results on escalated retries (attempt >= 1)

        Returns:
            Fixed code string, or None if fix failed
        """
        if not self.model_loader:
            return None

        # Build context about previous attempts
        prev_ctx = ""
        if len(previous_versions) > 1:
            prev_ctx = (
                "\n\nBISHERIGE FIX-VERSUCHE (vermeide dieselben Fehler):\n"
                + "\n---\n".join(
                    f"Versuch {i}:\n```python\n{v}\n```"
                    for i, v in enumerate(previous_versions[1:], 1)
                )
            )

        # SOTA: Research-Augmented Fixing (escalation on retry)
        # Attempt 0 = blind fix (fast), Attempt 1+ = research-augmented
        research_ctx = self._research_error(error, code, attempt)
        if research_ctx:
            logger.info(
                f"[CODE-FIX] Research-augmented fix "
                f"(attempt {attempt + 1}, {len(research_ctx)} chars of research)"
            )

        prompt = f"""Der folgende Python-Code hat einen Fehler erzeugt.
Korrigiere NUR den Fehler. Ändere nicht die Logik oder füge keine neuen Features hinzu.

{error.to_llm_prompt(code)}
{prev_ctx}
{research_ctx}

WICHTIG:
- Gib NUR den korrigierten Python-Code zurück
- Kein Markdown, keine Erklärung, kein ```python Block
- Nur reiner Python-Code
{('- Nutze die RECHERCHE-ERGEBNISSE oben als Referenz für die korrekte API/Syntax' if research_ctx else '')}"""

        try:
            system_content = (
                "Du bist ein Python-Debugging-Experte. "
                "Gib NUR korrigierten Python-Code zurück, "
                "ohne Markdown-Formatierung oder Erklärung."
            )
            if research_ctx:
                system_content += (
                    " Dir stehen Recherche-Ergebnisse aus dem Web/RAG zur "
                    "Verfügung -- nutze diese für die korrekte Lösung."
                )

            response = self.model_loader.generate_response(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2048,
                temperature=0.2,
            )

            if not response:
                return None

            # Clean up response -- strip markdown fences if present
            fixed = response.strip()
            if fixed.startswith("```python"):
                fixed = fixed[len("```python"):].strip()
            if fixed.startswith("```"):
                fixed = fixed[3:].strip()
            if fixed.endswith("```"):
                fixed = fixed[:-3].strip()

            # Sanity check: must be valid Python
            try:
                ast.parse(fixed)
            except SyntaxError:
                logger.warning("[CODE-FIX] LLM produced invalid Python syntax")
                return None

            # Security check on fixed code
            violations = self.security.analyze(fixed)
            if violations:
                logger.warning(f"[CODE-FIX] LLM fix has security violations: {violations}")
                return None

            return fixed

        except Exception as e:
            logger.warning(f"[CODE-FIX] LLM call failed: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────
    # AUTO-INSTALL
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_module_name(error_msg: str) -> Optional[str]:
        """Extract module name from ImportError/ModuleNotFoundError message."""
        # "No module named 'xyz'" or "No module named 'xyz.abc'"
        match = re.search(r"No module named ['\"]([^'\"]+)['\"]", error_msg)
        if match:
            return match.group(1).split(".")[0]
        # "cannot import name 'X' from 'Y'"
        match = re.search(r"cannot import name .+ from ['\"]([^'\"]+)['\"]", error_msg)
        if match:
            return match.group(1).split(".")[0]
        return None

    def _try_auto_install(self, package_name: str) -> bool:
        """Install a whitelisted package via pip."""
        pip_name = _IMPORT_TO_PIP.get(package_name, package_name)
        if pip_name not in PIP_INSTALL_WHITELIST and package_name not in PIP_INSTALL_WHITELIST:
            logger.warning(f"[AUTO-INSTALL] '{pip_name}' not in whitelist -- skipping")
            return False

        logger.info(f"[AUTO-INSTALL] Installing '{pip_name}'...")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_name, "--quiet"],
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                ),
            )
            if result.returncode == 0:
                logger.info(f"[AUTO-INSTALL] '{pip_name}' installed successfully")
                return True
            else:
                logger.warning(f"[AUTO-INSTALL] pip install failed: {result.stderr[:200]}")
                return False
        except Exception as e:
            logger.warning(f"[AUTO-INSTALL] Exception: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────

    def _get_session_sandbox(self, session_id: Optional[str] = None) -> str:
        """Get or create the sandbox directory for a session."""
        if session_id:
            d = str(self.sandbox_base / f"session_{session_id}")
        else:
            d = str(self.sandbox_base / f"ephemeral_{uuid.uuid4().hex[:8]}")
        os.makedirs(d, exist_ok=True)
        return d
