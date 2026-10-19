"""Narrator: a model rewrites the template sentences in a broadcast voice; the Verifier decides what is published.

The model only changes wording. Facts, cited events and named players always come from the template claim, so the
Verifier can recompute every number and check every name in the model's sentence against them. A model that invents a
number, names the wrong player, guesses a gender, or answers in the wrong shape is rejected, told why, and after the
allowed retries replaced by the template (see `explainer.explain` and the workflow)."""
import asyncio
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import replace

from agent_framework import Agent
from agent_framework.ollama import OllamaChatClient

from .analyst import Detection
from .claims import Claim
from .context import MatchContext
from .explainer import template_claims

Generate = Callable[[str, str], str]  # (system prompt, user prompt) -> the model's text

SYSTEM = (
    "You are a football broadcast writer. You get numbered sentences. Rewrite each one as one short, natural "
    "broadcast sentence in different words: change the sentence structure and the vocabulary, never copy the input. "
    "Rules: keep every number, every player name and every club name exactly as written; do not add any number, "
    "name, club or fact; do not use the pronouns he, she, his, her or him; do not use hype words such as brilliant, "
    "dramatic or dominant; do not explain causes. Reply with exactly one line per sentence, in the same order, "
    "formatted as '1. <sentence>'. Nothing else.\n"
    "Examples of the style (the names and numbers below are only examples):\n"
    "Input: 1. Sam Roe (Blue FC) scored in minute 40 from 6 m, making it 1-0.\n"
    "Output: 1. Minute 40: Sam Roe of Blue FC finishes from 6 m and it is 1-0.\n"
    "Input: 1. Red FC pressed at 3.5 pressures per minute over the last three minutes, up to minute 29.\n"
    "Output: 1. Red FC keep the pressure on, 3.5 pressures per minute across the last three minutes up to minute 29.\n"
    "Input: 1. Alex Doe (Red FC) reached 31 km/h in minute 12.\n"
    "Output: 1. In minute 12 Alex Doe of Red FC hits 31 km/h on the run."
)
NUMBERED_LINE = re.compile(r"^\s*(\d+)\s*[.)]\s*(.+?)\s*$")
QUOTES = "\"'“”‘’ "
DEFAULT_TEMPERATURE = 0.3
DEFAULT_SEED = 7
MAX_NEW_TOKENS = 220


def build_prompt(base: Sequence[Claim], feedback: Sequence[str]) -> str:
    prompt = "Rewrite these sentences:\n" + "\n".join(f"{i}. {claim.text}" for i, claim in enumerate(base, start=1))
    if feedback:
        prompt += ("\n\nYour previous answer was rejected for these reasons: " + "; ".join(feedback)
                   + ". Rewrite again and fix exactly those problems.")
    return prompt


def parse_numbered(text: str, expected: int) -> tuple[str, ...]:
    found: dict[int, str] = {}
    for line in text.splitlines():
        match = NUMBERED_LINE.match(line)
        if match:
            found.setdefault(int(match.group(1)), match.group(2).strip(QUOTES))
    if sorted(found) == list(range(1, expected + 1)):
        return tuple(found[i] for i in range(1, expected + 1))
    lines = [ln.strip(QUOTES) for ln in text.splitlines() if ln.strip()]
    if expected == 1 and not found and len(lines) == 1:
        return (lines[0],)
    raise ValueError(f"expected {expected} numbered sentences, got {len(found)}")


class Narrator:
    """A `Model` for `explainer.explain` and the workflow: (detection, context, feedback) -> claims."""

    def __init__(self, generate: Generate):
        self._generate = generate
        self.calls = 0
        self.seconds = 0.0

    def __call__(self, detection: Detection, ctx: MatchContext, feedback: tuple[str, ...]) -> tuple[Claim, ...]:
        base = template_claims(detection, ctx)
        started = time.perf_counter()
        try:
            text = self._generate(SYSTEM, build_prompt(base, feedback))
        finally:
            self.calls += 1
            self.seconds += time.perf_counter() - started
        sentences = parse_numbered(text, expected=len(base))
        return tuple(replace(claim, text=sentence) for claim, sentence in zip(base, sentences))


def ollama_generate(
    model: str,
    host: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: int = DEFAULT_SEED,
) -> Generate:
    """A generator backed by a Microsoft Agent Framework `Agent` on a local Ollama model.

    A fresh client and agent are built for every call: the client must not be shared across event loops, and each call
    runs in its own loop (the workflow calls this from a worker thread)."""
    def generate(system: str, prompt: str) -> str:
        async def run() -> str:
            agent = Agent(
                client=OllamaChatClient(host=host, model=model),
                name="Narrator",
                instructions=system,
                default_options={"temperature": temperature, "seed": seed, "num_predict": MAX_NEW_TOKENS},
            )
            return (await agent.run(prompt)).text

        return asyncio.run(run())

    return generate
