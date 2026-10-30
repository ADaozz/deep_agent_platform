"""Expose provider-reported cache usage without changing the agent runtime."""
from langchain_core.messages import AIMessage
from .agent_core.token_usage import message_usage


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
    parsed = message_usage(last)
    if parsed.get('input_tokens') == total:
        value.update(cache_read_input_tokens=parsed.get('cache_read_input_tokens'), cache_hit_rate=parsed.get('cache_hit_rate'))

    return value


def runner_usage(runner):
    usage = runner.latest_usage()
    try:
        state = runner.prepared.graph.get_state({'configurable': {'thread_id': runner.thread_id}})
        return cache_usage(usage, state.values.get('messages', []), runner.current_model().id)
    except Exception:
        return {**usage, 'cache_read_input_tokens': None, 'cache_hit_rate': None}
