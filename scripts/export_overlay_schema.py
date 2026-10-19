"""Write schema/overlay.schema.json from the overlay models: `uv run python scripts/export_overlay_schema.py`.

A test (tests/test_overlay.py) fails when the committed file and the models disagree."""
import json
from pathlib import Path

from matchdesk.overlay import overlay_json_schema

TARGET = Path(__file__).resolve().parents[1] / "schema" / "overlay.schema.json"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(overlay_json_schema(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {TARGET}")


if __name__ == "__main__":
    main()
