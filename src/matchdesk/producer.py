"""Producer: verified explanations become timed overlays (SCHEMA.md section 7). No model is involved here; every
sentence already passed the Verifier, and every overlay that makes a claim cites event ids."""
import math

from . import metrics
from .analyst import Detection
from .config import Thresholds
from .context import MatchContext
from .explainer import Explanation
from .overlay import (
    ExplanationOverlay, ExplanationPayload, KeyMomentOverlay, KeyMomentPayload, Lang, Overlay, Persona,
    PlayerTagOverlay, PlayerTagPayload, ScoreboardOverlay, ScoreboardPayload, Sentence,
)

TICK_S = 10.0
TAG_DURATION_S = 6.0
TAG_PRIORITY = 2
# How long each kind stays on screen and how much it matters (1-5) [A: design choices].
DURATION_S = {"goal": 8.0, "big_chance": 6.0, "sprint_burst": 4.0, "pressing_spell": 8.0, "momentum_swing": 6.0}
PRIORITY = {"goal": 5, "big_chance": 4, "momentum_swing": 3, "pressing_spell": 3, "sprint_burst": 2}
# Which cited event is "the" moment (index into the detection's event ids); kinds without a single point are absent.
POINT_EVENT = {"goal": -1, "big_chance": -1, "sprint_burst": 0}


# Words the Producer writes itself (accessibility text); the sentences come from the Explainer.
STRINGS = {
    "en": {"tag": "Player tag", "shirt": "shirt", "kmh": "km/h", "covered": "km covered"},
    "id": {"tag": "Label pemain", "shirt": "nomor", "kmh": "km/jam", "covered": "km ditempuh"},
}


def clock(match_s: float) -> str:
    minutes, seconds = divmod(int(match_s), 60)
    return f"{minutes:02d}:{seconds:02d}"


class Producer:
    def __init__(self, ctx: MatchContext, thresholds: Thresholds):
        self._ctx = ctx
        self._th = thresholds

    def scoreboard(self, at_s: float, tick_s: float = TICK_S, lang: Lang = "en", persona: Persona = "fan") -> ScoreboardOverlay:
        score = self._ctx.score_at(at_s)
        home, away = self._ctx.club_name("H"), self._ctx.club_name("A")
        return ScoreboardOverlay(
            overlay_id=f"OV-{self._ctx.info.match_id}-scoreboard-{int(round(at_s * 10)):06d}-{lang}-{persona}",
            match_id=self._ctx.info.match_id, start_s=at_s, end_s=at_s + tick_s, priority=1, lang=lang,
            persona=persona, evidence_event_ids=(),
            a11y_text=f"{home} {score['H']}, {away} {score['A']}, {clock(at_s)}.",
            payload=ScoreboardPayload(score=score, clock=clock(at_s), home=home, away=away),
        )

    def for_explanation(self, explanation: Explanation, lang: Lang = "en", persona: Persona = "fan") -> tuple[Overlay, ...]:
        detection, claims = explanation.detection, explanation.claims
        start = detection.detected_at_s
        evidence = tuple(dict.fromkeys(eid for claim in claims for eid in claim.evidence_ids))
        stem = (f"OV-{self._ctx.info.match_id}-{{type}}-{detection.kind}-{int(round(start * 10)):06d}-"
                f"{evidence[0].rsplit('-', 1)[1]}-{lang}-{persona}")
        point = self._point_event(detection)
        detail = claims[1:] if persona == "analyst" else ()  # the headline is claims[0]; analysts also get the rest
        common = dict(match_id=self._ctx.info.match_id, start_s=start, lang=lang, persona=persona)
        priority = PRIORITY[detection.kind]
        overlays: list[Overlay] = [
            KeyMomentOverlay(
                overlay_id=stem.format(type="key_moment"), end_s=start + DURATION_S[detection.kind], priority=priority,
                evidence_event_ids=evidence, a11y_text=claims[0].text, **common,
                payload=KeyMomentPayload(kind=detection.kind, headline=claims[0].text, team=detection.team,
                                         x=getattr(point, "x", None), y=getattr(point, "y", None),
                                         source=explanation.source),
            ),
        ]
        if detail:
            overlays.append(ExplanationOverlay(
                overlay_id=stem.format(type="explanation"), end_s=start + DURATION_S[detection.kind],
                priority=max(1, priority - 1), evidence_event_ids=tuple(dict.fromkeys(e for c in detail for e in c.evidence_ids)),
                a11y_text=" ".join(c.text for c in detail), **common,
                payload=ExplanationPayload(sentences=tuple(Sentence(text=c.text, evidence_event_ids=c.evidence_ids) for c in detail)),
            ))
        if point is not None and detection.player_id is not None:
            overlays.append(self._player_tag(stem.format(type="player_tag"), point, detection.player_id, common))
        return tuple(overlays)

    def _point_event(self, detection: Detection):
        index = POINT_EVENT.get(detection.kind)
        return None if index is None else self._ctx.event(detection.event_ids[index])

    def _player_tag(self, overlay_id: str, event, player_id: str, common: dict) -> PlayerTagOverlay:
        player = self._ctx.player(player_id)
        fired = {chip.kind for chip in metrics.indicator_chips(self._ctx.events(), self._th) if chip.event_id == event.event_id}
        speed = event.actor_speed_kmh if "speed" in fired else None
        distance = (math.floor(event.actor_distance_m / self._th.distance_step_m) * self._th.distance_step_m / 1000.0
                    if "distance" in fired else None)
        words = STRINGS[common["lang"]]
        text = f"{words['tag']}: {player.name}, {words['shirt']} {player.shirt}"
        text += f", {speed:.0f} {words['kmh']}" if speed is not None else ""
        text += f", {distance:.0f} {words['covered']}" if distance is not None else ""
        return PlayerTagOverlay(
            overlay_id=overlay_id, end_s=common["start_s"] + TAG_DURATION_S, priority=TAG_PRIORITY,
            evidence_event_ids=(event.event_id,), a11y_text=text + ".", **common,
            payload=PlayerTagPayload(name=player.name, shirt=player.shirt, team=player.team,
                                     speed_chip_kmh=speed, distance_chip_km=distance),
        )
