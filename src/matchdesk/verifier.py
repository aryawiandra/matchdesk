"""Verifier gate: a claim is published only if its evidence exists and every number it states is recomputed
from the events. Pure code, no model. A claim passes or is rejected with reasons; it is never edited."""
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .claims import TOLERANCE, Claim, recompute
from .context import MatchContext

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")  # Indonesian writes 3,5 where English writes 3.5
WORD = re.compile(r"[a-z][a-z'-]*")
PLAYER_FIELDS = ("player_id", "recipient_id", "target_id")
MIN_NAME_PART = 4  # shorter name parts ("Al", "Van") are too ambiguous to match on their own
# Words shared by many club names; they never identify one club on their own.
GENERIC_CLUB_WORDS = frozenset({"fc", "town", "athletic", "rovers", "united", "city", "county", "wanderers", "albion"})
# Wording the data cannot support, per language [A: deterministic lexical rules, METRICS.md section 11].
# Hype is matched by stem so that "dramatic" and "dramatically" are both caught; multi-word entries match as phrases.
# `foreign` holds common words of the OTHER language: finding one means the sentence is in the wrong language.
@dataclass(frozen=True)
class Lexicon:
    pronouns: frozenset[str]
    hype_stems: tuple[str, ...]
    causal: tuple[str, ...]
    foreign: frozenset[str]


ENGLISH = Lexicon(
    pronouns=frozenset({"he", "him", "his", "himself", "she", "her", "hers", "herself"}),
    hype_stems=(
        "brilliant", "stunning", "incredib", "dominan", "dominat", "dramatic", "sensation", "superb", "spectacul",
        "unstoppab", "magnific", "clinical", "world-class", "decisiv", "swift", "rocket", "blaz", "thunder", "scorch",
        "fierce", "blister", "lethal", "stunner", "screamer", "howitzer", "unleash", "magical",
    ),
    causal=("because", "due to", "thanks to", "as a result", "caused by", "led to"),
    foreign=frozenset({"yang", "dengan", "pada", "dari", "dan", "untuk", "dalam", "adalah", "hingga", "selama"}),
)
INDONESIAN = Lexicon(
    pronouns=ENGLISH.pronouns,  # Indonesian "dia" and "ia" carry no gender; only an English slip-through is caught
    hype_stems=(
        "luar biasa", "spektakul", "menakjubkan", "memukau", "fantastis", "dramatis", "dominan", "dominasi", "brilian",
        "mematikan", "tak terbendung", "gemilang", "sensasional", "hebat", "menawan", "ajaib", "mengerikan",
    ),
    causal=("karena", "sebab", "berkat", "akibat", "sehingga", "gara-gara", "disebabkan"),
    foreign=frozenset({"the", "of", "and", "with", "from", "scored", "minute", "after", "into", "during", "while"}),
)
LEXICONS = {"en": ENGLISH, "id": INDONESIAN}


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


def number_value(token: str) -> float:
    return float(token.replace(",", "."))


def half_unit(token: str) -> float:
    parts = re.split(r"[.,]", token)
    decimals = len(parts[1]) if len(parts) > 1 else 0
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
        number = number_value(token)
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


def _hype_hits(words: set[str], text: str, stems: tuple[str, ...]) -> list[str]:
    hits = [w for w in sorted(words) if w.startswith(tuple(s for s in stems if " " not in s))]
    hits += [s for s in stems if " " in s and re.search(rf"\b{re.escape(s)}", text)]
    return hits


def _wording_reasons(claim: Claim) -> list[str]:
    lexicon = LEXICONS.get(claim.lang)
    if lexicon is None:
        return [f"no wording rules for language {claim.lang!r}"]
    text = claim.text.lower()
    words = set(WORD.findall(text))
    reasons = [f"gendered pronoun '{w}' (the data does not give players' gender)" for w in sorted(words & lexicon.pronouns)]
    reasons += [f"unsupported wording '{w}'" for w in _hype_hits(words, text, lexicon.hype_stems)]
    reasons += [f"unsupported causal wording '{p}'" for p in lexicon.causal if re.search(rf"\b{re.escape(p)}\b", text)]
    reasons += [f"wrong language: '{w}' is not {claim.lang}" for w in sorted(words & lexicon.foreign)]
    return reasons


def _says(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase.lower())}\b", text) is not None


def _meaning_reasons(claim: Claim) -> list[str]:
    """The sentence must still say what happened (a goal stays a goal) and must not say what would contradict it."""
    text = claim.text.lower()
    reasons = []
    if claim.requires_any and not any(_says(text, phrase) for phrase in claim.requires_any):
        reasons.append("does not say what happened (expected one of: " + ", ".join(claim.requires_any) + ")")
    reasons += [f"must not say '{phrase}'" for phrase in claim.forbids if _says(text, phrase)]
    return reasons


def check_claim(claim: Claim, ctx: MatchContext, lang: str | None = None) -> list[str]:
    reasons = [] if claim.text.strip() else ["empty text"]
    if lang is not None and claim.lang != lang:
        reasons.append(f"claim language {claim.lang} does not match the requested language {lang}")
    reasons += _evidence_reasons(claim, ctx)
    reasons += _fact_reasons(claim, ctx)
    reasons += _number_reasons(claim)
    reasons += _player_reasons(claim, ctx)
    reasons += _name_reasons(claim, ctx)
    reasons += _wording_reasons(claim)
    reasons += _meaning_reasons(claim)
    return reasons


def verify_claims(claims: Sequence[Claim], ctx: MatchContext, lang: str | None = None) -> VerificationResult:
    accepted: list[Claim] = []
    rejected: list[Rejection] = []
    for claim in claims:
        reasons = check_claim(claim, ctx, lang)
        if reasons:
            rejected.append(Rejection(claim, tuple(reasons)))
        else:
            accepted.append(claim)
    return VerificationResult(tuple(accepted), tuple(rejected))
