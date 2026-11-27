"""Offline smoke test using the installed AstrBot request pipeline and a mock runner."""

import argparse
import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def block_network(event, args):
    if event not in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        return
    target = args[0] if event == "socket.getaddrinfo" else args[-1]
    host = target[0] if isinstance(target, tuple) else target
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"Offline regression attempted external network IO: {event}")


async def check(upstream, legacy_enabled):
    from astrbot.core import astr_main_agent as main_agent
    from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
        InternalAgentSubStage,
    )

    fixture = {
        "provider_settings": {
            "streaming_response": False,
            "unsupported_streaming_strategy": "buffer",
            "proactive_capability": {"add_cron_tools": False},
        },
        "agent_runner": {
            "config": {
                "model": {},
                "persona": {
                    "persona_id": "offline-example",
                    "safety_mode": legacy_enabled,
                    "safety_mode_strategy": "system_prompt",
                },
                "compression": {},
                "misc": {},
            }
        },
    }
    context = MagicMock(spec=main_agent.Context)
    context.conversation_manager = None
    context.get_config.return_value = fixture
    stage = InternalAgentSubStage()
    await stage.initialize(
        SimpleNamespace(astrbot_config=fixture, plugin_manager=SimpleNamespace(context=context))
    )
    event = MagicMock(spec=main_agent.AstrMessageEvent)
    event.message_obj = SimpleNamespace(message=[])
    event.get_extra.return_value = None
    event.unified_msg_origin = "offline:FriendMessage:example"
    event.platform_meta = SimpleNamespace(support_proactive_message=False)
    event.get_platform_name.return_value = "offline"
    provider = MagicMock()
    provider.provider_config = {"max_context_tokens": 2048}
    persona = "You are a friendly museum guide."
    user_text = "Hello, what is a fossil?"
    req = main_agent.ProviderRequest(prompt=user_text, system_prompt=persona)
    runner = SimpleNamespace(reset=AsyncMock())
    # Isolate model, plugin, DB and multimedia services. The stage initializer and
    # complete build_main_agent body (including any persona-mode branch) run normally.
    with ExitStack() as stack:
        for name in (
            "prepare_request_images",
            "_decorate_llm_request",
            "_apply_kb",
            "_apply_web_search_tools",
            "_get_compress_provider",
        ):
            stack.enter_context(patch.object(main_agent, name, AsyncMock(return_value=())))
        for name in ("_plugin_tool_fix", "_apply_web_search_citation_prompt"):
            stack.enter_context(patch.object(main_agent, name, return_value=None))
        stack.enter_context(patch.object(main_agent, "AgentRunner", return_value=runner))
        await main_agent.build_main_agent(
            event=event,
            plugin_context=context,
            provider=provider,
            config=stage.main_agent_cfg,
            req=req,
        )
    runner.reset.assert_awaited_once()
    prepared = runner.reset.await_args.kwargs["request"]
    if prepared.prompt != user_text:
        raise AssertionError("Unexpected user message")
    if upstream and legacy_enabled:
        if not prepared.system_prompt.startswith("You are running in Safe Mode."):
            raise AssertionError("Official-wheel positive control did not inject its default text")
        if not prepared.system_prompt.endswith(persona):
            raise AssertionError("Original persona was lost")
    elif prepared.system_prompt != persona:
        raise AssertionError("An unexpected instruction was added to the ordinary request")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", action="store_true", help="Expect official-wheel behavior")
    args = parser.parse_args()
    sys.addaudithook(block_network)
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as temp:
        os.environ["ASTRBOT_ROOT"] = temp
        os.chdir(temp)
        try:
            for flag in (True, False):
                asyncio.run(check(args.upstream, legacy_enabled=flag))
        finally:
            os.chdir(cwd)
    print("Offline ordinary-chat checks passed with both legacy setting values.")


if __name__ == "__main__":
    main()
