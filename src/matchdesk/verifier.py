"""Verifier gate: a claim is published only if its evidence exists and every number it states is recomputed
from the events. Pure code, no model. A claim passes or is rejected with reasons; it is never edited."""
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .claims import TOLERANCE, Claim, recompute
from .context import MatchContext

NUMBER = re.compile(r"\d+(?:\.\d+)?")
WORD = re.compile(r"[a-z][a-z'-]*")
PLAYER_FIELDS = ("player_id", "recipient_id", "target_id")
MIN_NAME_PART = 4  # shorter name parts ("Al", "Van") are too ambiguous to match on their own
# Words shared by many club names; they never identify one club on their own.
GENERIC_CLUB_WORDS = frozenset({"fc", "town", "athletic", "rovers", "united", "city", "county", "wanderers", "albion"})
# Wording the data cannot support [A: deterministic lexical rules, METRICS.md section 11].
PRONOUNS = frozenset({"he", "him", "his", "himself", "she", "her", "hers", "herself"})
# Stems, so that "dramatic", "dramatically" and "dramatics" are all caught.
HYPE_STEMS = (
    "brilliant", "stunning", "incredib", "dominan", "dominat", "dramatic", "sensation", "superb", "spectacul",
    "unstoppab", "magnific", "clinical", "world-class", "decisiv",
)
CAUSAL = ("because", "due to", "thanks to", "as a result", "caused by", "led to")


@dataclass(frozen=True)
class Rejection:
    claim: Claim
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class VerificationResult:
    accepted: tuple[Claim, ...]
    rejected: tuple[Rejection, ...]

    @property
    def ok(self) -> bool:
        return bool(self.accepted) and not self.rejected


def half_unit(token: str) -> float:
    decimals = len(token.split(".")[1]) if "." in token else 0
    return 0.5 * 10 ** -decimals


def _evidence_reasons(claim: Claim, ctx: MatchContext) -> list[str]:
    if not claim.evidence_ids:
        return ["no evidence cited"]
    return [f"unknown event id {eid}" for eid in claim.evidence_ids if ctx.event(eid) is None]


def _fact_reasons(claim: Claim, ctx: MatchContext) -> list[str]:
    reasons = []
    for fact in claim.facts:
        computed = recompute(fact, ctx)
        if computed is None:
            reasons.append(f"cannot verify {fact.name}")
        elif abs(computed - fact.value) > TOLERANCE[fact.name]:
            reasons.append(f"fact {fact.name} claims {fact.value:g} but the events give {computed:.2f}")
    return reasons


def _number_reasons(claim: Claim) -> list[str]:
    reasons = []
    for token in NUMBER.findall(claim.text):
        number = float(token)
        backed = any(
            abs(number - abs(fact.value)) <= max(TOLERANCE.get(fact.name, 0.0), half_unit(token))
            for fact in claim.facts
        )
        if not backed:
            reasons.append(f"unsupported number {token}")
    return reasons


def _player_reasons(claim: Claim, ctx: MatchContext) -> list[str]:
    cited = [e for e in (ctx.event(eid) for eid in claim.evidence_ids) if e is not None]
    involved = {getattr(e, field) for e in cited for field in PLAYER_FIELDS if hasattr(e, field)}
    reasons = []
    for pid in claim.player_ids:
        if ctx.player(pid) is None:
            reasons.append(f"unknown player {pid}")
        elif pid not in involved:
            reasons.append(f"player {pid} is not in the cited events")
    return reasons


def _name_parts(name: str) -> set[str]:
    return {w for w in WORD.findall(name.lower()) if len(w) >= MIN_NAME_PART}


def _name_reasons(claim: Claim, ctx: MatchContext) -> list[str]:
    """A named player must be one the claim cites, and a named club must be one the cited events involve."""
    words = set(WORD.findall(claim.text.lower()))
    cited_teams = {e.team for e in (ctx.event(eid) for eid in claim.evidence_ids) if e is not None}
    cited_parts = set().union(*[_name_parts(p.name) for p in ctx.info.players if p.player_id in claim.player_ids])
    reasons = []
    for player in ctx.info.players:
        if player.player_id in claim.player_ids:
            continue
        if (_name_parts(player.name) - cited_parts) & words:
            reasons.append(f"names player {player.name} who is not cited")
    for team, club in ctx.info.clubs.items():
        distinctive = {w for w in WORD.findall(club.name.lower()) if w not in GENERIC_CLUB_WORDS and len(w) >= MIN_NAME_PART}
        if team not in cited_teams and distinctive & words:
            reasons.append(f"names club {club.name} that the cited events do not involve")
    return reasons


def _wording_reasons(claim: Claim) -> list[str]:
    text = claim.text.lower()
    words = set(WORD.findall(text))
    reasons = [f"gendered pronoun '{w}' (the data does not give players' gender)" for w in sorted(words & PRONOUNS)]
    reasons += [f"unsupported wording '{w}'" for w in sorted(words) if w.startswith(HYPE_STEMS)]
    reasons += [f"unsupported causal wording '{p}'" for p in CAUSAL if re.search(rf"\b{re.escape(p)}\b", text)]
    return reasons


def check_claim(claim: Claim, ctx: MatchContext) -> list[str]:
    reasons = [] if claim.text.strip() else ["empty text"]
    reasons += _evidence_reasons(claim, ctx)
    reasons += _fact_reasons(claim, ctx)
    reasons += _number_reasons(claim)
    reasons += _player_reasons(claim, ctx)
    reasons += _name_reasons(claim, ctx)
    reasons += _wording_reasons(claim)
    return reasons


def verify_claims(claims: Sequence[Claim], ctx: MatchContext) -> VerificationResult:
    accepted: list[Claim] = []
    rejected: list[Rejection] = []
    for claim in claims:
        reasons = check_claim(claim, ctx)
        if reasons:
            rejected.append(Rejection(claim, tuple(reasons)))
        else:
            accepted.append(claim)
    return VerificationResult(tuple(accepted), tuple(rejected))
