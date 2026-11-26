"""Regenerate docs/collect-api-sample.json from the synthetic contract sample."""

import json
from pathlib import Path

from prodog_gmail.demo import contract_sample
from prodog_gmail.mail import mail_metadata, parse_gmail_message

PATH = Path(__file__).resolve().parents[1] / "docs" / "collect-api-sample.json"


def build() -> dict:
    source = contract_sample()
    parsed = parse_gmail_message(source)
    parsed.pop("pdf_texts", None)  # Match the public mail detail contract.
    return {"input": source, "output": parsed, "stored": mail_metadata(parsed)}


if __name__ == "__main__":
    PATH.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(PATH)
