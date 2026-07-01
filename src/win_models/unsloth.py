from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .common import echo, ensure_dir, open_url, powershell, run
from .config import DEFAULT_MODEL_ROOT, DEFAULT_STUDIO_HOME, DEFAULT_STUDIO_PORT


LOCAL_ZIP_MARKER = "UNSLOTH_LOCAL_ZIP_SHIM"
CHAT_TEMPLATE_OVERRIDE_SHIM_MARKER = "UNSLOTH_CHAT_TEMPLATE_OVERRIDE_SHIM"
CLI_API_KEY_REUSE_SHIM_MARKER = "UNSLOTH_CLI_API_KEY_REUSE_SHIM"
OPENAI_REASONING_PASSTHROUGH_SHIM_MARKER = "UNSLOTH_OPENAI_REASONING_PASSTHROUGH_SHIM"
EMBEDDING_EXTRA_ARGS_SHIM_MARKER = "UNSLOTH_EMBEDDING_EXTRA_ARGS_SHIM"
DEFAULT_MCP_CONFIG = (
    Path(__file__).resolve().parents[1] / "unsloth" / "mcp-servers.json"
)


def embedding_llama_args(model: str) -> list[str]:
    model_id = model.replace("\\", "/").lower()
    if "embed" not in model_id and "embedding" not in model_id:
        return []
    return ["--embedding", "--pooling", "last"]


def resolve_unsloth_exe(studio_home: Path) -> Path:
    exe = studio_home / "unsloth_studio" / "Scripts" / "unsloth.exe"
    if not exe.exists():
        raise FileNotFoundError(
            f"Unsloth Studio is not installed at {studio_home}.\n"
            "Install it first from https://unsloth.ai so the venv exists at "
            "<StudioHome>\\unsloth_studio."
        )
    return exe


def resolve_studio_python(studio_home: Path) -> Path:
    python = studio_home / "unsloth_studio" / "Scripts" / "python.exe"
    if not python.exists():
        raise FileNotFoundError(f"Missing venv python at {python}")
    return python


def studio_package_dir(studio_home: Path) -> Path:
    return studio_home / "unsloth_studio" / "Lib" / "site-packages" / "studio"


def unsloth_cli_studio_command(studio_home: Path) -> Path:
    return (
        studio_home
        / "unsloth_studio"
        / "Lib"
        / "site-packages"
        / "unsloth_cli"
        / "commands"
        / "studio.py"
    )


def apply_embedding_extra_args_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "backend" / "core" / "inference" / "llama_server_args.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if EMBEDDING_EXTRA_ARGS_SHIM_MARKER in text:
        echo("  embedding extra-args shim already present")
        return

    old = (
        '    # Server-mode flips: --embedding / --rerank restrict llama-server to\n'
        "    # those endpoints, breaking Studio's /v1/chat/completions hop.\n"
        '    frozenset({"--embedding", "--embeddings"}),\n'
    )
    new = (
        "    # UNSLOTH_EMBEDDING_EXTRA_ARGS_SHIM: win-models may intentionally run\n"
        "    # Studio with a dedicated embedding model, in which case /v1/chat/\n"
        "    # completions is irrelevant and the backend must allow --embedding.\n"
    )
    if old not in text:
        raise RuntimeError(
            f"Could not find the embedding denylist anchor in {target}; the Studio backend changed."
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    echo("  applied embedding extra-args shim to backend.core.inference.llama_server_args")


def apply_llama_local_zip_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "install_llama_prebuilt.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if LOCAL_ZIP_MARKER in text or "using local asset" in text:
        echo("  llama local-zip shim already present")
        return

    anchor = "def download_file(url: str, destination: Path) -> None:\n    destination.parent.mkdir(parents = True, exist_ok = True)\n"
    if anchor not in text:
        raise RuntimeError(f"Could not find the download_file() anchor in {target}; the installer changed.")

    shim = """def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents = True, exist_ok = True)
    # UNSLOTH_LOCAL_ZIP_SHIM: reuse a manually-downloaded asset of the same name
    # (GitHub throttles the large release zips). Searches UNSLOTH_LLAMA_LOCAL_DIR
    # (os.pathsep-separated) then ~/Downloads. sha256/validation still run.
    _shim_dirs = []
    _shim_env = os.environ.get("UNSLOTH_LLAMA_LOCAL_DIR")
    if _shim_env:
        _shim_dirs.extend(p for p in _shim_env.split(os.pathsep) if p.strip())
    _shim_home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if _shim_home:
        _shim_dirs.append(os.path.join(_shim_home, "Downloads"))
    for _shim_dir in _shim_dirs:
        try:
            _shim_cand = Path(_shim_dir) / destination.name
            if _shim_cand.is_file() and _shim_cand.stat().st_size > 0:
                log(f"using local asset {_shim_cand} instead of downloading {url}")
                shutil.copyfile(_shim_cand, destination)
                return
        except OSError:
            continue
"""
    target.write_text(text.replace(anchor, shim + "\n", 1), encoding="utf-8", newline="\n")
    echo("  applied llama local-zip shim to install_llama_prebuilt.py")


def apply_chat_template_override_shim(studio_home: Path) -> None:
    target = unsloth_cli_studio_command(studio_home)
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if CHAT_TEMPLATE_OVERRIDE_SHIM_MARKER in text:
        echo("  chat-template override shim already present")
        return

    signature_anchor = (
        "    llama_extra_args: Optional[List[str]] = None,\n"
        "    timeout: int = 600,\n"
    )
    if signature_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http signature anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        signature_anchor,
        (
            "    llama_extra_args: Optional[List[str]] = None,\n"
            "    chat_template_override: Optional[str] = None,\n"
            "    timeout: int = 600,\n"
        ),
        1,
    )

    payload_anchor = (
        "    if llama_extra_args:\n"
        "        payload[\"llama_extra_args\"] = list(llama_extra_args)\n\n"
    )
    if payload_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http payload anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        payload_anchor,
        (
            payload_anchor
            + "    if chat_template_override:\n"
            + "        payload[\"chat_template_override\"] = chat_template_override\n\n"
        ),
        1,
    )

    load_anchor = None
    load_indent = ""
    for candidate_indent in ("    ", "        "):
        candidate = (
            f"{candidate_indent}# 5. Load model via HTTP.\n"
            f"{candidate_indent}if not silent:\n"
            f"{candidate_indent}    typer.echo(f\"Loading model: {{model}}...\")\n"
            f"{candidate_indent}try:\n"
        )
        if candidate in text:
            load_anchor = candidate
            load_indent = candidate_indent
            break
    if load_anchor is None:
        raise RuntimeError(
            f"Could not find Studio run load anchor in {target}; the Studio CLI changed."
        )
    template_read = (
        f"{load_indent}# UNSLOTH_CHAT_TEMPLATE_OVERRIDE_SHIM: allow win-models to feed a\n"
        f"{load_indent}# repo-pinned Jinja template through Studio's first-class load field.\n"
        f"{load_indent}chat_template_override = None\n"
        f"{load_indent}chat_template_file = os.environ.get(\"UNSLOTH_CHAT_TEMPLATE_FILE\")\n"
        f"{load_indent}if chat_template_file:\n"
        f"{load_indent}    try:\n"
        f"{load_indent}        chat_template_override = Path(chat_template_file).read_text(encoding=\"utf-8\")\n"
        f"{load_indent}    except OSError as exc:\n"
        f"{load_indent}        typer.echo(f\"Error: could not read UNSLOTH_CHAT_TEMPLATE_FILE={{chat_template_file}}: {{exc}}\", err=True)\n"
        f"{load_indent}        raise typer.Exit(1)\n"
        f"{load_indent}    if not silent:\n"
        f"{load_indent}        typer.echo(f\"Using chat template override: {{chat_template_file}}\")\n\n"
    )
    text = text.replace(load_anchor, template_read + load_anchor, 1)

    call_anchor = "                llama_extra_args = extra_llama_args,\n"
    if call_anchor not in text:
        call_anchor = "            llama_extra_args = extra_llama_args,\n"
    if call_anchor not in text:
        raise RuntimeError(
            f"Could not find _load_model_via_http call anchor in {target}; "
            "the Studio CLI changed."
        )
    text = text.replace(
        call_anchor,
        call_anchor + call_anchor.replace(
            "llama_extra_args = extra_llama_args",
            "chat_template_override = chat_template_override",
        ),
        1,
    )

    target.write_text(text, encoding="utf-8", newline="\n")
    echo("  applied chat-template override shim to unsloth_cli.commands.studio")


def apply_cli_api_key_reuse_shim(studio_home: Path) -> None:
    target = unsloth_cli_studio_command(studio_home)
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if CLI_API_KEY_REUSE_SHIM_MARKER in text:
        echo("  CLI API-key reuse shim already present")
        return

    anchor = '''def _create_api_key_inprocess(name: str) -> str:
    """Create an API key via direct storage call (no HTTP needed).

    Bypasses the ``must_change_password`` gate that blocks HTTP
    ``POST /api/auth/api-keys`` on fresh installs.  Safe because the
    CLI already has filesystem access to ``~/.unsloth/studio``.
    """
    storage = _load_backend_auth_storage()

    raw_key, _row = storage.create_api_key(
        username = storage.DEFAULT_ADMIN_USERNAME,
        name = name,
    )
    return raw_key
'''
    if anchor not in text:
        raise RuntimeError(
            f"Could not find _create_api_key_inprocess anchor in {target}; "
            "the Studio CLI changed."
        )

    replacement = '''def _create_api_key_inprocess(name: str) -> str:
    """Create or reuse an API key via direct storage call (no HTTP needed).

    Bypasses the ``must_change_password`` gate that blocks HTTP
    ``POST /api/auth/api-keys`` on fresh installs.  Safe because the
    CLI already has filesystem access to ``~/.unsloth/studio``.
    """
    storage = _load_backend_auth_storage()

    # UNSLOTH_CLI_API_KEY_REUSE_SHIM: upstream creates a fresh visible
    # key named "cli" on every `unsloth studio run`. Keep custom names
    # upstream-compatible, but reuse one local raw key for the default.
    reuse_enabled = os.environ.get("UNSLOTH_REUSE_CLI_API_KEY", "1").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    if name != "cli" or not reuse_enabled:
        raw_key, _row = storage.create_api_key(
            username = storage.DEFAULT_ADMIN_USERNAME,
            name = name,
        )
        return raw_key

    key_path = STUDIO_HOME / "auth" / "cli-api-key.txt"
    try:
        raw_key = key_path.read_text(encoding = "utf-8").strip()
    except OSError:
        raw_key = ""
    if raw_key and storage.validate_api_key(raw_key) == storage.DEFAULT_ADMIN_USERNAME:
        return raw_key

    raw_key = storage.API_KEY_PREFIX + secrets.token_hex(16)
    key_hash = storage._pbkdf2_api_key(raw_key)
    key_prefix = raw_key[len(storage.API_KEY_PREFIX) : len(storage.API_KEY_PREFIX) + 8]
    now = datetime.now(timezone.utc).isoformat()

    conn = storage.get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO api_keys (
                    username, key_prefix, key_hash, name,
                    created_at, expires_at, is_internal
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    storage.DEFAULT_ADMIN_USERNAME,
                    key_prefix,
                    key_hash,
                    name,
                    now,
                    None,
                    0,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE api_keys
                   SET username = ?, name = ?, is_active = 1, expires_at = NULL
                 WHERE id = ?
                """,
                (storage.DEFAULT_ADMIN_USERNAME, name, existing["id"]),
            )
        conn.commit()
    finally:
        conn.close()

    _write_auth_secret(key_path, raw_key)
    return raw_key
'''
    target.write_text(text.replace(anchor, replacement, 1), encoding="utf-8", newline="\n")
    echo("  applied CLI API-key reuse shim to unsloth_cli.commands.studio")


def apply_openai_reasoning_passthrough_shim(studio_home: Path) -> None:
    target = studio_package_dir(studio_home) / "backend" / "routes" / "inference.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing {target}")
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    if OPENAI_REASONING_PASSTHROUGH_SHIM_MARKER in text:
        echo("  OpenAI reasoning passthrough shim already present")
        return

    helper_anchor = '''def _chat_content_chunk(completion_id, created, model_name, text) -> str:
    """A content-delta chunk carrying ``text``."""
    return _chat_chunk_sse(
        completion_id,
        created,
        model_name,
        delta = ChoiceDelta(content = text),
        finish_reason = None,
    )


def _chat_final_chunk(completion_id, created, model_name, finish_reason) -> str:
'''
    helper_replacement = '''def _chat_content_chunk(completion_id, created, model_name, text) -> str:
    """A content-delta chunk carrying ``text``."""
    return _chat_chunk_sse(
        completion_id,
        created,
        model_name,
        delta = ChoiceDelta(content = text),
        finish_reason = None,
    )


def _split_reasoning_tags_for_openai(text: str) -> tuple[str, str]:
    # UNSLOTH_OPENAI_REASONING_PASSTHROUGH_SHIM: Studio wraps llama.cpp
    # reasoning_content as <think>...</think> for its UI. Split it back into
    # OpenAI-compatible content/reasoning streams at the API boundary.
    start_tag = "<think>"
    end_tag = "</think>"
    start = text.find(start_tag)
    if start < 0:
        return text, ""
    before = text[:start]
    body_start = start + len(start_tag)
    end = text.find(end_tag, body_start)
    if end < 0:
        return before, text[body_start:]
    return before + text[end + len(end_tag) :], text[body_start:end]


def _chat_reasoning_chunk(completion_id, created, model_name, text) -> str:
    obj = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": {"reasoning_content": text},
                "finish_reason": None,
            }
        ],
    }
    return "data: " + json.dumps(obj, separators = (",", ":")) + "\\n\\n"


def _chat_final_chunk(completion_id, created, model_name, finish_reason) -> str:
'''
    if helper_anchor not in text:
        raise RuntimeError(f"Could not find _chat_content_chunk anchor in {target}; Studio changed.")
    text = text.replace(helper_anchor, helper_replacement, 1)

    replacements = [
        (
            '                    prev_text = ""\n'
            '                    _stream_usage = None\n',
            '                    prev_text = ""\n'
            '                    prev_reasoning_text = ""\n'
            '                    _stream_usage = None\n',
            "GGUF tool stream state",
        ),
        (
            '                            if not event["text"]:\n'
            '                                prev_text = ""\n',
            '                            if not event["text"]:\n'
            '                                prev_text = ""\n'
            '                                prev_reasoning_text = ""\n',
            "GGUF tool status reset",
        ),
        (
            '                            if event["type"] == "tool_start":\n'
            '                                prev_text = ""\n',
            '                            if event["type"] == "tool_start":\n'
            '                                prev_text = ""\n'
            '                                prev_reasoning_text = ""\n',
            "GGUF tool_start reset",
        ),
        (
            '                        new_text = clean_cumulative[len(prev_text) :]\n'
            '                        prev_text = clean_cumulative\n'
            '                        if not new_text:\n'
            '                            continue\n'
            '                        api_monitor.append_reply(monitor_id, new_text)\n'
            '                        yield _chat_content_chunk(completion_id, created, model_name, new_text)\n',
            '                        content_cumulative, reasoning_cumulative = _split_reasoning_tags_for_openai(\n'
            '                            clean_cumulative\n'
            '                        )\n'
            '                        new_reasoning = reasoning_cumulative[len(prev_reasoning_text) :]\n'
            '                        prev_reasoning_text = reasoning_cumulative\n'
            '                        if new_reasoning:\n'
            '                            yield _chat_reasoning_chunk(\n'
            '                                completion_id, created, model_name, new_reasoning\n'
            '                            )\n'
            '                        new_text = content_cumulative[len(prev_text) :]\n'
            '                        prev_text = content_cumulative\n'
            '                        if not new_text:\n'
            '                            continue\n'
            '                        api_monitor.append_reply(monitor_id, new_text)\n'
            '                        yield _chat_content_chunk(completion_id, created, model_name, new_text)\n',
            "GGUF tool content split",
        ),
        (
            '                    prev_text = ""\n'
            '                    _stream_usage = None\n',
            '                    prev_text = ""\n'
            '                    prev_reasoning_text = ""\n'
            '                    _stream_usage = None\n',
            "GGUF stream state",
        ),
        (
            '                        new_text = cumulative[len(prev_text) :]\n'
            '                        prev_text = cumulative\n'
            '                        if not new_text:\n'
            '                            continue\n'
            '                        api_monitor.append_reply(monitor_id, new_text)\n'
            '                        yield _chat_content_chunk(completion_id, created, model_name, new_text)\n',
            '                        content_cumulative, reasoning_cumulative = _split_reasoning_tags_for_openai(\n'
            '                            cumulative\n'
            '                        )\n'
            '                        new_reasoning = reasoning_cumulative[len(prev_reasoning_text) :]\n'
            '                        prev_reasoning_text = reasoning_cumulative\n'
            '                        if new_reasoning:\n'
            '                            yield _chat_reasoning_chunk(\n'
            '                                completion_id, created, model_name, new_reasoning\n'
            '                            )\n'
            '                        new_text = content_cumulative[len(prev_text) :]\n'
            '                        prev_text = content_cumulative\n'
            '                        if not new_text:\n'
            '                            continue\n'
            '                        api_monitor.append_reply(monitor_id, new_text)\n'
            '                        yield _chat_content_chunk(completion_id, created, model_name, new_text)\n',
            "GGUF stream content split",
        ),
        (
            '                        full_text = token\n'
            '\n'
            '                    _choices.append(\n',
            '                        full_text = token\n'
            '\n'
            '                    full_text, _reasoning_text = _split_reasoning_tags_for_openai(full_text)\n'
            '                    _choices.append(\n',
            "GGUF non-stream content split",
        ),
        (
            '                        full_text = _strip_tool_xml_for_display(\n'
            '                            event.get("text", ""),\n'
            '                            auto_heal_tool_calls = _sf_auto_heal_tool_calls,\n'
            '                        )\n',
            '                        full_text = _strip_tool_xml_for_display(\n'
            '                            event.get("text", ""),\n'
            '                            auto_heal_tool_calls = _sf_auto_heal_tool_calls,\n'
            '                        )\n'
            '                        full_text, _reasoning_text = _split_reasoning_tags_for_openai(full_text)\n',
            "tool non-stream content split",
        ),
    ]
    for old, new, label in replacements:
        if old not in text:
            raise RuntimeError(f"Could not find {label} anchor in {target}; Studio changed.")
        text = text.replace(old, new, 1)

    target.write_text(text, encoding="utf-8", newline="\n")
    echo("  applied OpenAI reasoning passthrough shim to backend.routes.inference")


def find_llama_zips(zip_dir: Path, release_tag: str, runtime: str) -> tuple[Path, Path]:
    bin_name = f"llama-{release_tag}-bin-win-cuda-{runtime}-x64.zip"
    cudart_name = f"cudart-llama-bin-win-cuda-{runtime}-x64.zip"
    bin_zip = zip_dir / bin_name
    cudart_zip = zip_dir / cudart_name
    missing = [name for name, path in ((bin_name, bin_zip), (cudart_name, cudart_zip)) if not path.exists()]
    if missing:
        base = f"https://github.com/ggml-org/llama.cpp/releases/download/{release_tag}"
        examples = "\n".join(f"  {base}/{name}" for name in missing)
        raise FileNotFoundError(
            f"Missing prebuilt zip(s) in {zip_dir}:\n"
            f"{chr(10).join(missing)}\n"
            "Download them with a resumable downloader, e.g.:\n"
            f"{examples}\n"
            "Then re-run, or pass --zip-dir to point at the folder that holds them."
        )
    return bin_zip, cudart_zip


def register_scan_folder(studio_home: Path, scan_path: Path) -> None:
    if not scan_path.exists():
        raise FileNotFoundError(f"Scan folder does not exist: {scan_path}")
    python = resolve_studio_python(studio_home)
    backend = studio_package_dir(studio_home) / "backend"
    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(studio_home)
    code = (
        "import sys\n"
        f"sys.path.insert(0, r'{backend}')\n"
        "from storage.studio_db import add_scan_folder, list_scan_folders\n"
        f"add_scan_folder(r'{scan_path}')\n"
        "print('scan folders:', [f['path'] for f in list_scan_folders()])\n"
    )
    completed = subprocess.run([str(python), "-c", code], env=env)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def install_unsloth_wrapper(studio_home: Path) -> None:
    user_profile = Path(os.environ.get("USERPROFILE") or Path.home())
    bin_dir = user_profile / ".local" / "bin"
    ensure_dir(bin_dir)
    cmd = (
        "@echo off\r\n"
        "rem unsloth launcher wrapper generated by win-models\r\n"
        "rem Sets UNSLOTH_STUDIO_HOME and forwards all args to the real unsloth CLI.\r\n"
        f'if not defined UNSLOTH_STUDIO_HOME set "UNSLOTH_STUDIO_HOME={studio_home}"\r\n'
        '"%UNSLOTH_STUDIO_HOME%\\unsloth_studio\\Scripts\\unsloth.exe" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )
    (bin_dir / "unsloth.cmd").write_text(cmd, encoding="ascii", newline="")
    echo(f"  wrote {bin_dir}\\unsloth.cmd (ensure {bin_dir} is on PATH)")


def _resolve_secret_ref(secret: dict[str, Any]) -> str:
    env_name = secret.get("env")
    if env_name:
        value = os.environ.get(str(env_name))
        if value:
            return value

    op_ref = secret.get("op")
    if op_ref:
        completed = subprocess.run(
            ["op", "read", str(op_ref)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "op read failed"
            raise RuntimeError(f"Could not resolve 1Password secret {op_ref}: {detail}")
        value = completed.stdout.strip()
        if not value:
            raise RuntimeError(f"1Password secret {op_ref} resolved to an empty value")
        return value

    value = secret.get("value")
    if value:
        return str(value)
    raise ValueError("secret must define one of: env, op, value")


def _headers_from_mcp_server_config(server: dict[str, Any]) -> dict[str, str] | None:
    headers = {str(k): str(v) for k, v in (server.get("headers") or {}).items()}
    auth = server.get("authorization")
    if isinstance(auth, dict):
        auth_type = str(auth.get("type", "")).lower()
        if auth_type == "bearer":
            token = _resolve_secret_ref(auth)
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type:
            raise ValueError(f"Unsupported authorization type: {auth_type}")
    return headers or None


def _ensure_mcp_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id TEXT NOT NULL PRIMARY KEY,
            display_name TEXT NOT NULL,
            url TEXT NOT NULL,
            headers_json TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            use_oauth INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(mcp_servers)").fetchall()}
    if "use_oauth" not in cols:
        conn.execute(
            "ALTER TABLE mcp_servers ADD COLUMN use_oauth INTEGER NOT NULL DEFAULT 0"
        )


def _sync_mcp_config(
    studio_home: Path,
    config_path: Path,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if not config_path.exists():
        raise FileNotFoundError(f"MCP config not found: {config_path}")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    servers = payload.get("servers")
    if not isinstance(servers, list):
        raise ValueError(f"{config_path} must contain a top-level 'servers' list")

    rows: list[dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict):
            raise ValueError("Each MCP server entry must be an object")
        server_id = str(server.get("id", "")).strip()
        display_name = str(server.get("display_name", "")).strip()
        url = str(server.get("url", "")).strip()
        if not server_id or not display_name or not url:
            raise ValueError("Each MCP server must define id, display_name, and url")
        headers = None if dry_run else _headers_from_mcp_server_config(server)
        rows.append(
            {
                "id": server_id,
                "display_name": display_name,
                "url": url,
                "headers_json": (
                    json.dumps(headers, separators=(",", ":")) if headers else None
                ),
                "is_enabled": bool(server.get("is_enabled", True)),
                "use_oauth": bool(server.get("use_oauth", False)),
                "has_headers": bool(
                    server.get("headers") or server.get("authorization")
                ),
            }
        )

    if dry_run:
        return rows

    db_path = studio_home / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_mcp_schema(conn)
        now_sql = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM mcp_servers WHERE id = ?", (row["id"],)
            ).fetchone()
            if exists:
                conn.execute(
                    f"""
                    UPDATE mcp_servers
                       SET display_name = ?,
                           url = ?,
                           headers_json = ?,
                           is_enabled = ?,
                           use_oauth = ?,
                           updated_at = {now_sql}
                     WHERE id = ?
                    """,
                    (
                        row["display_name"],
                        row["url"],
                        row["headers_json"],
                        int(row["is_enabled"]),
                        int(row["use_oauth"]),
                        row["id"],
                    ),
                )
            else:
                conn.execute(
                    f"""
                    INSERT INTO mcp_servers
                        (id, display_name, url, headers_json,
                         is_enabled, use_oauth, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, {now_sql}, {now_sql})
                    """,
                    (
                        row["id"],
                        row["display_name"],
                        row["url"],
                        row["headers_json"],
                        int(row["is_enabled"]),
                        int(row["use_oauth"]),
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    return rows


def sync_mcp(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    config_path = Path(args.config)
    rows = _sync_mcp_config(studio_home, config_path, dry_run=args.dry_run)
    action = "Would sync" if args.dry_run else "Synced"
    echo(f"{action} {len(rows)} MCP server(s) from {config_path}:")
    for row in rows:
        enabled = "enabled" if row["is_enabled"] else "disabled"
        auth = "headers" if row["has_headers"] else "no headers"
        echo(f"  {row['id']}: {row['display_name']} ({enabled}, {auth}) -> {row['url']}")


def setup(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    model_root = Path(args.model_root)
    zip_dir = Path(args.zip_dir)
    echo("=== Unsloth Studio setup ===")
    echo(f"StudioHome={studio_home}  ModelRoot={model_root}  tag={args.release_tag}  cuda={args.runtime}")

    unsloth = resolve_unsloth_exe(studio_home)
    python = resolve_studio_python(studio_home)
    llama_dir = studio_home / "llama.cpp"

    echo("\n[1/6] Patching llama.cpp prebuilt installer (local-zip shim)...")
    apply_llama_local_zip_shim(studio_home)
    apply_chat_template_override_shim(studio_home)
    apply_cli_api_key_reuse_shim(studio_home)
    apply_openai_reasoning_passthrough_shim(studio_home)
    apply_embedding_extra_args_shim(studio_home)

    echo(f"\n[2/6] Locating prebuilt zips in {zip_dir} ...")
    bin_zip, cudart_zip = find_llama_zips(zip_dir, args.release_tag, args.runtime)
    echo(f"  bin:    {bin_zip}")
    echo(f"  cudart: {cudart_zip}")

    echo(f"\n[3/6] Installing llama.cpp {args.release_tag} (cuda-{args.runtime}) from local zips...")
    staging = studio_home / ".staging"
    if staging.exists():
        for child in staging.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(studio_home)
    env["UNSLOTH_LLAMA_LOCAL_DIR"] = str(zip_dir)
    env["UNSLOTH_LLAMA_RELEASE_TAG"] = args.release_tag
    env["UNSLOTH_VERBOSE"] = "1"
    run(
        [
            python,
            studio_package_dir(studio_home) / "install_llama_prebuilt.py",
            "--install-dir",
            llama_dir,
            "--llama-tag",
            args.release_tag,
            "--published-repo",
            "ggml-org/llama.cpp",
            "--published-release-tag",
            args.release_tag,
        ],
        env=env,
    )

    echo("\n[4/6] Marking install Studio-owned and running 'unsloth studio setup'...")
    ensure_dir(llama_dir)
    (llama_dir / ".unsloth-studio-owned").write_text("", encoding="utf-8")
    if args.skip_base:
        env["SKIP_STUDIO_BASE"] = "1"
    else:
        env.pop("SKIP_STUDIO_BASE", None)
    run([unsloth, "studio", "setup", "--verbose"], env=env)
    apply_chat_template_override_shim(studio_home)
    apply_cli_api_key_reuse_shim(studio_home)
    apply_openai_reasoning_passthrough_shim(studio_home)
    apply_embedding_extra_args_shim(studio_home)

    if args.register_model_root:
        echo(f"\n[5/6] Registering model folder {model_root} as a Studio scan folder...")
        register_scan_folder(studio_home, model_root)
    else:
        echo(
            "\n[5/6] Skipping model-root scan-folder registration; "
            "HF cache repos are discovered as downloaded models."
        )

    echo("\n[6/6] Installing unsloth.cmd launcher...")
    install_unsloth_wrapper(studio_home)
    echo("\nDone. Launch the web UI with: just unsloth serve")


def serve(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    unsloth = resolve_unsloth_exe(studio_home)
    apply_chat_template_override_shim(studio_home)
    apply_cli_api_key_reuse_shim(studio_home)
    apply_openai_reasoning_passthrough_shim(studio_home)
    apply_embedding_extra_args_shim(studio_home)
    if args.parallel < 1:
        raise ValueError("--parallel must be at least 1")
    bind_host = "0.0.0.0" if args.lan else args.host
    model = args.model
    model_path = Path(model)
    is_local_model = (
        model_path.is_absolute()
        or model.startswith(".")
        or "\\" in model
        or model.lower().endswith(".gguf")
    )
    if is_local_model and not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")

    env = os.environ.copy()
    env["UNSLOTH_STUDIO_HOME"] = str(studio_home)
    env["LOG_LEVEL"] = args.log_level
    env["ENVIRONMENT_TYPE"] = "production"
    if args.hf_cache_dir:
        hf_cache_dir = Path(args.hf_cache_dir)
        hf_cache_dir.mkdir(parents=True, exist_ok=True)
        env["HUGGINGFACE_HUB_CACHE"] = str(hf_cache_dir)
        env["HF_HUB_CACHE"] = str(hf_cache_dir)
    if args.chat_template_file:
        chat_template_file = Path(args.chat_template_file).resolve()
        if not chat_template_file.is_file():
            raise FileNotFoundError(f"Chat template file not found: {chat_template_file}")
        env["UNSLOTH_CHAT_TEMPLATE_FILE"] = str(chat_template_file)

    port_check = powershell(f"Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue", check=False)
    if port_check.strip():
        echo(f"A server is already listening on port {args.port}.")
        if args.open:
            open_url(f"http://localhost:{args.port}")
        return

    command = [
        unsloth,
        "studio",
        "run",
        "--model",
        model,
        "--max-seq-length",
        str(args.max_seq_length),
        "--parallel",
        str(args.parallel),
        "-H",
        bind_host,
        "-p",
        str(args.port),
        "--yes",
    ]
    if args.max_seq_length > 0:
        command.extend(["-c", str(args.max_seq_length)])
    if args.cache_type_kv:
        command.extend(
            [
                "--cache-type-k",
                args.cache_type_kv,
                "--cache-type-v",
                args.cache_type_kv,
            ]
        )
    if args.reasoning_format:
        command.extend(["--reasoning-format", args.reasoning_format])
    embed_args = embedding_llama_args(model)
    if embed_args:
        command.extend(embed_args)
    if args.enable_tools:
        command.append("--enable-tools")
    else:
        command.append("--disable-tools")
    if args.verbose_llama:
        command.append("--verbose")

    echo(f"Starting Unsloth Studio on http://{bind_host}:{args.port}  (LOG_LEVEL={args.log_level})")
    echo(f"Context length={args.max_seq_length}; parallel slots={args.parallel}; per-slot context={args.max_seq_length // args.parallel}")
    if args.max_seq_length > 0:
        echo(f"llama-server context override: -c {args.max_seq_length}")
    if args.cache_type_kv:
        echo(f"llama-server KV cache override: --cache-type-k {args.cache_type_kv} --cache-type-v {args.cache_type_kv}")
    if args.reasoning_format:
        echo(f"llama-server reasoning parser: --reasoning-format {args.reasoning_format}")
    if embed_args:
        echo(f"llama-server embedding mode: {' '.join(embed_args)}")
    if args.chat_template_file:
        echo(f"Studio chat template override: {Path(args.chat_template_file).resolve()}")
    echo("Backend output will stream in this terminal. Press Ctrl+C to stop.")
    echo(f"llama-server log: {studio_home}\\logs\\llama-server\\llama-*-port-*.log")
    if args.lan:
        echo("Bound to 0.0.0.0; open via localhost on this machine for browser secure-context features.")
    if args.open:
        echo(f"Opening http://localhost:{args.port}; refresh once Studio finishes starting if needed.")
        open_url(f"http://localhost:{args.port}")
    run(command, env=env)


def stop(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    pid_file = studio_home / "studio.pid"
    pids: set[int] = set()
    command = (
        f"Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue "
        "| ForEach-Object { $_.OwningProcess }"
    )
    for line in powershell(command, check=False).splitlines():
        line = line.strip()
        if line.isdigit():
            pids.add(int(line))
    if pid_file.exists():
        text = pid_file.read_text(encoding="utf-8", errors="ignore").strip()
        if text.isdigit():
            pids.add(int(text))

    if not pids:
        echo(f"No Unsloth Studio server found on port {args.port}.")
        if pid_file.exists():
            pid_file.unlink()
            echo("Removed stale studio.pid.")
        return

    for pid in sorted(pids):
        subprocess.run(["taskkill.exe", "/PID", str(pid), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    still_listening = powershell(command, check=False).strip()
    if still_listening:
        echo("Forcing shutdown...")
        for pid in sorted(pids):
            subprocess.run(["taskkill.exe", "/F", "/PID", str(pid), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if pid_file.exists():
        pid_file.unlink()
    echo(f"Stopped Unsloth Studio (port {args.port}).")


def register(args: argparse.Namespace) -> None:
    studio_home = Path(args.studio_home)
    resolve_unsloth_exe(studio_home)
    register_scan_folder(studio_home, Path(args.path))
    echo(f"Registered {args.path}. Refresh the Models page in the web UI to see it.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="win-models unsloth")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    p.add_argument("--zip-dir", default=str(Path.home() / "Downloads"))
    p.add_argument("--release-tag", default="b9821")
    p.add_argument("--runtime", default="13.3")
    p.add_argument("--skip-base", action="store_true")
    p.add_argument("--register-model-root", action="store_true")
    p.set_defaults(func=setup)

    p = sub.add_parser("sync-mcp")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--config", default=str(DEFAULT_MCP_CONFIG))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=sync_mcp)

    p = sub.add_parser("serve")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--hf-cache-dir", default="")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--model", required=True)
    p.add_argument("--max-seq-length", type=int, required=True)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--cache-type-kv", default="q8_0")
    p.add_argument("--reasoning-format", choices=("none", "deepseek", "deepseek-legacy"), default="deepseek")
    p.add_argument("--chat-template-file", default="")
    p.add_argument("--port", type=int, default=DEFAULT_STUDIO_PORT)
    p.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    p.add_argument("--lan", action="store_true")
    p.add_argument("--open", action="store_true")
    p.add_argument("--enable-tools", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--verbose-llama", action="store_true")
    p.set_defaults(func=serve)

    p = sub.add_parser("stop")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.add_argument("--port", type=int, default=DEFAULT_STUDIO_PORT)
    p.set_defaults(func=stop)

    p = sub.add_parser("register")
    p.add_argument("path")
    p.add_argument("--studio-home", default=str(DEFAULT_STUDIO_HOME))
    p.set_defaults(func=register)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
