"""Import entity IDs as a disabled reference, without inventing HA metadata."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from custom_components.polish_context_voice.core.home_model import Entity, HomeModel


def import_ids(data):
    if not isinstance(data, list) or not all(
        isinstance(e, str) and re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", e) for e in data
    ):
        raise ValueError("Expected a JSON list of entity IDs")
    ids = sorted(set(data))
    home = HomeModel(
        entities=[Entity(e, e, None, exposed=False, available=False) for e in ids],
        satellites={e: None for e in ids if e.startswith("assist_satellite.")},
    )
    return home, {
        "source": "User-supplied entity ID inventory; metadata unknown",
        "entity_count": len(ids),
        "duplicates": len(data) - len(ids),
        "domains": dict(sorted(Counter(e.split(".")[0] for e in ids).items())),
        "missing_metadata": [
            "areas",
            "floors",
            "aliases",
            "labels",
            "device_class",
            "assist_exposure",
            "states",
            "group_members",
            "device_registry",
        ],
        "satellite_ids": sorted(home.satellites),
        "light_ids": [e for e in ids if e.startswith("light.")],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("local/inventory.json"))
    parser.add_argument("--report", type=Path, default=Path("local/inventory_report.json"))
    args = parser.parse_args()
    home, report = import_ids(json.loads(args.input.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(home.export(), indent=2, ensure_ascii=False) + "\n")
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ("entity_count", "domains", "satellite_ids")}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
