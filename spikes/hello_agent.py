"""Kill-test spike: can we call a Foundry model through Microsoft Agent Framework?

Three checks, each reported PASS/FAIL with timing:
  1. hello:    one agent call returns text
  2. tool:     the model calls a local function tool and uses its result
  3. workflow: two agents chained with WorkflowBuilder stream an output

Needs FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL, and `az login`. See
cases/.../20-build/sponsor-digest.md section 7. Prints no secrets.

Run: uv run --group agents python spikes/hello_agent.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Annotated

from pydantic import Field

REQUIRED_ENV = ("FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL")
FAKE_XG = 0.42


def missing_env() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.environ.get(name)]


def get_shot_xg(
    shot_id: Annotated[str, Field(description="Id of the shot event.")],
) -> str:
    """Return the expected-goals value for a shot (synthetic)."""
    return f"shot {shot_id} has xG {FAKE_XG}"


async def check_hello(client) -> str:
    agent = client.as_agent(name="Hello", instructions="Reply in one short sentence.")
    result = await agent.run("Say hello to the MatchDesk team.")
    if not result.text.strip():
        raise RuntimeError("empty response")
    return result.text.strip()[:80]


async def check_tool(client) -> str:
    agent = client.as_agent(
        name="Stats",
        instructions="Always call get_shot_xg for shot facts, then answer in one sentence.",
        tools=[get_shot_xg],
    )
    result = await agent.run("What is the xG of shot e-17?")
    if str(FAKE_XG) not in result.text:
        raise RuntimeError(f"tool result not used: {result.text[:80]!r}")
    return result.text.strip()[:80]


async def check_workflow(client) -> str:
    from agent_framework import AgentResponseUpdate, WorkflowBuilder

    writer = client.as_agent(name="Writer", instructions="Write one sentence about a football goal.")
    reviewer = client.as_agent(name="Reviewer", instructions="Shorten the sentence you receive to under 12 words.")
    workflow = WorkflowBuilder(start_executor=writer).add_edge(writer, reviewer).build()
    authors: list[str] = []
    async for event in workflow.run("A goal was scored.", stream=True):
        if event.type == "output" and isinstance(event.data, AgentResponseUpdate):
            if event.data.author_name not in authors:
                authors.append(event.data.author_name)
    if authors != ["Writer", "Reviewer"]:
        raise RuntimeError(f"unexpected authors: {authors}")
    return "streamed " + " -> ".join(authors)


async def run_check(name: str, check: Callable[..., Awaitable[str]], client) -> bool:
    start = time.perf_counter()
    try:
        detail = await check(client)
    except Exception as error:  # report every failure kind, then continue
        print(f"FAIL {name:9s} {time.perf_counter() - start:5.1f}s  {type(error).__name__}: {error}")
        return False
    print(f"PASS {name:9s} {time.perf_counter() - start:5.1f}s  {detail}")
    return True


async def main() -> int:
    absent = missing_env()
    if absent:
        print(f"Set {', '.join(absent)} (see .env.example) and run `az login`.")
        return 2
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import AzureCliCredential

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["FOUNDRY_MODEL"],
        credential=AzureCliCredential(),
    )
    results = [
        await run_check("hello", check_hello, client),
        await run_check("tool", check_tool, client),
        await run_check("workflow", check_workflow, client),
    ]
    print("KILL TEST PASSED" if all(results) else "KILL TEST FAILED: use the fallback in idea.md section 8")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
