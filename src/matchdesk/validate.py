"""Stream rules from SCHEMA.md section 4. Returns human-readable errors; empty list means valid."""
from .schema import Event, MatchInfo, MatchTruth

OWN_FRAME_TYPES = ("pass", "shot")
DEFENDER_TYPES = ("tackle", "pressure")


def validate_stream(info: MatchInfo, events, truth: MatchTruth | None = None) -> list[str]:
    errors: list[str] = []
    events = list(events)
    if not events:
        return ["empty stream"]
    ids = {p.player_id: p.team for p in info.players}
    last_s, last_period, possessor = -1.0, 1, None
    kinds = [e.kind for e in events if e.type == "meta"]
    for i, e in enumerate(events):
        errors += _check_order(i, e, info, last_s, last_period)
        last_s, last_period = e.match_s, e.period
        errors += _check_actors(e, ids)
        possessor, problem = _advance_possession(e, possessor)
        if problem:
            errors.append(f"{e.event_id}: {problem}")
    errors += _check_meta(events, kinds)
    if truth is not None:
        goals = sum(1 for e in events if e.type == "shot" and e.outcome == "goal")
        if goals != sum(truth.score.values()):
            errors.append(f"goal count {goals} != truth score {truth.score}")
    return errors


def _check_order(i: int, e: Event, info: MatchInfo, last_s: float, last_period: int) -> list[str]:
    out = []
    if e.seq != i:
        out.append(f"{e.event_id}: seq {e.seq} != position {i}")
    if e.match_id != info.match_id:
        out.append(f"{e.event_id}: wrong match_id")
    if e.match_s < last_s:
        out.append(f"{e.event_id}: match_s goes backwards")
    if e.period < last_period:
        out.append(f"{e.event_id}: period goes backwards")
    return out


def _check_actors(e: Event, ids: dict[str, str]) -> list[str]:
    out = []
    for field in ("player_id", "recipient_id"):
        pid = getattr(e, field, None)
        if pid is not None and ids.get(pid) != e.team:
            out.append(f"{e.event_id}: {field} {pid} is not on team {e.team}")
    target = getattr(e, "target_id", None)
    if target is not None and ids.get(target) == e.team:
        out.append(f"{e.event_id}: target {target} is a teammate")
    return out


def _advance_possession(e: Event, possessor: str | None) -> tuple[str | None, str | None]:
    if e.type == "meta":
        return (e.team if e.kind in ("kickoff", "second_half_start") else possessor), None
    if e.type == "possession_change":
        return e.team, ("possession_change to team already in possession" if e.team == possessor else None)
    if e.type in OWN_FRAME_TYPES and e.team != possessor:
        return possessor, f"{e.type} by {e.team} without possession"
    if e.type in DEFENDER_TYPES and e.team == possessor:
        return possessor, f"{e.type} by the team in possession"
    return possessor, None


def _check_meta(events, kinds: list[str]) -> list[str]:
    out = []
    first, last = events[0], events[-1]
    if not (first.type == "meta" and first.kind == "kickoff"):
        out.append("stream must begin with meta/kickoff")
    if not (last.type == "meta" and last.kind == "full_time"):
        out.append("stream must end with meta/full_time")
    for kind in ("half_time", "second_half_start"):
        if kinds.count(kind) != 1:
            out.append(f"expected exactly one {kind}")
    return out
