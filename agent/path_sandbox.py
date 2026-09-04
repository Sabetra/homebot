"""
Path Sandbox (SOTA-Hardened, 2026-08)
=====================================

Zentrale Pfad-Validierung für alle LLM-callable Tools, die Dateien lesen
oder schreiben. Verhindert Path-Traversal- und Symlink-Escape-Angriffe und
limitiert Dateigrößen.

Design:
- Whitelist von Base-Directories. Default: Workspace-Root (Eltern-Verzeichnis
  dieses Moduls).
- Vergleich nach `os.path.realpath()` + `os.path.commonpath()` (löst symlinks
  auf, normalisiert ".." etc.).
- Größenlimits beim Lesen (DoS-Schutz).
- Erweiterbares Whitelisting (z.B. zusätzliche Daten-Verzeichnisse).

SOTA-Erweiterungen (2026):
- Symlink-Rejection (nicht nur Resolution): `os.path.islink()` Check
- Binary-File-Erkennung: Magic-Bytes-Check vor Text-Read
- Max-Depth-Limiter für Directory-Listings
- list_directory_safe(), search_files_safe(), read_file_safe()
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class PathSandboxError(PermissionError):
    """Pfad ausserhalb der erlaubten Basisverzeichnisse oder Datei zu gross."""


# Default-Workspace-Root: Verzeichnis oberhalb von agent/
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent

# Standard-Größenlimit für Dateilesevorgänge: 50 MB
# P0-Byte-Guard: Dateien >20MB werden abgewiesen (Token-Budget-Schutz).
# Vertrag verankert in tests/test_file_reader_offset_limit.py
# (test_oversize_byte_guard_preserved: 30MB-Datei muss PathSandboxError werfen).
DEFAULT_MAX_READ_BYTES = 20 * 1024 * 1024

# Standard-Größenlimit für Schreibvorgänge: 50 MB
DEFAULT_MAX_WRITE_BYTES = 50 * 1024 * 1024

# SOTA: Default-Max-Depth für Directory-Listings
DEFAULT_MAX_DEPTH = 5

# SOTA: Default-Max-Results für File-Search
DEFAULT_MAX_SEARCH_RESULTS = 200

# SOTA: Max-Char-Limit für safe file reads (Token-Budget-Schutz)
DEFAULT_MAX_READ_CHARS = 50_000

# P1 (2026-08-24): Default-Read-Fenster in ZEILEN (1-based offset/limit,
# Claude Code Read-Modell) — primäre Navigation für große Dateien.
# Das Char-Limit (DEFAULT_MAX_READ_CHARS) bleibt Sicherheits-Backstop.
DEFAULT_READ_LINE_LIMIT = 2000

# P2 (2026-08-25): ripgrep-Content-Search (Workdoc-DoD #5: Cap 50, Timeout
# 10s, partial/timed_out). rg-Dateigrößenlimit bewusst = 20 MB (P1-Byte-Guard),
# damit beide Schutz-Ebenen dieselbe Obergrenze kommunizieren.
DEFAULT_RG_MAX_RESULTS = 50
DEFAULT_RG_TIMEOUT = 10.0
_RG_MAX_FILESIZE = "20M"

# SOTA: Binary-Magic-Bytes (erste Bytes, die Binärdateien identifizieren)
BINARY_MAGIC_BYTES: Set[bytes] = {
    b"\x89PNG",           # PNG
    b"\xff\xd8\xff",      # JPEG
    b"GIF8",              # GIF
    b"PK\x03\x04",        # ZIP/DOCX/XLSX/JAR
    b"%PDF",              # PDF
    b"\x1f\x8b",          # GZIP
    b"\xfd7zXZ",          # XZ
    b"\x7fELF",           # ELF Binary
    b"MZ",                # PE/EXE/DLL (Windows)
    b"\x00",              # Null-byte (oft Binär)
    b"\x78\x9c",          # gzip-compressed
    b"\x78\x01",          # gzip-compressed
    b"\x78\xda",          # gzip-compressed
    b"BSDIFF",            # bsdiff
    b"\x4f\x67\x67\x53",  # Ogg
    b"RIFF",              # WebM/OGG/WAV
    b"\xca\xfe\xba\xbe",  # Java/Mach-O
    b"\xfe\xed\xfa\xce",  # Mach-O
    b"\xcf\xfa\xed\xfe",  # Mach-O
    b"\xc9\xfa\xed\xfe",  # Mach-O
    b"\xfe\xed\xfa\xcf",  # Mach-O
    b"\x4d\x5a\x90\x00",  # MZ EXE extended
    b"\x50\x4f\x57\x45",  # POWER (PowerPoint)
    b"\xd0\xcf\x11\xe0",  # OLE2 (old Office)
    b"\x50\x4b\x03\x04",  # PK ZIP (same as above, explicit)
    b"\x50\x4b\x05\x06",  # PK empty ZIP
    b"\x50\x4b\x07\x08",  # PK spanned ZIP
    b"\x4d\x53\x43\x46",  # MSCF (Cabinet)
    b"\x45\x4c\x46\x00",  # ELF extended
    b"\x7e\xdf\xda\xbe",  # Dart
    b"\x64\x65\x6c\x69",  # Delphi
    b"\x4d\x5a\x00\x00",  # MZ variant
    b"\x4d\x5a\x09\x00",  # MZ variant
    b"\x4d\x5a\x10\x00",  # MZ variant
    b"\x4d\x5a\x20\x00",  # MZ variant
    b"\x4d\x5a\x50\x00",  # MZ variant
    b"\x4d\x5a\x90\x00",  # MZ variant
    b"\x4d\x5a\xe0\x00",  # MZ variant
}

# SOTA: Dateitypen die als Binär behandelt werden (Extension-basiert)
BINARY_EXTENSIONS: Set[str] = {
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".pyc", ".pyo", ".pyd", ".whl",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".avi", ".mkv", ".flac", ".wav", ".ogg",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".db", ".sqlite", ".sqlite3",
    ".ico", ".cur", ".ani",
    ".woff", ".woff2", ".ttf", ".otf",
    ".class", ".jar",
    ".bin", ".dat", ".cache",
    ".onnx", ".pt", ".pth", ".ckpt", ".bin",
    ".gguf", ".safetensors",
}


class RipgrepNotFoundError(RuntimeError):
    """ripgrep-Binary nicht installiert (expliziter Fallback-Trigger).

    Bewusst KEINE ``PathSandboxError``: Es ist eine Umgebungsbedingung,
    keine Sandbox-Verletzung. Die Toolkit-Ebene fängt sie ab und aktiviert
    den expliziten Python-Fallback (Resultat trägt ``backend="python"``).
    """


_RG_BIN_CACHE: Optional[str] = None


def rg_bin() -> Optional[str]:
    """Pfad zur ripgrep-Binary auflösen (gecacht).

    Auflösung: Env-Override ``BOT6_RG_BIN`` (Test-/Deploy-Flexibilität) →
    ``shutil.which("rg")``. Returns ``None`` wenn ripgrep nicht installiert
    ist — die Toolkit-Ebene entscheidet dann über den expliziten
    Python-Fallback (keine silent fallbacks).
    """
    global _RG_BIN_CACHE
    if _RG_BIN_CACHE is None:
        _RG_BIN_CACHE = os.environ.get("BOT6_RG_BIN") or shutil.which("rg")
    return _RG_BIN_CACHE


def _rg_lines_text(data: Dict[str, Any]) -> str:
    """Textfeld aus rg ``--json``-Events extrahieren.

    rg ≥ 13: ``data.lines`` ist Objekt ``{"text": "..."}``; ältere Versionen
    liefern einen String. Beide Formate werden unterstützt.
    """
    lines = data.get("lines")
    if lines is None:
        lines = data.get("line_text")
    if isinstance(lines, dict):
        lines = lines.get("text")
    if lines is None:
        return ""
    return str(lines).rstrip("\r\n")


@dataclass(frozen=True)
class SandboxPolicy:
    """Konfiguration des Pfad-Sandbox."""

    base_dirs: Tuple[Path, ...]
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES
    allow_create_subdirs: bool = True
    # SOTA: Depth-Limiter
    max_depth: int = DEFAULT_MAX_DEPTH
    # SOTA: Search-Limiter
    max_search_results: int = DEFAULT_MAX_SEARCH_RESULTS
    # SOTA: Char-Limiter für Token-Budget
    max_read_chars: int = DEFAULT_MAX_READ_CHARS


def _coerce_base_dirs(base_dirs: Optional[Iterable[os.PathLike[str] | str]]) -> Tuple[Path, ...]:
    if base_dirs is None:
        return (_DEFAULT_ROOT,)
    resolved = []
    for d in base_dirs:
        p = Path(d).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            raise PathSandboxError(f"Base-Dir existiert nicht oder ist kein Verzeichnis: {p}")
        resolved.append(p)
    if not resolved:
        return (_DEFAULT_ROOT,)
    return tuple(resolved)


def _is_binary_file(path: Path) -> bool:
    """SOTA: Prüft ob eine Datei binär ist (Magic-Bytes + Extension)."""
    # Extension-Check (schnell)
    suffix = path.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return True

    # Magic-Bytes-Check (zuverlässig)
    try:
        with path.open("rb") as f:
            header = f.read(8)
        for magic in BINARY_MAGIC_BYTES:
            if header.startswith(magic):
                return True
        # Null-byte im Header = wahrscheinlich binär
        if b"\x00" in header:
            return True
    except (OSError, PermissionError):
        pass
    return False


def _compute_depth(base: Path, target: Path) -> int:
    """Berechnet die Tiefe relativ zum Base-Dir."""
    try:
        rel = target.relative_to(base)
        return rel.parts.count(os.sep) + 1
    except ValueError:
        return 0


class PathSandbox:
    """Zentrale Pfad-Validierung (SOTA-hardened)."""

    def __init__(
        self,
        base_dirs: Optional[Iterable[os.PathLike[str] | str]] = None,
        max_read_bytes: int = DEFAULT_MAX_READ_BYTES,
        max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES,
        allow_create_subdirs: bool = True,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_search_results: int = DEFAULT_MAX_SEARCH_RESULTS,
        max_read_chars: int = DEFAULT_MAX_READ_CHARS,
    ) -> None:
        self.policy = SandboxPolicy(
            base_dirs=_coerce_base_dirs(base_dirs),
            max_read_bytes=max_read_bytes,
            max_write_bytes=max_write_bytes,
            allow_create_subdirs=allow_create_subdirs,
            max_depth=max_depth,
            max_search_results=max_search_results,
            max_read_chars=max_read_chars,
        )

    # ------------------------------------------------------------------
    # Kern-Validierung
    # ------------------------------------------------------------------
    def _is_within(self, candidate: Path) -> bool:
        cand_str = str(candidate)
        for base in self.policy.base_dirs:
            try:
                # commonpath wirft ValueError, wenn Drive sich unterscheidet (Windows)
                if os.path.commonpath([cand_str, str(base)]) == str(base):
                    return True
            except ValueError:
                continue
        return False

    def _check_symlink(self, path: Path) -> None:
        """SOTA: Expliziter Symlink-Check (nicht nur Resolution)."""
        if path.is_symlink():
            raise PathSandboxError(
                f"Symlink-Zugriff verboten (Security): {path}"
            )

    def resolve(self, user_path: str, *, must_exist: bool) -> Path:
        """Validiert + normalisiert einen vom LLM gelieferten Pfad.

        Args:
            user_path: Pfad-String wie vom LLM übergeben.
            must_exist: Wenn True, wird PathSandboxError geworfen falls die
                Datei nicht existiert. Wenn False (z.B. für Schreibvorgang),
                wird der *Eltern*-Pfad geprüft und dessen Realpath verwendet.

        Returns:
            Absoluter, aufgelöster Path innerhalb der Sandbox.
        """
        if not user_path or not isinstance(user_path, str):
            raise PathSandboxError("Leerer oder ungültiger Pfad")

        raw = Path(user_path).expanduser()
        # Falls relativ, gegen erstes Base-Dir auflösen (Workspace-Root)
        if not raw.is_absolute():
            raw = self.policy.base_dirs[0] / raw

        if must_exist:
            if not raw.exists():
                raise PathSandboxError(f"Datei existiert nicht: {raw}")
            # SOTA: Symlink-Check VOR Resolution
            self._check_symlink(raw)
            real = raw.resolve(strict=True)
            if not self._is_within(real):
                raise PathSandboxError(
                    f"Pfad ausserhalb der Sandbox (Base: {[str(b) for b in self.policy.base_dirs]}): {real}"
                )
            return real

        # must_exist=False → Eltern-Verzeichnis muss in Sandbox liegen
        parent = raw.parent
        if not parent.exists():
            if not self.policy.allow_create_subdirs:
                raise PathSandboxError(f"Eltern-Verzeichnis existiert nicht: {parent}")
            # Vor mkdir prüfen, ob theoretischer Realpath in Sandbox läge.
            # Wir laufen bis zum ersten existierenden Vorfahren.
            anchor = parent
            while not anchor.exists():
                anchor = anchor.parent
            anchor_real = anchor.resolve(strict=True)
            if not self._is_within(anchor_real):
                raise PathSandboxError(
                    f"Schreibziel ausserhalb der Sandbox: {raw} (Anchor: {anchor_real})"
                )
            # Eltern noch nicht erstellen — das macht der Aufrufer bewusst.
            return (anchor_real / raw.relative_to(anchor)).resolve(strict=False)

        parent_real = parent.resolve(strict=True)
        if not self._is_within(parent_real):
            raise PathSandboxError(
                f"Schreibziel ausserhalb der Sandbox: {raw} (Parent: {parent_real})"
            )
        return (parent_real / raw.name).resolve(strict=False)

    # ------------------------------------------------------------------
    # I/O-Helfer mit integrierter Sandbox + Größenprüfung
    # ------------------------------------------------------------------
    def read_text(self, user_path: str, encoding: str = "utf-8") -> Tuple[Path, str]:
        path = self.resolve(user_path, must_exist=True)
        # SOTA: Binary-Check vor Text-Read
        if _is_binary_file(path):
            raise PathSandboxError(
                f"Binärdatei kann nicht als Text gelesen werden: {path.name} "
                f"(Type: {path.suffix or 'unknown'})"
            )
        size = path.stat().st_size
        if size > self.policy.max_read_bytes:
            raise PathSandboxError(
                f"Datei größer als Limit: {size} Bytes > {self.policy.max_read_bytes} Bytes"
            )
        with path.open("r", encoding=encoding) as fh:
            return path, fh.read()

    def write_text(self, user_path: str, content: str, encoding: str = "utf-8") -> Tuple[Path, int]:
        path = self.resolve(user_path, must_exist=False)
        encoded = content.encode(encoding)
        if len(encoded) > self.policy.max_write_bytes:
            raise PathSandboxError(
                f"Inhalt zu gross: {len(encoded)} Bytes > Limit {self.policy.max_write_bytes} Bytes"
            )
        if self.policy.allow_create_subdirs:
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            fh.write(encoded)
        return path, len(encoded)

    def list_base_dirs(self) -> List[str]:
        return [str(b) for b in self.policy.base_dirs]

    # ------------------------------------------------------------------
    # SOTA: Neue safe-Helfer für Filesystem-Tools
    # ------------------------------------------------------------------

    @dataclass
    class FileInfo:
        """Strukturierter Datei/Dir-Eintrag."""
        name: str
        path: str
        is_dir: bool
        size: int  # 0 für Verzeichnisse
        modified: float  # timestamp

    def list_directory_safe(
        self,
        user_path: str,
        max_depth: Optional[int] = None,
    ) -> List[FileInfo]:
        """SOTA: Sicheres Directory-Listing mit Depth-Limiter.

        Args:
            user_path: Verzeichnis zum Auflisten.
            max_depth: Maximale Tiefe (Default: policy.max_depth).

        Returns:
            Liste von FileInfo-Einträgen.
        """
        path = self.resolve(user_path, must_exist=True)
        if not path.is_dir():
            raise PathSandboxError(f"Kein Verzeichnis: {path}")

        depth_limit = max_depth if max_depth is not None else self.policy.max_depth
        results: List[PathSandbox.FileInfo] = []

        def _walk(current: Path, depth: int) -> None:
            if depth > depth_limit:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except (PermissionError, OSError) as e:
                logger.warning("Permission denied listing %s: %s", current, e)
                return

            for entry in entries:
                # SOTA: Symlink-Skip (nicht abweisen, sondern überspringen)
                if entry.is_symlink():
                    logger.debug("Skipping symlink: %s", entry)
                    continue

                is_dir = entry.is_dir()
                size = 0
                modified = 0.0
                try:
                    st = entry.stat()
                    size = st.st_size if not is_dir else 0
                    modified = st.st_mtime
                except (OSError, PermissionError):
                    pass

                results.append(PathSandbox.FileInfo(
                    name=entry.name,
                    path=str(entry),
                    is_dir=is_dir,
                    size=size,
                    modified=modified,
                ))

                # Rekursiv nur für Unterverzeichnisse
                if is_dir and depth < depth_limit:
                    _walk(entry, depth + 1)

        _walk(path, depth=1)
        logger.info("list_directory_safe: %d entries from %s (depth=%d)", len(results), path, depth_limit)
        return results

    def search_files_safe(
        self,
        root_path: str,
        pattern: str,
        content_search: bool = False,
        max_depth: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        """SOTA: Sichere Dateisuche mit Pattern-Matching.

        Args:
            root_path: Root-Verzeichnis für die Suche.
            pattern: Regex-Pattern für Dateinamen (oder Inhalt bei content_search=True).
            content_search: Wenn True, suche auch im Dateiinhalt.
            max_depth: Maximale Suchtiefe.

        Returns:
            Liste von Dicts mit 'path', 'name', 'match_type' (name|content).
        """
        root = self.resolve(root_path, must_exist=True)
        if not root.is_dir():
            raise PathSandboxError(f"Kein Verzeichnis: {root}")

        depth_limit = max_depth if max_depth is not None else self.policy.max_depth
        compiled = re.compile(pattern, re.IGNORECASE)
        results: List[Dict[str, str]] = []

        def _search(current: Path, depth: int) -> None:
            if depth > depth_limit or len(results) >= self.policy.max_search_results:
                return

            try:
                entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except (PermissionError, OSError):
                return

            for entry in entries:
                if len(results) >= self.policy.max_search_results:
                    break

                # Symlink-Skip
                if entry.is_symlink():
                    continue

                # Name-Match
                if compiled.search(entry.name):
                    results.append({
                        "path": str(entry),
                        "name": entry.name,
                        "match_type": "name",
                    })

                # Content-Match (nur bei Text-Dateien)
                if content_search and entry.is_file() and not _is_binary_file(entry):
                    try:
                        with entry.open("r", encoding="utf-8", errors="ignore") as f:
                            # Nur erste 10K für Content-Search (Performance)
                            chunk = f.read(10_000)
                        if compiled.search(chunk):
                            results.append({
                                "path": str(entry),
                                "name": entry.name,
                                "match_type": "content",
                            })
                    except (OSError, PermissionError, UnicodeDecodeError):
                        pass

                # Rekursiv
                if entry.is_dir() and depth < depth_limit:
                    _search(entry, depth + 1)

        _search(root, depth=1)
        logger.info(
            "search_files_safe: %d results for pattern '%s' in %s",
            len(results), pattern, root,
        )
        return results

    # ------------------------------------------------------------------
    # P2 (2026-08-25): ripgrep-Content-Search (SOTA: Agent-Standard 2026,
    # Claude Code Grep / Codex CLI). ReDoS-sichere lineare Regex,
    # gitignore-/hidden-Auswahl, Binary-Skip, UTF-16, --json-Ereignisse.
    # ------------------------------------------------------------------

    def search_content_rg(
        self,
        root_path: str,
        pattern: str,
        *,
        case_sensitive: bool = False,
        fixed_string: bool = False,
        glob: Optional[str] = None,
        hidden: bool = False,
        context: int = 0,
        max_results: int = DEFAULT_RG_MAX_RESULTS,
        timeout: float = DEFAULT_RG_TIMEOUT,
    ) -> Dict[str, Any]:
        """Inhaltssuche über den verifizierten Sandbox-Root via ripgrep.

        Sicherheit:
        - Root wird VOR dem Spawn durch ``resolve()`` verifiziert
          (Sandbox-Verletzung → ``PathSandboxError``, unverändert).
        - Argumente als Liste (kein ``shell=True`` → kein Injection-Vektor).
        - Windows: ``CREATE_NO_WINDOW`` (keine Konsole-Flicker).
        - Timeout-Kill mit Partial-Output (``partial``/``timed_out`` im
          Rückgabedict; DoD: Cap 50 / Timeout 10s).
        - Dateien > 20 MB werden übersprungen (``--max-filesize``),
          ausgerichtet mit dem P1-Byte-Guard.
        - Hidden-Dateien (Dotfiles) und .gitignore-Einträge (venv,
          node_modules) werden standardmäßig übersprungen; ``hidden=True``
          sucht zusätzlich Dotfiles (``--hidden``, .gitignore
          bleibt respektiert).

        Args:
            fixed_string: Pattern als fester Text statt Regex behandeln
                (rg ``-F``). Default: aus (Rust-Regex-Semantik).
            hidden: Dotfiles einbeziehen (Default: aus — z.B. .env nicht
                ungefragt in den Kontext ziehen).

        Returns:
            Dict mit ``backend``, ``count``, ``truncated``, ``timed_out``,
            ``matches`` (Liste ``{path, line, text, context_before,
            context_after}`` — ``path`` ist absolut und verifiziert unter
            dem Sandbox-Root), ``elapsed_ms``, ``error``, ``error_class``.

        Raises:
            PathSandboxError: Root fehlt/außerhalb der Sandbox.
            RipgrepNotFoundError: rg-Binary nicht installiert
                (Toolkit-Fallback: reiner Python-Backend, dokumentiert).
            ValueError: leeres Pattern.
        """
        root = self.resolve(root_path, must_exist=True)
        rg = rg_bin()
        if rg is None:
            raise RipgrepNotFoundError(
                "ripgrep (rg) wurde nicht gefunden — ripgrep-Backend "
                "für die Content-Suche ist nicht verfügbar."
            )
        if not pattern or not pattern.strip():
            raise ValueError("pattern darf im Content-Modus nicht leer sein")

        max_results = max(1, int(max_results))
        timeout = float(timeout)
        if timeout <= 0:
            timeout = DEFAULT_RG_TIMEOUT
        context = max(0, int(context))

        cmd: List[str] = [
            rg,
            "--json",
            "--color=never",
            "--max-filesize", _RG_MAX_FILESIZE,
            "-e", pattern,
        ]
        if not case_sensitive:
            cmd.append("-i")
        if fixed_string:
            cmd.append("-F")
        if glob:
            cmd += ["-g", glob]
        if hidden:
            cmd.append("--hidden")
        if context:
            cmd += ["-C", str(context)]
        # SOTA: Explizites, absolutes Search-Target → rg meldet absolute Pfade
        # in der JSON-Ausgabe. (Ohne Target würde rg mit `cwd` implizit `.`
        # suchen und relative Pfade liefern — das würde das dokumentierte
        # Match-Schema "path ist absolut" verletzen.)
        cmd.append(str(root))

        creation_flags = (
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        start = time.perf_counter()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                timeout=timeout,
                creationflags=creation_flags,
            )
            stdout = proc.stdout or b""
            stderr = proc.stderr or b""
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            # Windows: Partial-Output (Kill + erneute communicate)
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            returncode = None
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # rg-Exit-Codes: 0 = Treffer, 1 = keine Treffer, >=2 = Fehler
        # (z.B. "regex parse error").
        if returncode is not None and returncode >= 2:
            err = stderr.decode("utf-8", errors="replace").strip()
            # Bekannte rg-Konvention: exit 2 mit "No files were searched"
            # (z.B. alle Dateien durch einen Glob-Filter ausgeschlossen)
            # ist ein legitimer Nulltreffer — kein Fehler.
            if "No files were searched" in err:
                logger.info(
                    "search_content_rg: keine Dateien nach Filter "
                    "(exit=%s) → 0 Treffer", returncode,
                )
                return {
                    "backend": "ripgrep",
                    "count": 0,
                    "truncated": False,
                    "timed_out": False,
                    "matches": [],
                    "elapsed_ms": elapsed_ms,
                    "error": None,
                    "error_class": None,
                }
            err_class = (
                "invalid_regex" if "regex parse error" in err else "search_error"
            )
            logger.warning(
                "search_content_rg: rg Fehler (exit=%s): %s",
                returncode, err[:500],
            )
            return {
                "backend": "ripgrep",
                "count": 0,
                "truncated": False,
                "timed_out": False,
                "matches": [],
                "elapsed_ms": elapsed_ms,
                "error": err or f"rg Exit-Code {returncode}",
                "error_class": err_class,
            }

        matches, truncated = self._parse_rg_json(
            stdout, max_results=max_results, context_hint=context
        )
        if timed_out:
            truncated = True  # Partial-Ergebnis immer explizit markieren
        logger.info(
            "search_content_rg: %d hits (truncated=%s, timed_out=%s, %d ms) "
            "for pattern '%r' in %s",
            len(matches), truncated, timed_out, elapsed_ms, pattern, root,
        )
        return {
            "backend": "ripgrep",
            "count": len(matches),
            "truncated": truncated,
            "timed_out": timed_out,
            "matches": matches,
            "elapsed_ms": elapsed_ms,
            "error": None,
            "error_class": None,
        }

    @staticmethod
    def _parse_rg_json(
        stdout: bytes,
        max_results: int,
        context_hint: int = 0,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """rg ``--json``-Events in strukturierte Hits umwandeln.

        Hit-Form: ``{path, line, text, context_before, context_after}``.
        Invalide/abgeschnittene Zeilen (Timeout-Kill) werden übersprungen.
        Returns ``(matches, truncated)``.
        """
        matches: List[Dict[str, Any]] = []
        truncated = False
        current_file: Optional[str] = None
        recent_ctx: List[str] = []          # ≤ context_hint Kontextzeilen vor Match
        pending_after: List[str] = []       # Kontextzeilen nach Match
        after_left = 0

        def _close_after() -> None:
            nonlocal pending_after, after_left
            if pending_after and matches:
                matches[-1]["context_after"] = list(pending_after)
            pending_after = []
            after_left = 0

        for raw_line in stdout.splitlines():
            if not raw_line.strip():
                continue
            try:
                ev = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue  # Partial-Zeile nach Timeout-Kill
            etype = ev.get("type")
            data = ev.get("data") or {}
            if etype == "begin":
                _close_after()
                current_file = (
                    data.get("absolute_path")
                    or (data.get("path") or {}).get("text")
                    or ""
                )
                recent_ctx = []
            elif etype == "match":
                _close_after()
                if len(matches) >= max_results:
                    truncated = True
                    break
                text = _rg_lines_text(data)
                line_no = data.get("line_number")
                path = str(data.get("absolute_path") or current_file or "")
                # rg gibt unter Windows evtl. Unix-Slashes → lexikalisch normalisieren
                if path:
                    path = os.path.normpath(path)
                matches.append({
                    "path": path,
                    "line": int(line_no) if line_no is not None else None,
                    "text": text,
                    "context_before": list(recent_ctx),
                    "context_after": [],
                })
                recent_ctx = []
                if context_hint:
                    after_left = context_hint
            elif etype == "context":
                if context_hint == 0:
                    continue  # kein Kontext gewünscht (auch: `-0`-Slice-Falle)
                text = _rg_lines_text(data)
                if after_left > 0:
                    pending_after.append(text)
                    after_left -= 1
                else:
                    recent_ctx.append(text)
                    if len(recent_ctx) > context_hint:
                        recent_ctx = recent_ctx[-context_hint:]
        _close_after()
        return matches, truncated

    def read_file_safe(
        self,
        user_path: str,
        max_chars: Optional[int] = None,
        encoding: str = "utf-8",
        offset: int = 1,
        limit: Optional[int] = None,
    ) -> Tuple[Path, str, bool, int, Dict[str, object]]:
        """SOTA: Sicheres Datei-Lesen mit Zeilenfenster + Char-Backstop.

        P0: Binary-Check, Size-Limit, Char-Limiter (Token-Budget-Schutz).
        P1 (2026-08-24): Zeilenbasierte Navigation (Claude Code Read-Modell):
        1-basiertes ``offset`` + ``limit`` (Default: ``DEFAULT_READ_LINE_LIMIT``).

        Args:
            user_path: Dateipfad.
            max_chars: Maximale Zeichen (Default: policy.max_read_chars).
            encoding: Text-Encoding.
            offset: Erste Zeile, 1-basiert (Default: 1). Werte < 1 werden
                auf 1 geklemmt; ``offset > total_lines`` liefert leeren
                Inhalt mit ehrlichen Metadaten (keine Exception).
            limit: Maximale Zeilen im Fenster (Default: DEFAULT_READ_LINE_LIMIT).
                Werte < 1 werden auf 1 geklemmt.

        Returns:
            Tuple von (resolved_path, content, was_truncated, total_chars, line_meta):

            - ``total_chars`` = Gesamtlänge der Datei nach Decoding, VOR
              Fenster/Trunkierung (damit die Metadaten im Tool-Result ehrlich sind).
            - ``line_meta`` = ``{``
                ``total_lines``:    int      — Zeilenzahl der ganzen Datei
                ``start_line``:     int      — erste angeforderte Zeile (1-basiert, geklemmt)
                ``end_line``:       int      — letzte gelieferte Zeile (0, wenn das Fenster
                                              leer ist: ``offset > total_lines`` oder leere Datei)
                ``has_more_lines``: bool     — True, wenn nach ``end_line`` noch Zeilen folgen
                ``next_offset``:    int      — nächster Start: ``max(start_line, end_line + 1)``
                                             (Voll-Read → ``total_lines + 1``; Offset über EOF → Offset selbst)
              ``}``

        Semantik:
        - Das Zeilenfenster ist die PRIMÄRE Navigation; ``splitlines(keepends=True)``
          erhält die Original-Zeilenenden (CRLF bleibt CRLF).
        - Das Char-Limit bleibt Sicherheits-BACKSTOP: ein Fenster mit sehr
          langen Zeilen (z. B. minifizierte Dateien) wird zusätzlich auf
          ``max_chars`` gekürzt → ``was_truncated=True``.
        """
        path = self.resolve(user_path, must_exist=True)

        if not path.is_file():
            raise PathSandboxError(f"Keine Datei: {path}")

        # SOTA: Binary-Check
        if _is_binary_file(path):
            raise PathSandboxError(
                f"Binärdatei kann nicht gelesen werden: {path.name} "
                f"(Type: {path.suffix or 'unknown'})"
            )

        char_limit = max_chars if max_chars is not None else self.policy.max_read_chars

        # Byte-Check vor Read
        size = path.stat().st_size
        if size > self.policy.max_read_bytes:
            raise PathSandboxError(
                f"Datei größer als Limit: {size} Bytes > {self.policy.max_read_bytes} Bytes"
            )

        with path.open("r", encoding=encoding, errors="replace") as f:
            raw_content = f.read()

        total_chars = len(raw_content)

        # P1: Zeilenfenster (1-based offset/limit) — primäre Navigation
        start = max(1, int(offset) if offset is not None else 1)
        line_limit = int(limit) if limit is not None else DEFAULT_READ_LINE_LIMIT
        line_limit = max(1, line_limit)

        lines = raw_content.splitlines(keepends=True)
        total_lines = len(lines)

        if start > total_lines:
            # Offset über EOF: leeres Fenster, keine weiteren Zeilen (keine Exception)
            end = 0
            has_more_lines = False
        else:
            end = min(start + line_limit - 1, total_lines)
            has_more_lines = end < total_lines

        content = "".join(lines[start - 1 : max(end, start - 1)])
        # Das Fenster endet mit dem Trennzeichen ZUR nächsten Zeile → dieses wird
        # nicht mitgeliefert (Fenster ≠ Datei-Präfix; ein schwebendes \n würde
        # fälschlich Dateiende andeuten).
        if content.endswith("\r\n"):
            content = content[:-2]
        elif content.endswith(("\n", "\r")):
            content = content[:-1]
        line_meta: Dict[str, object] = {
            "total_lines": total_lines,
            "start_line": start,
            "end_line": end,
            "has_more_lines": has_more_lines,
            # Vertrag (tests/test_file_reader_offset_limit.py): next_offset ist
            # IMMER int — bei Voll-Read total_lines+1, bei Offset über EOF der
            # Offset selbst. max() deckt beide Fälle ab (start ≤ end+1 normal).
            "next_offset": max(start, end + 1),
        }

        # P0: Char-Backstop (Context-Safety) — greift innerhalb des Zeilenfensters
        was_truncated = False
        if len(content) > char_limit:
            content = content[:char_limit]
            was_truncated = True
            logger.info(
                "read_file_safe: truncated %s window from %d to %d chars",
                path.name, total_chars, char_limit,
            )

        return path, content, was_truncated, total_chars, line_meta


__all__ = [
    "PathSandbox",
    "PathSandboxError",
    "SandboxPolicy",
    "_is_binary_file",
    "_compute_depth",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_SEARCH_RESULTS",
    "DEFAULT_MAX_READ_CHARS",
    "DEFAULT_READ_LINE_LIMIT",
]