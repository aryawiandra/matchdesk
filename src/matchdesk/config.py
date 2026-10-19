"""Generator configuration and indicator/detection thresholds (config/thresholds.toml)."""
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "config" / "thresholds.toml"


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int
    period_s: float = 2700.0
    match_id: str | None = None

    @property
    def resolved_match_id(self) -> str:
        return self.match_id or f"M{self.seed:04d}"


@dataclass(frozen=True)
class Thresholds:
    sprint_speed_kmh: float = 25.0
    distance_step_m: float = 2000.0
    speed_chip_cooldown_s: float = 60.0
    big_chance_xg: float = 0.30
    swing_delta_dominance: float = 0.70
    pressing_spell_level: float = 0.65
    pressing_spell_min_s: float = 180.0
    sprint_min_seconds: int = 3


def load_thresholds(path: Path | str | None = None) -> Thresholds:
    """Read thresholds from TOML; missing file or keys fall back to the documented defaults."""
    target = Path(path) if path is not None else DEFAULT_THRESHOLDS_PATH
    base = Thresholds()
    if not target.exists():
        return base
    with target.open("rb") as handle:
        data = tomllib.load(handle)
    flat = {**data.get("indicators", {}), **data.get("key_moments", {})}
    known = {k: v for k, v in flat.items() if hasattr(base, k)}
    if "sprint_min_seconds" in known:
        known["sprint_min_seconds"] = int(known["sprint_min_seconds"])
    return replace(base, **known)
