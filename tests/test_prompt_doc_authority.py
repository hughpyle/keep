"""Regression tests for store-backed prompt and state doc authority."""

from keep.api import Keeper
from keep.const import STATE_FIND_DEEP, STATE_PROMPT
from keep.flow_env import LocalFlowEnvironment
from keep.state_doc_runtime import FlowResult
from keep.state_doc_runtime import make_action_runner


def _ensure_system_docs(kp: Keeper) -> None:
    """Trigger system doc migration in temp-store tests."""
    kp.put("migration trigger", id="_prompt-test-trigger")
    kp.delete("_prompt-test-trigger")


def test_render_prompt_requires_state_for_dynamic_prompt(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    kp.put(
        "# Test\n\n## Prompt\nHello {get}",
        id=".prompt/agent/test-dynamic",
        tags={"category": "system", "context": "prompt"},
    )

    result = kp.run_flow_command(STATE_PROMPT, params={"name": "test-dynamic"})

    assert result.status == "error"
    assert "no state tag" in str(result.data.get("error", "")).lower()


def test_prompt_list_bootstraps_on_fresh_store(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)

    result = kp.run_flow_command(STATE_PROMPT, params={"list": True})

    assert result.status == "done"
    prompts = result.data.get("prompts", [])
    assert any(prompt.get("name") == "reflect" for prompt in prompts)
    reflect = next(prompt for prompt in prompts if prompt.get("name") == "reflect")
    assert reflect.get("mcp_arguments") == ["text", "id", "since", "token_budget"]


def test_prompt_list_normalizes_mcp_prompt_tag_variants(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    kp.put(
        "# Test\n\n## Prompt\nContext: {text}",
        id=".prompt/agent/test-mcp-string",
        tags={
            "category": "system",
            "context": "prompt",
            "mcp_prompt": " text , since , unsupported , text ",
        },
    )
    kp.put(
        "# Test\n\n## Prompt\nContext: {text}",
        id=".prompt/agent/test-mcp-list",
        tags={
            "category": "system",
            "context": "prompt",
            "mcp_prompt": ["id", "token_budget", "bogus", "id"],
        },
    )

    result = kp.run_flow_command(STATE_PROMPT, params={"list": True})

    assert result.status == "done"
    prompts = {prompt["name"]: prompt for prompt in result.data.get("prompts", [])}
    assert prompts["test-mcp-string"]["mcp_arguments"] == ["text", "since"]
    assert prompts["test-mcp-list"]["mcp_arguments"] == ["id", "token_budget"]


def test_prompt_list_normalizes_json_encoded_mcp_prompt_tag(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    kp.put(
        "# Test\n\n## Prompt\nContext: {text}",
        id=".prompt/agent/test-mcp-json",
        tags={
            "category": "system",
            "context": "prompt",
            "mcp_prompt": '["text", "since", "token_budget"]',
        },
    )

    result = kp.run_flow_command(STATE_PROMPT, params={"list": True})

    assert result.status == "done"
    prompts = {prompt["name"]: prompt for prompt in result.data.get("prompts", [])}
    assert prompts["test-mcp-json"]["mcp_arguments"] == ["text", "since", "token_budget"]


def test_query_prompt_without_text_renders_without_running_query_resolution(
    mock_providers, tmp_path
):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    result = kp.run_flow_command(STATE_PROMPT, params={"name": "query"})

    assert result.status == "done"
    text = result.data.get("text", "")
    assert "Question:" in text
    assert "Context:" in text


def test_query_prompt_uses_find_deep_state(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    prompt_doc = kp.get(".prompt/agent/query")

    assert prompt_doc is not None
    assert prompt_doc.tags.get("state") == STATE_FIND_DEEP


def test_prompt_render_tolerates_ambiguous_stopped_flow(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    original = kp.run_flow_command

    def _patched(state, params=None, **kwargs):
        if state == STATE_FIND_DEEP:
            return FlowResult(
                status="stopped",
                bindings={
                    "search": {
                        "results": [{"id": "doc1", "summary": "Daemon code edited yesterday", "tags": {}}],
                        "count": 1,
                    }
                },
                data={"reason": "ambiguous"},
                ticks=3,
                history=["find-deep"],
            )
        return original(state, params=params, **kwargs)

    kp.run_flow_command = _patched

    result = kp.render_prompt(name="query", text="when was the daemon code edited?")

    assert result is not None
    assert result.flow_bindings is not None
    assert result.flow_bindings["search"]["count"] == 1


def test_render_prompt_coerces_string_token_budget(mock_providers, tmp_path):
    from keep.cli import expand_prompt

    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)
    kp.put("Daemon code edited yesterday", id="doc1")

    result = kp.render_prompt(
        name="query",
        text="when was the daemon code edited?",
        token_budget="50",
    )

    assert result is not None
    assert result.token_budget == 50
    expanded = expand_prompt(result, kp)
    assert "Question:" in expanded


def test_summarize_action_errors_when_default_prompt_is_broken(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)
    kp.put("Content to summarize", id="doc-1")
    doc_coll = kp._resolve_doc_collection()
    for rec in kp._document_store.query_by_id_prefix(doc_coll, ".prompt/summarize/"):
        kp._document_store.delete(doc_coll, rec.id)

    runner = make_action_runner(LocalFlowEnvironment(kp), writable=True)

    try:
        runner("summarize", {"item_id": "doc-1", "force": True})
        assert False, "summarize should fail when the default prompt doc is broken"
    except ValueError as exc:
        assert "missing prompt doc for summarize" in str(exc).lower()


def test_render_prompt_expands_include_directive(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    kp.put(
        "# Inner\n\n## Prompt\ninner body text",
        id=".prompt/agent/test-inner",
        tags={"category": "system", "context": "prompt"},
    )
    kp.put(
        "# Wrapper\n\n## Prompt\nbefore\n{{include:agent/test-inner}}\nafter",
        id=".prompt/agent/test-wrapper",
        tags={"category": "system", "context": "prompt"},
    )

    result = kp.render_prompt(name="test-wrapper")

    assert result is not None
    assert result.prompt is not None
    assert "before" in result.prompt
    assert "inner body text" in result.prompt
    assert "after" in result.prompt
    assert "{{include:" not in result.prompt


def test_render_prompt_include_missing_target_raises(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    kp.put(
        "# Wrapper\n\n## Prompt\n{{include:agent/does-not-exist}}",
        id=".prompt/agent/test-missing-include",
        tags={"category": "system", "context": "prompt"},
    )

    try:
        kp.render_prompt(name="test-missing-include")
        assert False, "missing include target should raise"
    except ValueError as exc:
        assert "not found" in str(exc).lower()


def test_render_prompt_include_cycle_raises(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    kp.put(
        "# A\n\n## Prompt\n{{include:agent/test-cycle-b}}",
        id=".prompt/agent/test-cycle-a",
        tags={"category": "system", "context": "prompt"},
    )
    kp.put(
        "# B\n\n## Prompt\n{{include:agent/test-cycle-a}}",
        id=".prompt/agent/test-cycle-b",
        tags={"category": "system", "context": "prompt"},
    )

    try:
        kp.render_prompt(name="test-cycle-a")
        assert False, "include cycle should raise"
    except ValueError as exc:
        assert "cycle" in str(exc).lower()


def test_render_prompt_include_rejects_path_escape(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    # Dots are not in the allowed include character set — the directive
    # should simply not match, leaving the literal text in place (and
    # therefore not pulling anything from outside the prompt namespace).
    kp.put(
        "# Wrapper\n\n## Prompt\n{{include:../tag/act}}",
        id=".prompt/agent/test-escape",
        tags={"category": "system", "context": "prompt"},
    )

    result = kp.render_prompt(name="test-escape")

    assert result is not None
    assert "{{include:../tag/act}}" in result.prompt


def test_system_hermes_prompt_embeds_generic_body(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)

    result = kp.render_prompt(name="system-hermes")

    assert result is not None
    assert result.prompt is not None
    # Framed header from the wrapper
    assert "KEEP — REFLECTIVE MEMORY" in result.prompt
    # Division-of-labor text from the wrapper
    assert "Use them together" in result.prompt
    assert "Built-in `memory`" in result.prompt
    # Content pulled in from the generic .prompt/agent/system
    assert "keep_prompt" in result.prompt
    assert "keep_flow" in result.prompt
    # Include directive must have been expanded, not left literal
    assert "{{include:" not in result.prompt


def test_analyze_action_errors_when_default_prompt_is_broken(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    _ensure_system_docs(kp)
    kp.put("Analyze this content into parts.", id="doc-2")
    doc_coll = kp._resolve_doc_collection()
    for rec in kp._document_store.query_by_id_prefix(doc_coll, ".prompt/analyze/"):
        kp._document_store.delete(doc_coll, rec.id)

    runner = make_action_runner(LocalFlowEnvironment(kp), writable=True)

    try:
        runner("analyze", {"item_id": "doc-2", "force": True})
        assert False, "analyze should fail when the default prompt doc is broken"
    except ValueError as exc:
        assert "missing prompt doc for analyze" in str(exc).lower()
