"""Grade synthetic Skill outputs against the public ranking contract, without model calls."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, pstdev

from pydantic import ValidationError

from hakimi_analysis.reminders import ReminderRankings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    skill = Path(__file__).resolve().parent.parent
    config = json.loads((skill / "evals/evals.json").read_text(encoding="utf-8"))
    runs = []
    for case, folder in zip(config["evals"], ["short-set", "two-actions"], strict=True):
        given = json.loads((skill / case["files"][0]).read_text(encoding="utf-8"))
        expected = {
            action["action_id"]: [tip["tip_id"] for tip in action["tips"]]
            for action in given["actions"]
        }
        metadata = {
            "eval_id": case["id"],
            "eval_name": folder,
            "prompt": case["prompt"],
            "assertions": case["expectations"],
        }
        (args.workspace / folder / "eval_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for configuration in ["with_skill", "without_skill"]:
            run_dir = args.workspace / folder / configuration
            try:
                ranked = ReminderRankings.model_validate_json(
                    (run_dir / "outputs/ranking.json").read_text(encoding="utf-8")
                )
            except (OSError, ValidationError) as error:
                checks = [False, False, False]
                evidence = [type(error).__name__] * 3
            else:
                ids = [row.action_id for row in ranked.rankings]
                action_check = len(ids) == len(expected) and set(ids) == set(expected)
                tip_check = action_check and all(
                    len(row.tip_ids) == len(expected[row.action_id])
                    and set(row.tip_ids) == set(expected[row.action_id])
                    for row in ranked.rankings
                )
                checks = [
                    ranked.input_fingerprint == given["input_fingerprint"],
                    action_check,
                    tip_check,
                ]
                evidence = [
                    "Public schema accepted; fingerprint "
                    + ("matches." if checks[0] else "does not match."),
                    f"Returned {len(ids)} action IDs; expected {len(expected)} unique bound IDs.",
                    "All own tip IDs are preserved exactly once."
                    if tip_check
                    else "An action's own ID permutation is missing or invalid.",
                ]
            expectations = [
                {"text": text, "passed": passed, "evidence": detail}
                for text, passed, detail in zip(case["expectations"], checks, evidence, strict=True)
            ]
            passed = sum(checks)
            result = {
                "pass_rate": passed / len(checks),
                "passed": passed,
                "failed": len(checks) - passed,
                "total": len(checks),
                "time_seconds": None,
                "tokens": None,
            }
            grading = {
                "expectations": expectations,
                "summary": {
                    key: value
                    for key, value in result.items()
                    if key not in {"time_seconds", "tokens"}
                },
            }
            (run_dir / "grading.json").write_text(
                json.dumps(grading, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            runs.append(
                {
                    "eval_id": case["id"],
                    "eval_name": folder,
                    "configuration": configuration,
                    "run_number": 1,
                    "result": result,
                    "expectations": expectations,
                }
            )
    summary = {}
    for configuration in ["with_skill", "without_skill"]:
        rates = [
            run["result"]["pass_rate"] for run in runs if run["configuration"] == configuration
        ]
        summary[configuration] = {
            "pass_rate": {
                "mean": mean(rates),
                "stddev": pstdev(rates),
                "min": min(rates),
                "max": max(rates),
            }
        }
    delta = (
        summary["with_skill"]["pass_rate"]["mean"] - summary["without_skill"]["pass_rate"]["mean"]
    )
    summary["delta"] = {"pass_rate": f"{delta:+.2f}"}
    notes = [
        "Two frozen synthetic cases, one fresh agent output per case/configuration.",
        "Tests contract adherence only, not source auditing, semantic ranking quality, "
        "real Provider output or speech.",
        "Timing and token metrics were not available; omitted statistics do not mean zero cost.",
        "The displayed standard deviation is across these cases, not repeated-run stability.",
    ]
    if all(run["result"]["pass_rate"] == 1 for run in runs):
        notes.append(
            "Both configurations passed; these assertions do not demonstrate "
            "a benefit from the Skill."
        )
    benchmark = {
        "metadata": {
            "skill_name": config["skill_name"],
            "skill_path": str(skill),
            "timestamp": datetime.now(UTC).isoformat(),
            "evals_run": [1, 2],
            "runs_per_configuration": 1,
        },
        "runs": runs,
        "run_summary": summary,
        "notes": notes,
    }
    (args.workspace / "benchmark.json").write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"runs": len(runs), "summary": summary, "notes": notes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
