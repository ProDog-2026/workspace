from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .data import load_samples
from .email_text import eml_bytes_to_model_text, fields_to_model_text
from .model import load_artifact, predict, save_artifact, train


def _train(args: argparse.Namespace) -> int:
    samples = []
    used_row_groups = False
    for path in args.data:
        loaded, used_fallback = load_samples(path)
        samples.extend(loaded)
        used_row_groups = used_row_groups or used_fallback
    artifact, report = train(
        samples,
        seed=args.seed,
        max_validation_fpr=args.max_validation_fpr,
        malicious_threshold=args.malicious_threshold,
    )
    report["row_id_group_fallback"] = used_row_groups
    report["training_sources"] = args.data
    artifact["report"] = report
    save_artifact(artifact, args.output)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if used_row_groups:
        print(
            "warning: some samples lacked group_id/scenario_id; leakage safety is weaker",
            file=sys.stderr,
        )
    return 0


def _scan(args: argparse.Namespace) -> int:
    artifact = load_artifact(args.model)
    texts: list[str] = []
    names: list[str] = []
    if args.text is not None:
        names.append("<text>")
        texts.append(fields_to_model_text(body_text=args.text))
    for raw_path in args.paths:
        path = Path(raw_path)
        names.append(str(path))
        raw = path.read_bytes()
        if path.suffix.lower() == ".eml":
            texts.append(eml_bytes_to_model_text(raw))
        else:
            texts.append(fields_to_model_text(body_text=raw.decode("utf-8", "replace")))
    if not texts:
        raise ValueError("provide at least one path or --text")
    results = predict(artifact, texts)
    rendered = [
        {"source": name, **result}
        for name, result in zip(names, results, strict=True)
    ]
    print(json.dumps(rendered, ensure_ascii=False, indent=2))
    if args.fail_on_detect and any(
        item["verdict"] in {"suspicious", "malicious"} for item in rendered
    ):
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prodog-sast",
        description="ML-only prompt-injection detector for email",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train", help="train and evaluate a model")
    train_parser.add_argument(
        "data", nargs="+", help="one or more .csv, .json, or .jsonl datasets"
    )
    train_parser.add_argument("--output", default="models/prompt_injection.joblib")
    train_parser.add_argument("--report", default="reports/metrics.json")
    train_parser.add_argument("--seed", type=int, default=42)
    train_parser.add_argument("--max-validation-fpr", type=float, default=0.02)
    train_parser.add_argument("--malicious-threshold", type=float, default=0.5)
    train_parser.set_defaults(handler=_train)

    scan_parser = subparsers.add_parser("scan", help="scan .eml or UTF-8 text files")
    scan_parser.add_argument("paths", nargs="*")
    scan_parser.add_argument("--text")
    scan_parser.add_argument("--model", default="models/prompt_injection.joblib")
    scan_parser.add_argument("--fail-on-detect", action="store_true")
    scan_parser.set_defaults(handler=_scan)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError) as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
