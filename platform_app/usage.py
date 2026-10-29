"""Expose provider-reported cache usage without changing the agent runtime."""
from langchain_core.messages import AIMessage


def count(value):
    return value if type(value) is int and value >= 0 else None


def cache_usage(usage, messages, model_id):
    value = {**usage, 'cache_read_input_tokens': None, 'cache_hit_rate': None}
    total = count(usage.get('input_tokens'))
    if total is None:
        return value
    last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if last is None or last.response_metadata.get('deep_agent_model_id') != model_id or last.additional_kwargs.get('manual_compact_completed'):
        return value
    metadata = last.usage_metadata or {}
    raw = last.response_metadata.get('token_usage') or {}
    raw = raw if isinstance(raw, dict) else {}
    reported = count(metadata.get('input_tokens'))
    if reported is None:
        reported = count(raw.get('prompt_tokens', raw.get('input_tokens')))
    if reported != total:
        return value
    details = metadata.get('input_token_details') or {}
    prompt = raw.get('prompt_tokens_details') or raw.get('input_tokens_details') or {}
    details = details if isinstance(details, dict) else {}
    prompt = prompt if isinstance(prompt, dict) else {}
    # DeepSeek also reports the native prompt_cache_hit_tokens field.
    candidates = [raw.get('prompt_cache_hit_tokens'), details.get('cache_read'),
        details.get('priority_cache_read'), details.get('flex_cache_read'), prompt.get('cached_tokens')]
    cached = next((n for item in candidates if (n := count(item)) is not None), None)
    if cached is not None and cached <= total:
        value['cache_read_input_tokens'] = cached
        value['cache_hit_rate'] = cached / total if total else None
    return value


def runner_usage(runner):
    usage = runner.latest_usage()
    try:
        state = runner.prepared.graph.get_state({'configurable': {'thread_id': runner.thread_id}})
        return cache_usage(usage, state.values.get('messages', []), runner.current_model().id)
    except Exception:
        return {**usage, 'cache_read_input_tokens': None, 'cache_hit_rate': None}
