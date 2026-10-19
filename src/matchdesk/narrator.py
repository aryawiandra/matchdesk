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
    "Output: 1. Minute 40: Sam Roe of Blue FC scores from 6 m and it is 1-0.\n"
    "Input: 1. Red FC pressed at 3.5 pressures per minute over the last three minutes, up to minute 29.\n"
    "Output: 1. Red FC keep the pressure on, 3.5 pressures per minute across the last three minutes up to minute 29.\n"
    "Input: 1. Alex Doe (Red FC) reached 31 km/h in minute 12.\n"
    "Output: 1. In minute 12 Alex Doe of Red FC hits 31 km/h on the run."
)
SYSTEM_ID = (
    "Kamu penulis siaran sepak bola. Kamu menerima kalimat bernomor. Tulis ulang setiap kalimat menjadi satu kalimat "
    "siaran yang singkat dan alami dalam bahasa Indonesia, dengan kata-kata yang berbeda: ubah struktur kalimat dan "
    "pilihan katanya, jangan menyalin. Aturan: pertahankan setiap angka, setiap nama pemain, dan setiap nama klub "
    "persis seperti tertulis; jangan menambah angka, nama, klub, atau fakta; jangan memakai kata hiperbola seperti "
    "luar biasa, spektakuler, atau dramatis; jangan menjelaskan sebab. Balas dengan tepat satu baris per kalimat, "
    "dengan urutan yang sama, berformat '1. <kalimat>'. Jangan menulis hal lain.\n"
    "Contoh gaya (nama dan angka di bawah hanya contoh):\n"
    "Masukan: 1. Sam Roe (Blue FC) mencetak gol pada menit 40 dari jarak 6 m, skor menjadi 1-0.\n"
    "Keluaran: 1. Menit 40: Sam Roe dari Blue FC mencetak gol dari jarak 6 m dan skor menjadi 1-0.\n"
    "Masukan: 1. Red FC menekan dengan 3,5 tekanan per menit selama tiga menit terakhir, hingga menit 29.\n"
    "Keluaran: 1. Red FC terus menekan, 3,5 tekanan per menit dalam tiga menit terakhir hingga menit 29.\n"
    "Masukan: 1. Alex Doe (Red FC) berlari hingga 31 km/jam pada menit 12.\n"
    "Keluaran: 1. Pada menit 12, Alex Doe dari Red FC berlari sampai 31 km/jam."
)
SYSTEMS = {"en": SYSTEM, "id": SYSTEM_ID}
NUMBERED_LINE = re.compile(r"^\s*(\d+)\s*[.)]\s*(.+?)\s*$")
QUOTES = "\"'“”‘’ "
DEFAULT_TEMPERATURE = 0.3
DEFAULT_SEED = 7
MAX_NEW_TOKENS = 220


PROMPT_WORDS = {
    "en": ("Rewrite these sentences:",
           "Your previous answer was rejected for these reasons: {reasons}. Rewrite again and fix exactly those problems."),
    "id": ("Tulis ulang kalimat-kalimat ini:",
           "Jawabanmu sebelumnya ditolak karena: {reasons}. Tulis ulang dan perbaiki tepat masalah itu."),
}


def build_prompt(base: Sequence[Claim], feedback: Sequence[str], lang: str = "en") -> str:
    header, rejected = PROMPT_WORDS[lang]
    prompt = header + "\n" + "\n".join(f"{i}. {claim.text}" for i, claim in enumerate(base, start=1))
    if feedback:
        prompt += "\n\n" + rejected.format(reasons="; ".join(feedback))
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

    def __init__(self, generate: Generate, lang: str = "en"):
        if lang not in SYSTEMS:
            raise ValueError(f"no prompts for language {lang!r}")
        self._generate = generate
        self._lang = lang
        self.calls = 0
        self.seconds = 0.0

    def __call__(self, detection: Detection, ctx: MatchContext, feedback: tuple[str, ...]) -> tuple[Claim, ...]:
        base = template_claims(detection, ctx, self._lang)
        started = time.perf_counter()
        try:
            text = self._generate(SYSTEMS[self._lang], build_prompt(base, feedback, self._lang))
        finally:
            self.calls += 1
            self.seconds += time.perf_counter() - started
        sentences = parse_numbered(text, expected=len(base))
        return tuple(replace(claim, text=sentence) for claim, sentence in zip(base, sentences))


def narrator_from_spec(spec: str | None, host: str | None = None, lang: str = "en") -> Narrator | None:
    """`ollama:<model>` builds a narrator on a local Ollama model; blank means no model."""
    if spec is None or not spec.strip():
        return None
    provider, _, model = spec.strip().partition(":")
    if provider != "ollama" or not model:
        raise ValueError(f"MATCHDESK_MODEL must look like 'ollama:<model>', got {spec!r}")
    return Narrator(ollama_generate(model, host), lang)


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
