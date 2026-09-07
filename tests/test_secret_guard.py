"""Unit-Tests fuer scripts/secret_guard.py (Fail-Closed Secret-/Key-Guard).

Deterministisch: kein Netzwerk, keine Modelle. Deckt ab:
  - Name-basiert: Private Keys, Keystores, .env, Credentials
  - Inhalts-basiert: Private-Key-Material, API-Keys, Secret-Zuweisungen
  - False-Positive-Schutz: Platzhalter, Hashes, .env.example, Loeschungen
  - CLI-Vertrag: Exit-Codes (0=sauber, 1=flagged, 2=Fehler), stdout-Pfade
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD = REPO_ROOT / "scripts" / "secret_guard.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
secret_guard = importlib.import_module("secret_guard")


def _run_guard(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD), *args],
        capture_output=True, text=True, timeout=180, cwd=REPO_ROOT,
    )


# --- Name-basierte Erkennung ---------------------------------------------

def test_name_private_key_file_flagged() -> None:
    assert secret_guard.scan_path("keys/id_ed25519", None, ()) == ["private-key-file"]
    assert secret_guard.scan_path("id_rsa", None, ()) == ["private-key-file"]


def test_name_public_key_not_flagged() -> None:
    assert secret_guard.scan_path("keys/id_ed25519.pub", None, ()) == []


def test_name_env_file_flagged() -> None:
    assert "env-file" in secret_guard.scan_path(".env", None, ())
    assert "env-file" in secret_guard.scan_path("config/.env.prod", None, ())


def test_name_env_example_not_flagged() -> None:
    assert secret_guard.scan_path(".env.example", None, ()) == []
    assert secret_guard.scan_path("config/.env.sample", None, ()) == []


def test_name_keystore_flagged() -> None:
    assert "keystore-file" in secret_guard.scan_path("certs/app.p12", None, ())
    assert "keystore-file" in secret_guard.scan_path("certs/app.pfx", None, ())
    assert "keystore-file" in secret_guard.scan_path("certs/app.jks", None, ())


def test_name_credentials_flagged() -> None:
    assert "credential-file" in secret_guard.scan_path(
        "gcp/service-account-prod.json", None, ())
    assert "credential-file" in secret_guard.scan_path("auth/credentials.json", None, ())


def test_name_netrc_and_authorized_keys_flagged() -> None:
    assert "credential-file" in secret_guard.scan_path(".netrc", None, ())
    assert "credential-file" in secret_guard.scan_path("ssh/authorized_keys", None, ())


def test_name_secrets_yaml_flagged_but_py_module_not() -> None:
    assert "secrets-file" in secret_guard.scan_path("config/secrets.yaml", None, ())
    # Python-Modulname (stdlib-Kollision) darf nicht blockiert werden:
    assert secret_guard.scan_path("utils/secrets.py", None, ()) == []


def test_name_settings_json_only_root_flagged() -> None:
    assert "local-settings" in secret_guard.scan_path("settings.json", None, ())
    assert secret_guard.scan_path("packages/web/settings.json", None, ()) == []


# --- Inhalts-basierte Erkennung -------------------------------------------

def test_content_private_key_material() -> None:
    # Marker aus "openssh-key-v1" (base64); bewusst via Konkat, damit
    # dieser Test selbst im Repo-Scan keinen Treffer erzeugt.
    marker = "b3BlbnNza" + "C1rZXktdjE" + "AAAA"
    # BEGIN/END-Zeilen ebenfalls gesplittet (siehe Marker-Hinweis oben).
    content = ("-----BEGIN OPEN" + "SSH PRIVATE KEY-----\n" + marker + "\n"
               "-----END OPEN" + "SSH PRIVATE KEY-----\n")
    reasons = secret_guard.scan_path("note.txt", content, ())
    assert "private-key-material" in reasons
    assert "openssh-private-key" in reasons


def test_content_known_api_key_formats() -> None:
    assert "aws-access-key-id" in secret_guard.scan_path(
        "a.txt", "key = AKIA" + "1234567890ABCDEF\n", ())
    assert "github-pat" in secret_guard.scan_path(
        "a.txt", "token: ghp_" + "a" * 40 + "\n", ())
    assert "openai-style-key" in secret_guard.scan_path(
        "a.txt", "sk-" + "a1B2c3D4e5F6g7H8i9J0kLmN" + "\n", ())
    assert "slack-token" in secret_guard.scan_path(
        "a.txt", "xox" + "b-1234567890-abc\n", ())


def test_content_secret_assignment_flagged() -> None:
    content = 'password = "abcdefghij' + '1234567890xyz"\n'
    assert "secret-assignment" in secret_guard.scan_path("cfg.txt", content, ())


def test_content_placeholder_not_flagged() -> None:
    content = 'password = "your-xxxxxx-placeholder"\n'
    assert secret_guard.scan_path("cfg.txt", content, ()) == []


def test_content_long_hex_hash_not_flagged() -> None:
    digest = "ab" * 32  # 64 Hex-Zeichen -> Hash, kein Secret
    assert "secret-assignment" not in secret_guard.scan_path(
        "cfg.txt", f"secret = '{digest}'\n", ())


def test_content_short_value_not_flagged() -> None:
    # Kurzere Werte als 20 Zeichen sind zu unsicher fuer ein Block-Gate.
    assert secret_guard.scan_path("cfg.txt", 'secret = "abc123"\n', ()) == []


def test_binary_content_skipped() -> None:
    content = "\x00" * 100
    assert secret_guard.scan_path("blob.bin", content, ()) == []


def test_allow_override_suppresses() -> None:
    assert secret_guard.scan_path("keys/id_ed25519", None, ()) != []
    assert secret_guard.scan_path("keys/id_ed25519", None, ("^keys/")) == []


# --- CLI-Vertrag ------------------------------------------------------------

def test_cli_clean_exit_zero(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("x = 1\n", encoding="utf-8")
    proc = _run_guard("--files", str(clean))
    assert proc.returncode == 0, proc.stderr


def test_cli_flagged_exit_one_and_stdout_contract(tmp_path: Path) -> None:
    bad = tmp_path / "id_ed25519"
    bad.write_text("irrelevant", encoding="utf-8")
    proc = _run_guard("--files", str(bad))
    assert proc.returncode == 1, proc.stderr
    # stdout-Vertrag: eine Pfad-Zeile pro geflaggter Datei (fuer Hooks/PS1).
    # Bei --files wird der Pfad posix-normalisiert zurueckgereicht
    # (Windows-Backslashes -> Forward-Slashes), damit Hooks ihn verarbeiten
    # koennen - der Aufrufer prueft daher die normalisierte Form.
    assert str(bad).replace("\\", "/") in proc.stdout.splitlines()


def test_cli_repo_tracked_clean() -> None:
    """Das aktuelle Repository muss saubere tracked Dateien haben
    (kein False-Positive-Alarm im Audit-Modus)."""
    proc = _run_guard("--tracked")
    assert proc.returncode == 0, proc.stderr