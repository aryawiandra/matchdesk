"""Fault injection for the Verifier: corrupt a good claim in a known way. Used by tests and the eval harness to
measure how many wrong claims the Verifier blocks."""
from collections.abc import Callable
from dataclasses import replace

from .claims import Claim, Fact
from .context import MatchContext
from .verifier import NUMBER, PLAYER_FIELDS, half_unit

WRONG_FACT_DELTA = 7.0
UNBACKED_SUFFIX = " That is the 97th such moment."

Fault = Callable[[Claim, MatchContext], Claim | None]


def _no_evidence(claim: Claim, ctx: MatchContext) -> Claim | None:
    return replace(claim, evidence_ids=())


def _fabricated_event(claim: Claim, ctx: MatchContext) -> Claim | None:
    return replace(claim, evidence_ids=claim.evidence_ids + (f"{ctx.info.match_id}-99999",))


def _wrong_fact(claim: Claim, ctx: MatchContext) -> Claim | None:
    """Change one stated number in both the text and its fact, so the claim is self-consistent but false."""
    for index, fact in enumerate(claim.facts):
        for match in NUMBER.finditer(claim.text):
            token = match.group()
            if abs(float(token) - abs(fact.value)) > half_unit(token):
                continue
            decimals = len(token.split(".")[1]) if "." in token else 0
            new_value = abs(fact.value) + WRONG_FACT_DELTA
            text = claim.text[:match.start()] + f"{new_value:.{decimals}f}" + claim.text[match.end():]
            facts = list(claim.facts)
            facts[index] = Fact(fact.name, new_value, fact.event_id, fact.team, fact.at_s)
            return replace(claim, text=text, facts=tuple(facts))
    return None


def _unbacked_number(claim: Claim, ctx: MatchContext) -> Claim | None:
    return replace(claim, text=claim.text + UNBACKED_SUFFIX)


def _wrong_player(claim: Claim, ctx: MatchContext) -> Claim | None:
    if not claim.player_ids:
        return None
    cited = [e for e in (ctx.event(eid) for eid in claim.evidence_ids) if e is not None]
    involved = {getattr(e, field) for e in cited for field in PLAYER_FIELDS if hasattr(e, field)}
    stranger = next((p.player_id for p in ctx.info.players if p.player_id not in involved), None)
    return None if stranger is None else replace(claim, player_ids=(stranger,))


FAULTS: dict[str, Fault] = {
    "no_evidence": _no_evidence,
    "fabricated_event": _fabricated_event,
    "wrong_fact": _wrong_fact,
    "unbacked_number": _unbacked_number,
    "wrong_player": _wrong_player,
}


def inject(name: str, claim: Claim, ctx: MatchContext) -> Claim | None:
    """The corrupted claim, or None when this fault does not apply to the claim. Unknown names raise KeyError."""
    return FAULTS[name](claim, ctx)
