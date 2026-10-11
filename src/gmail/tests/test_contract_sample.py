import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_contract_sample.py"
spec = importlib.util.spec_from_file_location("export_contract_sample", SCRIPT)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_sample_file_matches_parser():
    # Fails when the parser output changes without rerunning scripts/export_contract_sample.py.
    assert json.loads(exporter.PATH.read_text(encoding="utf-8")) == exporter.build()


def test_sample_fills_contract_fields():
    sample = exporter.build()
    output, stored = sample["output"], sample["stored"]
    for key in ("id", "subject", "sender", "text_plain", "text_html", "analysis_text", "body_hash"):
        assert output[key], key
    assert output["attachments"] and "attachment_content_not_analyzed" in output["warnings"]
    assert "truncated" in output
    assert not {"raw", "text_plain", "text_html", "analysis_text"} & stored.keys()
