"""Normalize reported token counts for both checkpoint and streaming events."""


def message_usage(message):
    metadata = getattr(message, 'usage_metadata', None) or {}
    raw = (getattr(message, 'response_metadata', None) or {}).get('token_usage') or {}
    metadata = metadata if isinstance(metadata, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    def count(value):
        return value if type(value) is int and value >= 0 else None
    result = {key: value for key in ('input_tokens', 'output_tokens', 'total_tokens')
        if (value := count(metadata.get(key))) is not None}
    for key, alternate in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'), ('total_tokens', 'total_tokens')):
        if key not in result and (value := count(raw.get(alternate, raw.get(key)))) is not None:
            result[key] = value
    if 'input_tokens' not in result:
        return result
    total = result['input_tokens']
    details = metadata.get('input_token_details') or {}
    prompt = raw.get('prompt_tokens_details') or raw.get('input_tokens_details') or {}
    details = details if isinstance(details, dict) else {}
    prompt = prompt if isinstance(prompt, dict) else {}
    cached = next((value for item in (raw.get('prompt_cache_hit_tokens'), details.get('cache_read'),
        details.get('priority_cache_read'), details.get('flex_cache_read'), prompt.get('cached_tokens'))
        if (value := count(item)) is not None), None)
    if cached is not None and cached > total:
        cached = None
    result.update(cache_read_input_tokens=cached, cache_hit_rate=cached / total if cached is not None and total else None)
    return result
