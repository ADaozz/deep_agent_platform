"""Compatibility loader for external callers; core Agent code never imports it."""
from .format_plugins import load_plugin
_plugin = load_plugin('docx')
Documents = _plugin.Documents
DocumentStore = _plugin.DocumentStore
engine = _plugin.engine
safe_path = _plugin.safe_path
INSTRUCTIONS = _plugin.INSTRUCTIONS
