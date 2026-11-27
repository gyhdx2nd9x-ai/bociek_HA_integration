"""Check the lab's reproducible Core pin against Home Assistant's stable channel."""

import argparse
import json
import re
from pathlib import Path
from urllib.request import urlopen

STABLE_CHANNEL = "https://version.home-assistant.io/stable.json"
LATEST_RELEASE = "https://api.github.com/repos/home-assistant/core/releases/latest"
REQUIREMENTS = Path(__file__).resolve().parents[1] / "lab/requirements.txt"


def core_pin(requirements: str) -> str:
    pins = re.findall(r"^homeassistant==([\d.]+)\s*$", requirements, re.M)
    if len(pins) != 1:
        raise ValueError("Pin exactly one stable Home Assistant Core in lab/requirements.txt.")
    return pins[0]


def verify_pin(requirements: str, stable: str) -> str:
    pinned = core_pin(requirements)
    if pinned != stable:
        raise ValueError(
            f"The lab pins HA Core {pinned}; stable is {stable}. "
            "Update the pin and the native Assist/MCP dependencies before validation."
        )
    return pinned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Check a stable version verified separately")
    args = parser.parse_args()
    try:
        stable = args.version
        if stable is None:
            try:
                with urlopen(STABLE_CHANNEL, timeout=10) as response:
                    stable = json.load(response)["homeassistant"]["default"]
            except OSError:
                with urlopen(LATEST_RELEASE, timeout=10) as response:
                    stable = json.load(response)["tag_name"]
        if not isinstance(stable, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", stable):
            raise ValueError("The published release is not a stable version number.")
        version = verify_pin(REQUIREMENTS.read_text(), stable)
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise SystemExit(f"Cannot validate latest stable HA Core: {error}") from error
    source = "the supplied version" if args.version else "the stable channel"
    print(f"HA Core {version} matches {source}.")


if __name__ == "__main__":
    main()
