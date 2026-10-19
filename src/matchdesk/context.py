"""What has been seen so far in a match: events by id, players, clubs, and the running score.

The Verifier resolves evidence only against this context, so an event id from the future, or an invented one,
is unknown by construction."""
from collections.abc import Sequence

from .schema import Event, MatchInfo, Player

SECONDS_PER_MINUTE = 60.0


class MatchContext:
    def __init__(self, info: MatchInfo):
        self.info = info
        self._events: list[Event] = []
        self._by_id: dict[str, Event] = {}
        self._score_after: dict[str, dict[str, int]] = {}
        self._score = {"H": 0, "A": 0}
        self._players = {p.player_id: p for p in info.players}

    def add(self, event: Event) -> None:
        if event.event_id in self._by_id:
            raise ValueError(f"duplicate event id {event.event_id}")
        if event.type == "shot" and event.outcome == "goal":
            self._score[event.team] += 1
        self._events.append(event)
        self._by_id[event.event_id] = event
        self._score_after[event.event_id] = dict(self._score)

    def events(self) -> Sequence[Event]:
        return self._events

    def event(self, event_id: str) -> Event | None:
        return self._by_id.get(event_id)

    def player(self, player_id: str) -> Player | None:
        return self._players.get(player_id)

    def club_name(self, team: str) -> str:
        return self.info.clubs[team].name

    def score_after(self, event_id: str) -> dict[str, int] | None:
        score = self._score_after.get(event_id)
        return None if score is None else dict(score)

    @staticmethod
    def minute_at(match_s: float) -> int:
        return int(match_s // SECONDS_PER_MINUTE) + 1

    def minute(self, event: Event) -> int:
        return self.minute_at(event.match_s)
