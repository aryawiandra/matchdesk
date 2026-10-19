"""Invented clubs and players. Names are composed from made-up syllables; any match with a real
person or club is coincidental, and no real name list is used."""
import random

from .schema import Club, Player

CLUBS = (
    ("HAL", "Halden Rovers"), ("MAR", "Marlow Athletic"), ("COR", "Corvale FC"),
    ("BRI", "Brightmoor United"), ("SEN", "Sennet Wanderers"), ("ASH", "Ashcombe Town"),
    ("TEL", "Telmar City"), ("VOR", "Vorwick Albion"), ("KES", "Kesterly Park"),
    ("DUN", "Dunmere Argyle"),
)
GIVEN = ("Tor", "Vel", "Ar", "Kes", "Mir", "Dal", "Fen", "Lor", "Bren", "Cal", "Syl", "Ren", "Oth", "Jav", "Pel", "Nik")
GIVEN_END = ("vin", "dan", "ric", "lo", "tan", "mo", "ek", "ar", "ius", "en")
FAMILY = ("Hal", "Brek", "Mar", "Os", "Wen", "Dra", "Kol", "Sar", "Vash", "Quin", "Tal", "Rav")
FAMILY_END = ("brek", "ford", "ani", "ovic", "wick", "ndale", "mere", "tov", "sen", "ley")

# (role, shirt, slot_x, slot_y) in the team's own attacking frame (4-4-2).
SLOTS = (
    ("GK", 1, 5.0, 34.0),
    ("DEF", 2, 25.0, 10.0), ("DEF", 3, 25.0, 25.0), ("DEF", 4, 25.0, 43.0), ("DEF", 5, 25.0, 58.0),
    ("MID", 6, 52.0, 10.0), ("MID", 7, 52.0, 25.0), ("MID", 8, 52.0, 43.0), ("MID", 11, 52.0, 58.0),
    ("FWD", 9, 78.0, 26.0), ("FWD", 10, 78.0, 42.0),
)


def pick_clubs(rng: random.Random) -> tuple[Club, Club]:
    home, away = rng.sample(CLUBS, 2)
    return Club(club_id=home[0], name=home[1]), Club(club_id=away[0], name=away[1])


def make_players(rng: random.Random) -> tuple[Player, ...]:
    used: set[str] = set()
    players: list[Player] = []
    for team in ("H", "A"):
        for role, shirt, _, _ in SLOTS:
            name = _unique_name(rng, used)
            players.append(Player(player_id=f"{team}{shirt:02d}", name=name, shirt=shirt, role=role, team=team))
    return tuple(players)


def _unique_name(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.choice(GIVEN)}{rng.choice(GIVEN_END)} {rng.choice(FAMILY)}{rng.choice(FAMILY_END)}"
        if name not in used:
            used.add(name)
            return name
