"""Replay a reviewed Bociek feedback example offline against its synthetic home."""

import argparse
import asyncio
import json
from pathlib import Path

from custom_components.polish_context_voice.core.feedback import (
    FIXTURE,
    FeedbackError,
    corpus_case,
    fixture,
)

from .corpus import run_case, validate_cases

MAX_REPORT_BYTES = 32768

async def replay(report):
    case = corpus_case(report)
    home = fixture()
    validate_cases([case], {FIXTURE: home})
    return {"case": case, "fixture": home, "result": await run_case(case, home)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="JSON example or dialogue copied from HA")
    parser.add_argument("--output", type=Path, required=True, help="New file for the result")
    args = parser.parse_args()
    try:
        with args.report.open("rb") as file:
            data = file.read(MAX_REPORT_BYTES + 1)
        if len(data) > MAX_REPORT_BYTES:
            raise FeedbackError("file_size")
        replayed = asyncio.run(replay(json.loads(data)))
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps(replayed, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, RecursionError) as error:
        parser.exit(2, f"Cannot replay feedback: {error}\n")
    result = replayed["result"]
    if result["passed"]:
        print(f"{result['id']} passes the expected plan on {FIXTURE}.")
        return
    failures = "; ".join(
        f"turn {turn['turn']}: {', '.join(turn['mismatches'])}"
        for turn in result["turns"] if not turn["passed"]
    )
    parser.exit(1, f"{result['id']} reproduced a mismatch: {failures}\n")


if __name__ == "__main__":
    main()
