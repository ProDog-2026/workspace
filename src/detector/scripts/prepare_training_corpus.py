from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TextIO

from prodog_sast.data import load_samples
from prodog_sast.email_text import eml_bytes_to_model_text, fields_to_model_text

SPLITS = ("train", "validation", "test")
POSITIONS = ("start", "middle", "end")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint(value: str) -> bytes:
    """A short digest: the dedup set holds one entry per row of a 400k-row corpus."""
    return hashlib.sha256(value.encode("utf-8")).digest()[:16]


def _partition(values: Iterable[str], *, seed: int, salt: str) -> dict[str, str]:
    unique = sorted(
        set(values),
        key=lambda value: hashlib.sha256(f"{seed}:{salt}:{value}".encode()).digest(),
    )
    if len(unique) < 3:
        raise ValueError(f"{salt} requires at least 3 distinct groups")
    train_count = max(1, min(len(unique) - 2, len(unique) * 2 // 3))
    validation_count = (len(unique) - train_count) // 2
    return {
        **{value: "train" for value in unique[:train_count]},
        **{
            value: "validation"
            for value in unique[train_count : train_count + validation_count]
        },
        **{value: "test" for value in unique[train_count + validation_count :]},
    }


class Writer:
    def __init__(self, handle: TextIO) -> None:
        self.handle = handle
        self.seen: set[tuple[str, int, bytes]] = set()
        self.counts: Counter[str] = Counter()
        self.duplicates: Counter[str] = Counter()

    def add(
        self,
        *,
        source: str,
        label: int,
        group: str,
        model_text: str,
        split: str | None = None,
        **metadata: Any,
    ) -> None:
        key = (source, label, _fingerprint(model_text))
        if key in self.seen:
            self.duplicates[source] += 1
            return
        self.seen.add(key)
        record = {
            "source_dataset": source,
            "group_id": group,
            "model_text": model_text,
            "prompt_injection": label,
            **metadata,
        }
        if split:
            record["split"] = split
        self.handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.counts[f"{source}:{label}"] += 1


def add_llmail(writer: Writer, paths: list[Path], benign_path: Path | None) -> None:
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                subject = str(record.get("subject", ""))
                body = str(record.get("body", ""))
                if not subject and not body:
                    raise ValueError(f"{path}:{line_number}: subject or body is required")
                text = fields_to_model_text(subject=subject, body_text=body)
                team = str(record.get("team_id") or _digest(text))
                try:
                    objectives = json.loads(record.get("objectives") or "{}")
                except (TypeError, json.JSONDecodeError):
                    objectives = {}
                # Every raw submission is an attempted injection, so the label is 1 even
                # when the attempt failed. exfil_sent keeps the success flag auditable.
                writer.add(
                    source="llmail-inject",
                    label=1,
                    group=f"llmail-team-{team}",
                    model_text=text,
                    scenario=str(record.get("scenario", "")),
                    exfil_sent=bool(objectives.get("exfil.sent")),
                )
    if benign_path:
        values = json.loads(benign_path.read_text(encoding="utf-8"))
        if not isinstance(values, list):
            raise ValueError(f"{benign_path}: JSON root must be a list")
        for value in values:
            text = fields_to_model_text(body_text=str(value))
            writer.add(
                source="llmail-inject",
                label=0,
                group=f"llmail-benign-{_digest(text)}",
                model_text=text,
            )


def add_paired(writer: Writer, path: Path) -> None:
    samples, _ = load_samples(path)
    for sample in samples:
        writer.add(
            source="paired-email",
            label=sample.label,
            group=f"paired-{sample.group}",
            model_text=sample.text,
        )


def _load_bipia_contexts(root: Path) -> list[str]:
    contexts: list[str] = []
    for name in ("train.jsonl", "test.jsonl"):
        with (root / "benchmark" / "email" / name).open("r", encoding="utf-8") as handle:
            for line in handle:
                contexts.append(str(json.loads(line)["context"]))
    return list(dict.fromkeys(contexts))


def _load_bipia_attacks(root: Path) -> list[tuple[str, str]]:
    attacks: list[tuple[str, str]] = []
    for name in ("text_attack_train.json", "text_attack_test.json"):
        values = json.loads((root / "benchmark" / name).read_text(encoding="utf-8"))
        for category, prompts in values.items():
            attacks.extend((str(category), str(prompt)) for prompt in prompts)
    return list(dict.fromkeys(attacks))


BIPIA_MARKER = re.compile(r"\|?\s*(SUBJECT|EMAIL_FROM|RECEIVED DATE|CONTENT):\s*")


def _parse_bipia_context(context: str) -> tuple[str, str, str]:
    """Split BIPIA's pre-serialised email back into subject, sender and body.

    BIPIA stores each mail as "SUBJECT: ...|EMAIL_FROM: ...|CONTENT: ...". Those
    pipe-delimited markers appear in no other source, so feeding the whole string in
    as a body hands the model a fingerprint for "this mail came from BIPIA" - and
    because the attacked rows carry the same markers, it learned to read the carrier
    instead of the injected phrase and flagged BIPIA's own benign mail.
    """
    matches = list(BIPIA_MARKER.finditer(context))
    if not matches:
        return "", "", context
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(context)
        fields[match.group(1)] = context[match.end() : end].strip()
    return (
        fields.get("SUBJECT", ""),
        fields.get("EMAIL_FROM", ""),
        fields.get("CONTENT", ""),
    )


def _insert_attack(context: str, attack: str, position: str) -> str:
    if position == "start":
        return f"{attack} {context}"
    if position == "end":
        return f"{context} {attack}"
    midpoint = len(context) // 2
    insertion = context.find(" ", midpoint)
    if insertion == -1:
        insertion = midpoint
    return f"{context[:insertion]} {attack} {context[insertion:]}"


def add_bipia(writer: Writer, root: Path, seed: int) -> None:
    contexts = _load_bipia_contexts(root)
    attacks = _load_bipia_attacks(root)
    context_split = _partition(
        (_digest(value) for value in contexts), seed=seed, salt="bipia-context"
    )
    attack_split = _partition(
        (_digest(value) for _, value in attacks), seed=seed, salt="bipia-attack"
    )
    for split in SPLITS:
        split_contexts = [
            value for value in contexts if context_split[_digest(value)] == split
        ]
        split_attacks = [
            (category, value)
            for category, value in attacks
            if attack_split[_digest(value)] == split
        ]
        # Half the emails stay clean, the other half carry the attacks. Reusing one
        # carrier on both sides is what broke the earlier models: each carrier showed
        # up ~6x as an attack against one benign copy, so the model memorised those
        # specific emails as attacks and then scored its own benign copies as attacks
        # (49 of 52 training mails). No weighting fixes that - balancing each source
        # internally does, but benign-only sources hold 68% of the benign mass, so
        # per-source balance and global class balance cannot both hold, and the
        # balanced variant dropped the benign pass rate from 98% to 37%. Keeping the
        # two halves disjoint removes the conflict instead of trading one for the
        # other, and the same-carrier contrast is already covered by Paired Email.
        clean_count = max(1, len(split_contexts) // 2)
        clean_contexts = split_contexts[:clean_count]
        carrier_contexts = split_contexts[clean_count:] or split_contexts
        for context in clean_contexts:
            subject, sender, body = _parse_bipia_context(context)
            writer.add(
                source="bipia-emailqa",
                label=0,
                group=f"bipia-context-{_digest(context)}",
                model_text=fields_to_model_text(
                    subject=subject, sender=sender, body_text=body
                ),
                split=split,
            )
        # Every attack phrase is used at all three positions, with the carrier email
        # rotating. The full context x attack product would instead emit ~300 attacked
        # copies of each of the 78 benign emails, and no weighting survives a 300:1
        # ratio: the benign copies lose, and the model learns that the carrier email
        # itself is the attack.
        for attack_index, (category, attack) in enumerate(split_attacks):
            for position_index, position in enumerate(POSITIONS):
                slot = attack_index * len(POSITIONS) + position_index
                context = carrier_contexts[slot % len(carrier_contexts)]
                subject, sender, body = _parse_bipia_context(context)
                writer.add(
                    source="bipia-emailqa",
                    label=1,
                    group=f"bipia-{_digest(context)}-{_digest(attack)}",
                    model_text=fields_to_model_text(
                        subject=subject,
                        sender=sender,
                        body_text=_insert_attack(body, attack, position),
                    ),
                    split=split,
                    attack_category=category,
                    attack_position=position,
                )


MIN_CAMPAIGN_SUBJECT = 16


def _campaign_key(subject: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", subject.lower()).strip()


def add_phishing(writer: Writer, directory: Path) -> None:
    """The 11 Zenodo CSVs form one source: TREC_05/06/07 overlap heavily, and 11
    separate sources would also let phishing dominate the per-source weighting."""
    csv.field_size_limit(10**9)
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as handle:
            for record in csv.DictReader(handle):
                subject = str(record.get("subject") or "")
                body = str(record.get("body") or "")
                text = fields_to_model_text(subject=subject, body_text=body)
                # Spam arrives in campaigns, so same-subject mail shares a group and
                # cannot straddle train and test.
                campaign = _campaign_key(subject)
                group = (
                    f"phishing-campaign-{_digest(campaign)}"
                    if len(campaign) >= MIN_CAMPAIGN_SUBJECT
                    else f"phishing-row-{_digest(text)}"
                )
                writer.add(
                    source="phishing-curated",
                    label=0,
                    group=group,
                    model_text=text,
                    corpus=path.stem,
                    phishing_label=record.get("label"),
                )


def add_eml(writer: Writer, directory: Path) -> None:
    for path in sorted(directory.rglob("*.eml")):
        text = eml_bytes_to_model_text(path.read_bytes())
        writer.add(
            source="cw-l-email-corpus",
            label=0,
            group=f"cw-l-{_digest(text)}",
            model_text=text,
            phishing_label=1,
        )


def _shuffle_lines(path: Path, seed: int) -> None:
    """Interleave the sources on disk.

    The corpus is written one source at a time, and minibatch SGD would otherwise
    see all of LLMail before any benign mail. Shuffling by line offset keeps peak
    memory at one offset per row instead of the whole corpus.
    """
    offsets: list[tuple[int, int]] = []
    with path.open("rb") as handle:
        offset = 0
        for line in handle:
            offsets.append((offset, len(line)))
            offset += len(line)
    random.Random(seed).shuffle(offsets)
    temporary = path.with_name(path.name + ".shuffling")
    with path.open("rb") as source, temporary.open("wb") as target:
        for start, length in offsets:
            source.seek(start)
            target.write(source.read(length))
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the source-aware prompt-injection training corpus"
    )
    parser.add_argument("--llmail-raw", action="append", default=[], type=Path)
    parser.add_argument("--llmail-benign", type=Path)
    parser.add_argument("--paired", required=True, type=Path)
    parser.add_argument("--bipia-root", required=True, type=Path)
    parser.add_argument("--phishing-dir", required=True, type=Path)
    parser.add_argument("--eml-root", required=True, type=Path)
    parser.add_argument("--output", default="data/processed/training-corpus.jsonl", type=Path)
    parser.add_argument(
        "--manifest", default="reports/training-corpus-manifest.json", type=Path
    )
    parser.add_argument("--seed", default=42, type=int)
    args = parser.parse_args()
    if not args.llmail_raw:
        parser.error("at least one --llmail-raw file is required")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        writer = Writer(handle)
        add_llmail(writer, args.llmail_raw, args.llmail_benign)
        add_paired(writer, args.paired)
        add_bipia(writer, args.bipia_root, args.seed)
        add_phishing(writer, args.phishing_dir)
        add_eml(writer, args.eml_root)
    _shuffle_lines(args.output, args.seed)

    manifest = {
        "output": str(args.output),
        "seed": args.seed,
        "split_policy": "per source/group: 2/3 train, 1/6 validation, 1/6 test",
        "injecagent": "excluded from training; reserved for external evaluation",
        "row_order": f"shuffled on disk with seed {args.seed}",
        "samples": dict(sorted(writer.counts.items())),
        "exact_duplicates_removed": dict(sorted(writer.duplicates.items())),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
