"""Prepare the native core with explicitly registered platform tools."""
from platform_app.agent_core import AgentSpec, PermissionMode, RunController, build_agent
from platform_app.agent_core.permission import interrupt_on_for_mode

from .web_tools import build_web_tools


def prepare_agent(*, settings, model, instructions, skills, extra_tools,
                  checkpointer=None, interrupt_on=None, mcp=None):
    # Native create_agent implicitly reads TAVILY_API_KEY. Build the public
    # AgentSpec directly so that provider is never registered, even on rebuilds
    # or in native subagents. No process environment or installed core is edited.
    spec = AgentSpec(settings=settings, trust_env=settings.trust_env,
        instructions=instructions, skills=tuple(skills), sandbox=settings.sandbox,
        hidden_tools=settings.hidden_tools,
        tools=tuple([*extra_tools, *build_web_tools(settings)]),
        web_search_api_key=None)
    approvals = {**(interrupt_on_for_mode(PermissionMode.ASK) or {}),
        'web_fetch': {'allowed_decisions': ['approve', 'reject']},
        **(interrupt_on or {})}
    return build_agent(spec, model, PermissionMode.ASK, checkpointer,
        RunController(), lambda: False, interrupt_on_override=approvals, mcp=mcp)
