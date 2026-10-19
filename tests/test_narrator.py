"""Narrator: a model rewrites template sentences; the Verifier decides what is published. Tests use fake generators."""
import pytest

from matchdesk import narrator as narrator_module
from matchdesk.explainer import explain, template_claims
from matchdesk.narrator import SYSTEM, Narrator, build_prompt, narrator_from_spec, ollama_generate, parse_numbered
from matchdesk.verifier import verify_claims
from tests.test_explainer import first_of, replay


@pytest.fixture(scope="module")
def replayed(full_match):
    return replay(full_match)


@pytest.fixture(scope="module")
def goal(replayed):
    context, found = replayed
    return first_of(found, "goal"), context


def reword(prompt: str) -> str:
    """A well-behaved model: same facts, different words, one numbered line per sentence."""
    lines = [ln.split(". ", 1) for ln in prompt.splitlines() if ln[:1].isdigit()]
    return "\n".join(f"{n}. {text.replace('scored', 'found the net').replace('The shot left', 'It flew off')}" for n, text in lines)


# ---- prompt and parsing
def test_the_system_prompt_forbids_what_the_verifier_would_reject():
    for word in ("number", "pronouns", "cause"):
        assert word in SYSTEM


def test_the_prompt_numbers_every_template_sentence_and_adds_feedback_only_on_a_retry(goal):
    detection, context = goal
    base = template_claims(detection, context)
    first = build_prompt(base, ())
    assert all(f"{i}. {claim.text}" in first for i, claim in enumerate(base, start=1))
    assert "rejected" not in first
    retry = build_prompt(base, ("unsupported number 97", "gendered pronoun 'his'"))
    assert "rejected" in retry and "unsupported number 97" in retry and "gendered pronoun 'his'" in retry


@pytest.mark.parametrize("text", ["1. First.\n2. Second.", "1) First.\n2) Second.", '\n1. "First."\n\n2. Second.\n',
                                  "Sure!\n1. First.\n2. Second."])
def test_parse_numbered_accepts_common_formats(text):
    assert parse_numbered(text, expected=2) == ("First.", "Second.")


def test_a_single_unnumbered_line_is_accepted_when_one_sentence_is_expected():
    assert parse_numbered('"The home side pressed."', expected=1) == ("The home side pressed.",)


@pytest.mark.parametrize("text", ["1. Only one.", "2. Wrong start.\n3. Next.", "no numbers here\nat all", ""])
def test_parse_numbered_rejects_the_wrong_number_of_sentences(text):
    with pytest.raises(ValueError, match="numbered sentences"):
        parse_numbered(text, expected=2)


# ---- the narrator keeps facts and evidence from the template
def test_only_the_wording_changes(goal):
    detection, context = goal
    base = template_claims(detection, context)
    claims = Narrator(lambda system, prompt: reword(prompt))(detection, context, ())
    assert len(claims) == len(base)
    for new, old in zip(claims, base):
        assert (new.evidence_ids, new.facts, new.player_ids) == (old.evidence_ids, old.facts, old.player_ids)
    assert claims[0].text != base[0].text
    assert verify_claims(claims, context).ok


def test_a_reworded_answer_is_used_as_the_model_answer(goal):
    detection, context = goal
    result = explain(detection, context, model=Narrator(lambda s, p: reword(p)))
    assert result.source == "model" and result.rejections == ()


# ---- the Verifier is the gate
def test_an_invented_number_is_blocked_and_the_template_is_used(goal):
    detection, context = goal
    result = explain(detection, context, model=Narrator(lambda s, p: reword(p).replace("minute", "97th minute")))
    assert result.source == "template" and result.rejected_claims >= 1
    assert result.claims == template_claims(detection, context)


def test_a_gendered_pronoun_is_fed_back_and_a_corrected_answer_is_accepted(goal):
    detection, context = goal
    prompts = []

    def model(system, prompt):
        prompts.append(prompt)
        text = reword(prompt)
        return text.replace(" from ", " to give his side the lead from ") if "rejected" not in prompt else text

    result = explain(detection, context, model=Narrator(model))
    assert result.source == "model_retry" and result.attempts == 2
    assert "gendered pronoun" in prompts[1]


def test_a_goal_that_no_longer_says_it_was_scored_is_blocked(goal):
    detection, context = goal
    vague = lambda system, prompt: reword(prompt).replace("found the net", "hit the target")
    result = explain(detection, context, model=Narrator(vague), max_retries=0)
    assert result.source == "template"
    assert any("expected one of" in r for f in result.rejections for r in f.reasons)


def test_naming_the_wrong_player_is_blocked(goal):
    detection, context = goal
    shot = context.event(detection.event_ids[-1])
    scorer = context.player(shot.player_id).name
    other = next(p.name for p in context.info.players if p.team == shot.team and p.player_id != shot.player_id)

    def swap(system, prompt):
        return reword(prompt).replace(scorer, other)

    assert explain(detection, context, model=Narrator(swap), max_retries=0).source == "template"


def test_a_wrong_number_of_sentences_is_an_explainer_error_not_a_crash(goal):
    detection, context = goal
    result = explain(detection, context, model=Narrator(lambda s, p: "1. Only one line."), max_retries=0)
    assert result.source == "template"
    assert any("numbered sentences" in reason for failure in result.rejections for reason in failure.reasons)


def test_a_model_that_raises_falls_back_to_the_template(goal):
    detection, context = goal

    def down(system, prompt):
        raise ConnectionError("ollama is not running")

    result = explain(detection, context, model=Narrator(down))
    assert result.source == "template"
    assert any("ollama is not running" in r for f in result.rejections for r in f.reasons)


# ---- configuration from a spec string
def test_a_blank_spec_means_no_model():
    assert narrator_from_spec(None) is None and narrator_from_spec("") is None and narrator_from_spec("  ") is None


def test_an_ollama_spec_builds_a_narrator_for_that_model_and_host(monkeypatch):
    seen = {}

    def fake_generate(model, host=None, **kwargs):
        seen["args"] = (model, host)
        return lambda system, prompt: "1. ok"

    monkeypatch.setattr(narrator_module, "ollama_generate", fake_generate)
    narrator = narrator_from_spec("ollama:qwen2.5:3b", host="http://127.0.0.1:11434")
    assert isinstance(narrator, Narrator) and seen["args"] == ("qwen2.5:3b", "http://127.0.0.1:11434")


@pytest.mark.parametrize("spec", ["openai:gpt", "qwen2.5:3b", "ollama:"])
def test_an_unknown_provider_or_a_missing_model_name_is_a_clear_error(spec):
    with pytest.raises(ValueError, match="MATCHDESK_MODEL"):
        narrator_from_spec(spec)


# ---- the Agent Framework adapter
def test_ollama_generate_builds_an_agent_framework_agent_and_returns_its_text(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, host=None, model=None):
            seen["client"] = (host, model)

    class FakeResult:
        text = "1. Fine."

    class FakeAgent:
        def __init__(self, client, name, instructions, default_options):
            seen["agent"] = (name, instructions, default_options)

        async def run(self, prompt):
            seen["prompt"] = prompt
            return FakeResult()

    monkeypatch.setattr(narrator_module, "OllamaChatClient", FakeClient)
    monkeypatch.setattr(narrator_module, "Agent", FakeAgent)
    generate = ollama_generate("qwen2.5:3b", host="http://localhost:11434", temperature=0.0, seed=7)
    assert generate("SYSTEM TEXT", "PROMPT TEXT") == "1. Fine."
    assert seen["client"] == ("http://localhost:11434", "qwen2.5:3b")
    assert seen["agent"][1] == "SYSTEM TEXT" and seen["agent"][2]["temperature"] == 0.0 and seen["agent"][2]["seed"] == 7
    assert seen["prompt"] == "PROMPT TEXT"
