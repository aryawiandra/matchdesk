"""Full-time recap: what happened, in claims the Verifier checks like any other.

The result sentence is new and proved by the final score and by events of both clubs. Goals and key moments reuse the
sentences already written and verified during the match (template or model), so the recap never says anything the
match view did not. The Producer verifies every recap sentence once more before showing it."""
from collections.abc import Sequence
from dataclasses import dataclass

from .claims import Claim, Fact
from .context import MatchContext
from .explainer import Explanation

MAX_GOALS = 6
TITLES = {
    "en": {"result": "Result", "goals": "Goals", "moments": "Key moments"},
    "id": {"result": "Hasil", "goals": "Gol", "moments": "Momen kunci"},
}
RESULT_TEXT = {
    "en": "{home} {h}-{a} {away} at full time.",
    "id": "{home} {h}-{a} {away} di akhir pertandingan.",
}
# Key moments the analyst view adds: the strongest of each kind, in this order.
MOMENT_KINDS = ("momentum_swing", "pressing_spell", "sprint_burst")


@dataclass(frozen=True)
class RecapSection:
    key: str
    title: str
    claims: tuple[Claim, ...]


def _result_claim(ctx: MatchContext, lang: str) -> Claim:
    events = ctx.events()
    final = events[-1]
    score = ctx.score_after(final.event_id)
    last_of_each_club = [next((e.event_id for e in reversed(events) if e.team == team and e.type != "meta"), None)
                         for team in ("H", "A")]
    cited = tuple(dict.fromkeys([eid for eid in last_of_each_club if eid] + [final.event_id]))
    text = RESULT_TEXT[lang].format(home=ctx.club_name("H"), away=ctx.club_name("A"), h=score["H"], a=score["A"])
    facts = (Fact("score_home", score["H"], final.event_id), Fact("score_away", score["A"], final.event_id))
    return Claim(text, cited, facts, lang=lang)


def _strongest(explanations: Sequence[Explanation], kind: str) -> Claim | None:
    pool = [e for e in explanations if e.detection.kind == kind]
    return max(pool, key=lambda e: e.detection.magnitude).claims[0] if pool else None


def recap_sections(explanations: Sequence[Explanation], ctx: MatchContext, lang: str, persona: str) -> tuple[RecapSection, ...]:
    titles = TITLES[lang]
    sections = [RecapSection("result", titles["result"], (_result_claim(ctx, lang),))]
    goals = tuple(e.claims[0] for e in explanations if e.detection.kind == "goal")[:MAX_GOALS]
    if goals:
        sections.append(RecapSection("goals", titles["goals"], goals))
    if persona == "analyst":
        moments = tuple(c for c in (_strongest(explanations, kind) for kind in MOMENT_KINDS) if c is not None)
        if moments:
            sections.append(RecapSection("moments", titles["moments"], moments))
    return tuple(sections)
