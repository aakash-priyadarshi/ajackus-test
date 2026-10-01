"""Run the golden-set evaluation against the real OpenAI extraction client."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from app.llm_client import LLMError, OpenAIClient

DEFAULT_THRESHOLD = 0.80


def score_record(record: dict[str, Any], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    expected_kind = record["expected_kind"]
    matching = [item for item in predictions if item["kind"] == expected_kind]
    matching_text = [item["text"] for item in matching]
    return {
        "kind": bool(matching),
        "restricted": any(
            item["restricted"] == record["expected_restricted"] for item in matching
        ),
        "keywords": bool(
            any(
                keyword.casefold() in selected["text"].casefold()
                for keyword in record["expected_keywords"]
                for selected in matching
            )
        ),
        "predicted_kinds": sorted({item["kind"] for item in predictions}),
        "selected_text": " | ".join(matching_text),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")

    client = OpenAIClient()
    if not client.configured:
        print("OPENAI_API_KEY is not configured; set it in the project-root .env.", file=sys.stderr)
        return 2

    golden_path = ROOT / "eval" / "golden_set.json"
    records = json.loads(golden_path.read_text(encoding="utf-8"))
    checks = 0
    passed = 0

    for index, record in enumerate(records, start=1):
        try:
            predictions = client.extract_attributes(record["message"])
            result = score_record(record, predictions)
        except LLMError as error:
            result = {
                "kind": False,
                "restricted": False,
                "keywords": False,
                "predicted_kinds": [],
                "selected_text": "",
            }
            print(f"record {index}: API failure: {error}")

        record_passed = sum(bool(result[key]) for key in ("kind", "restricted", "keywords"))
        checks += 3
        passed += record_passed
        print(
            f"record {index}: kind={'PASS' if result['kind'] else 'FAIL'}, "
            f"restricted={'PASS' if result['restricted'] else 'FAIL'}, "
            f"keywords={'PASS' if result['keywords'] else 'FAIL'}, "
            f"predicted_kinds={result['predicted_kinds']}, "
            f"text={result['selected_text']!r} ({record_passed}/3)"
        )

    score = passed / checks if checks else 0.0
    passed_threshold = score >= args.threshold
    print(
        f"overall: {passed}/{checks} = {score:.1%}; "
        f"threshold={args.threshold:.1%}; {'PASS' if passed_threshold else 'FAIL'}"
    )
    return 0 if passed_threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
