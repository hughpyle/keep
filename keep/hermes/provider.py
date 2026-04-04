"""Hermes MemoryProvider backed by an in-process Keeper.

Duck-typed to the Hermes MemoryProvider protocol — no import from hermes.
Reads use the Keeper directly (no RPC).  Writes go through the Keeper too
(fast synchronous part), while the daemon handles background processing
(embeddings, summaries).

Keeper lifetime: created once in initialize(), held for the session,
closed in shutdown().  If an unrecoverable error occurs the Keeper is
closed and the provider degrades to inactive.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from keep.hermes.const import (
    EMBEDDING_EMPTY_HINTS,
    EMBEDDING_EMPTY_MESSAGE,
    EMBEDDING_LABEL,
    EMBEDDING_MISSING_ERROR,
    FLOW_SCHEMA,
    HELP_SCHEMA,
    KEEP_SKILL_MISSING_ERROR,
    PROMPT_QUERY,
    PROMPT_SCHEMA,
    ROLE_ASSISTANT,
    ROLE_USER,
    SETUP_COMMAND,
    SUMMARIZATION_LABEL,
    SYSTEM_PROMPT_HEADER,
    SYSTEM_PROMPT_SETUP_REQUIRED,
    TOOL_ERROR_INACTIVE,
    TOOL_ERROR_SETUP_HINT,
    TOOL_ERROR_SETUP_REQUIRED,
)

logger = logging.getLogger(__name__)

_MEMORY_CHARS_PER_TOKEN = 2.75


def _write_env_var(env_path: Path, key: str, value: str) -> None:
    """Set a single env var in a .env file (append or update)."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def _display_path(path: Path) -> str:
    """Format a path for display, using ~ for the home directory."""
    try:
        return str(Path("~") / path.resolve().relative_to(Path.home()))
    except Exception:
        return str(path)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class KeepMemoryProvider:
    """Duck-typed Hermes MemoryProvider backed by an in-process Keeper.

    Reads (search, get, prompt rendering) use the Keeper directly.
    Writes (put) also use the Keeper; the daemon handles background
    processing (embeddings, summaries).
    """

    def __init__(self):
        self._keeper = None  # keep.api.Keeper — created in initialize()
        self._store_path: Optional[str] = None
        self._session_id = ""
        self._session_item_id = ""
        self._session_tags: Dict[str, str] = {"source": "hermes"}
        self._setup_required = False
        self._system_prompt_token_budget = 1200
        self._prefetch_inline_token_budget = 900
        self._prefetch_background_token_budget = 1500
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._turn_count = 0
        self._setup_cmd = SETUP_COMMAND

    # -- Core protocol -------------------------------------------------------

    @property
    def name(self) -> str:
        return "keep"

    def is_available(self) -> bool:
        """Check if keep-skill is installed. No network calls."""
        try:
            from keep.api import Keeper  # noqa: F401
            return True
        except ImportError:
            return False

    def get_config_schema(self):
        try:
            current_embed, current_summ = self._current_keep_providers()
            embedding_choices, summarization_choices = self._setup_choices(
                current_embed, current_summ
            )
            return [
                {
                    "key": "embedding_choice",
                    "description": EMBEDDING_LABEL,
                    "choices": embedding_choices,
                    "empty_message": EMBEDDING_EMPTY_MESSAGE,
                    "empty_hints": EMBEDDING_EMPTY_HINTS,
                },
                {
                    "key": "summarization_choice",
                    "description": SUMMARIZATION_LABEL,
                    "choices": summarization_choices,
                },
            ]
        except ImportError:
            return []

    def save_config(self, values, hermes_home):
        """Bootstrap a profile-scoped Keep store config for Hermes.

        Prints its own setup summary so the wizard doesn't need
        keep-specific display logic.
        """
        from keep.config import ProviderConfig, create_default_config, save_config

        # Always use profile-scoped path for setup — ignore inherited
        # KEEP_STORE_PATH to prevent cross-profile store binding.
        store_path = Path(hermes_home, "keep").resolve()
        config = create_default_config(store_path)

        embedding = self._provider_config_from_choice(
            ProviderConfig, values.get("embedding_choice")
        )
        summarization = self._provider_config_from_choice(
            ProviderConfig, values.get("summarization_choice")
        )

        if embedding is None:
            raise ValueError(EMBEDDING_MISSING_ERROR)

        config.embedding = embedding
        if summarization is not None:
            config.summarization = summarization

        save_config(config)

        # Persist KEEP_STORE_PATH in .env so the daemon can find this store
        env_path = Path(hermes_home) / ".env"
        _write_env_var(env_path, "KEEP_STORE_PATH", str(store_path))
        os.environ["KEEP_STORE_PATH"] = str(store_path)

        # Print setup summary
        display_path = _display_path(store_path)
        embed_desc = f"{config.embedding.name}"
        if config.embedding.params.get("model"):
            embed_desc += f" ({config.embedding.params['model']})"
        print(f"\n  Store: {display_path}")
        print(f"  Embeddings: {embed_desc}")
        if summarization is not None:
            summ_desc = f"{config.summarization.name}"
            if config.summarization.params.get("model"):
                summ_desc += f" ({config.summarization.params['model']})"
            print(f"  Summarization: {summ_desc}")
        print(f"\n  To use the keep CLI with this store:")
        print(f"    export KEEP_STORE_PATH={display_path}")
        print()

    def initialize(self, session_id: str, **kwargs) -> None:
        """Create an in-process Keeper and ensure the daemon is running."""
        self._session_id = session_id
        hermes_home = kwargs.get("hermes_home")
        if hermes_home:
            self._store_path = os.environ.get("KEEP_STORE_PATH") or str(
                Path(hermes_home) / "keep"
            )
        else:
            self._store_path = os.environ.get("KEEP_STORE_PATH") or None

        self._session_item_id = self._build_session_item_id(session_id, **kwargs)
        self._session_tags = self._build_session_tags(session_id, **kwargs)
        self._configure_token_budgets(**kwargs)

        # Per-profile CLI command (hermes creates wrapper aliases per profile)
        identity = kwargs.get("agent_identity")
        if identity and identity != "default":
            self._setup_cmd = f"{identity} memory setup"

        # Skip for cron/flush contexts
        agent_context = kwargs.get("agent_context", "")
        platform = kwargs.get("platform", "cli")
        if agent_context in ("cron", "flush") or platform == "cron":
            logger.debug("Keep skipped: cron/flush context")
            return

        # Ensure the daemon is running for background processing.
        # If setup is required, skip Keeper creation — the store isn't ready.
        self._ensure_daemon()
        if self._setup_required:
            return

        # Create in-process Keeper
        try:
            from keep.api import Keeper

            self._keeper = Keeper(
                store_path=self._store_path,
                defer_startup_maintenance=True,
            )
            # Ensure system docs (prompts, library, now) are present before
            # first render.  Deferred maintenance skips this on fresh stores.
            self._keeper.ensure_sysdocs()
            logger.info("Keep memory provider initialized (in-process Keeper)")
        except Exception as e:
            self._keeper = None
            logger.warning("Keep Keeper creation failed: %s", e)

    def system_prompt_block(self) -> str:
        if self._setup_required:
            return SYSTEM_PROMPT_SETUP_REQUIRED.format(
                setup_command=self._setup_cmd,
            )

        if self._keeper is None:
            return ""

        try:
            now = self._keeper.get_now()
            if now and now.summary and now.summary.strip():
                body = now.summary
                # Respect the configured token budget to prevent prompt bloat
                max_chars = int(self._system_prompt_token_budget * _MEMORY_CHARS_PER_TOKEN)
                header_chars = len(SYSTEM_PROMPT_HEADER) + 2  # + "\n\n"
                remaining = max(200, max_chars - header_chars)
                if len(body) > remaining:
                    body = body[:remaining].rsplit("\n", 1)[0] + "\n…"
                return (
                    f"{SYSTEM_PROMPT_HEADER}\n\n"
                    f"{body}"
                )
        except Exception as e:
            logger.debug("Keep system_prompt_block failed: %s", e)

        return SYSTEM_PROMPT_HEADER

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._keeper is None:
            return ""

        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)

        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""

        if not result:
            result = self._render_prompt(
                PROMPT_QUERY,
                text=query,
                token_budget=self._prefetch_inline_token_budget,
            )
            if not result:
                return ""
        return result

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self._keeper is None or not query:
            return

        def _run():
            try:
                text = self._render_prompt(
                    PROMPT_QUERY,
                    text=query,
                    token_budget=self._prefetch_background_token_budget,
                )
                if text and text.strip():
                    with self._prefetch_lock:
                        self._prefetch_result = text
            except Exception as e:
                logger.debug("Keep prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="keep-prefetch"
        )
        self._prefetch_thread.start()

    def sync_turn(
        self, user_content: str, assistant_content: str, *, session_id: str = ""
    ) -> None:
        if self._keeper is None:
            return

        self._turn_count += 1
        turn = self._turn_count
        item_id = self._session_item_id or f"hermes:{self._session_id}"
        tags = dict(self._session_tags)

        def _sync():
            try:
                self._keeper.put(
                    f"{ROLE_USER} {user_content}",
                    id=item_id,
                    tags=tags,
                )
                self._keeper.put(
                    f"{ROLE_ASSISTANT} {assistant_content}",
                    id=item_id,
                    tags=tags,
                )
            except Exception as e:
                logger.debug("Keep sync_turn failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="keep-sync"
        )
        self._sync_thread.start()

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self._turn_count = turn_number

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if self._keeper is None or action not in ("add", "replace") or not content:
            return

        def _write():
            try:
                self._keeper.put(
                    content,
                    tags={**self._session_tags, "source": "hermes-builtin", "target": target},
                )
            except Exception as e:
                logger.debug("Keep memory mirror failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="keep-memwrite")
        t.start()

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        if self._keeper is None or not messages:
            return ""

        role_map = {"user": ROLE_USER, "assistant": ROLE_ASSISTANT}
        parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                label = role_map.get(role, role)
                parts.append(f"{label} {content}")

        if not parts:
            return ""

        try:
            self._keeper.put(
                "\n\n".join(parts),
                tags={**self._session_tags, "kind": "compression-snapshot"},
            )
        except Exception as e:
            logger.debug("Keep on_pre_compress failed: %s", e)

        return ""

    def on_delegation(
        self, task: str, result: str, *, child_session_id: str = "", **kwargs
    ) -> None:
        if self._keeper is None or not task:
            return

        def _write():
            try:
                content = f"Task: {task}\nResult: {result}"
                self._keeper.put(
                    content,
                    summary="Subagent delegation result",
                    tags={
                        **self._session_tags,
                        "child_session": child_session_id,
                        "kind": "delegation",
                    },
                )
            except Exception as e:
                logger.debug("Keep on_delegation failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="keep-delegation")
        t.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        # Return schemas unconditionally — they are static definitions.
        # Hermes indexes tool names at add_provider time (before initialize),
        # so returning [] here would leave the tool routing table empty.
        return [FLOW_SCHEMA, HELP_SCHEMA, PROMPT_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if self._keeper is None:
            if self._setup_required:
                return json.dumps({
                    "error": TOOL_ERROR_SETUP_REQUIRED,
                    "hint": TOOL_ERROR_SETUP_HINT.format(setup_command=self._setup_cmd),
                })
            return json.dumps({"error": TOOL_ERROR_INACTIVE})

        try:
            if tool_name == "keep_flow":
                return self._tool_flow(args)
            elif tool_name == "keep_help":
                return self._tool_help(args)
            elif tool_name == "keep_prompt":
                return self._tool_prompt(args)
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.error("Keep tool %s failed: %s", tool_name, e)
            return json.dumps({"error": f"keep {tool_name} failed: {e}"})

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        if self._keeper is not None:
            try:
                self._keeper.close()
            except Exception as e:
                logger.debug("Keep close failed: %s", e)
            self._keeper = None

    # -- Tool implementations ------------------------------------------------

    def _tool_flow(self, args: dict) -> str:
        state = args.get("state", "")
        if not state:
            return json.dumps({"error": "Missing required parameter: state"})

        params = args.get("params") or {}

        # Models sometimes put params into state_doc_yaml as YAML text.
        # Rescue by parsing the YAML as params if no explicit params given.
        state_doc_yaml = args.get("state_doc_yaml")
        if state_doc_yaml and not params:
            try:
                import yaml
                parsed = yaml.safe_load(state_doc_yaml)
                if isinstance(parsed, dict):
                    params = parsed
                    state_doc_yaml = None
            except Exception:
                pass

        result = self._keeper.run_flow(
            state,
            params=params,
            budget=args.get("budget"),
            cursor_token=args.get("cursor"),
            state_doc_yaml=state_doc_yaml,
        )

        # Always render readable output. Use explicit token_budget if
        # provided, otherwise default so read operations aren't terse.
        token_budget = args.get("token_budget") or 4000
        if token_budget:
            from keep.cli import render_flow_response
            rendered = render_flow_response(
                result, token_budget=token_budget, keeper=self._keeper,
            )
            if rendered:
                return json.dumps({"result": rendered})

        output: dict[str, Any] = {
            "status": result.status,
            "ticks": result.ticks,
        }
        if result.data:
            output["data"] = result.data
        if result.cursor:
            output["cursor"] = result.cursor
        if result.tried_queries:
            output["tried_queries"] = result.tried_queries
        return json.dumps(output, default=str)

    def _tool_help(self, args: dict) -> str:
        from keep.help import get_help_topic

        topic = args.get("topic", "index")
        text = get_help_topic(topic, link_style="mcp")
        if text:
            return json.dumps({"result": text})
        return json.dumps({"error": f"Unknown help topic: {topic}"})

    def _tool_prompt(self, args: dict) -> str:
        prompt_name = args.get("name")
        if not prompt_name:
            prompts = self._keeper.list_prompts()
            return json.dumps({
                "prompts": [p.name for p in prompts],
            })

        text = self._render_prompt(
            prompt_name,
            text=args.get("text"),
            id=args.get("id"),
            since=args.get("since"),
            until=args.get("until"),
            tags=args.get("tags"),
            deep=args.get("deep", False),
            scope=args.get("scope"),
            token_budget=args.get("token_budget"),
        )
        if text is None:
            return json.dumps({"error": f"prompt not found: {prompt_name}"})
        return json.dumps({"result": text})

    # -- Internal helpers ----------------------------------------------------

    def _render_prompt(self, name: str, text: str = None, **kwargs) -> Optional[str]:
        """Render a prompt and expand its templates. Returns text or None."""
        result = self._keeper.render_prompt(name, text, **kwargs)
        if result is None:
            return None
        from keep.cli import expand_prompt
        return expand_prompt(result, self._keeper)

    def _ensure_daemon(self) -> None:
        """Ensure the daemon is running for background processing."""
        try:
            from keep.daemon_client import get_port
            get_port(self._store_path)
        except SystemExit:
            self._setup_required = True
            logger.warning("Keep daemon not available (needs setup)")
        except Exception as e:
            logger.debug("Keep daemon check failed: %s", e)

    def _build_session_item_id(self, session_id: str, **kwargs) -> str:
        """Build the keep item ID for conversation turns.

        Each turn becomes a new version of this item — the keep model
        naturally captures the session as version history.

        For gateway sessions, the session_id already encodes platform,
        chat_id, thread_id, and user_id (built by hermes's
        build_session_key).  We use it directly so that each channel
        gets its own conversation item.

        For CLI sessions the session_id is a random UUID — we use a
        stable key instead so CLI turns accumulate as versions.
        """
        identity = kwargs.get("agent_identity") or "default"
        platform = kwargs.get("platform") or "cli"
        if platform == "cli":
            return f"{identity}:cli"
        return f"{identity}:{platform}:{session_id}" if session_id else f"{identity}:{platform}"

    def _build_session_tags(self, session_id: str, **kwargs) -> Dict[str, str]:
        tags = {"source": "hermes"}
        for key in (
            "platform", "user_id", "agent_identity",
        ):
            value = kwargs.get(key)
            if value:
                tags[key] = str(value)
        return tags

    def _configure_token_budgets(self, **kwargs) -> None:
        memory_char_limit = kwargs.get("memory_char_limit")
        user_char_limit = kwargs.get("user_char_limit")
        try:
            memory_chars = int(memory_char_limit) if memory_char_limit is not None else 2200
            user_chars = int(user_char_limit) if user_char_limit is not None else 1375
        except (TypeError, ValueError):
            memory_chars, user_chars = 2200, 1375

        total_chars = max(0, memory_chars) + max(0, user_chars)
        total_tokens = max(200, math.ceil(total_chars / _MEMORY_CHARS_PER_TOKEN)) if total_chars > 0 else 1300

        self._system_prompt_token_budget = total_tokens
        self._prefetch_inline_token_budget = max(300, round(total_tokens * 0.70))
        self._prefetch_background_token_budget = max(500, round(total_tokens * 1.15))

    def _provider_config_from_choice(self, provider_cls, choice):
        if choice is None:
            return None
        if isinstance(choice, (list, tuple)) and len(choice) == 2:
            return provider_cls(str(choice[0]).strip(), dict(choice[1]) if isinstance(choice[1], dict) else {})
        if isinstance(choice, dict):
            name = str(choice.get("provider") or "").strip()
            params = choice.get("params") if isinstance(choice.get("params"), dict) else {}
            return provider_cls(name, dict(params)) if name else None
        if isinstance(choice, str) and choice.strip():
            return provider_cls(choice.strip(), {})
        return None

    def _current_keep_providers(self):
        try:
            from keep.config import load_config
            store_path = Path(
                os.environ.get("KEEP_STORE_PATH")
                or Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "keep"
            ).resolve()
            config = load_config(store_path)
            embed = config.embedding.name if config.embedding else None
            summ = config.summarization.name if config.summarization else None
            return embed, summ
        except Exception:
            return None, None

    def _setup_choices(self, current_embed, current_summ):
        from keep.setup_wizard import detect_embedding_choices, detect_summarization_choices

        all_embed = detect_embedding_choices(current=current_embed)
        all_summ = detect_summarization_choices(current=current_summ)

        embedding_choices = [
            {"name": c["name"], "hint": f"-- {c['hint']}", "value": c["value"], "default": c.get("default", False)}
            for c in all_embed if c.get("available")
        ]
        summarization_choices = [
            {"name": c["name"], "hint": f"-- {c['hint']}", "value": c["value"], "default": c.get("default", False)}
            for c in all_summ if c.get("available")
        ]

        return embedding_choices, summarization_choices
