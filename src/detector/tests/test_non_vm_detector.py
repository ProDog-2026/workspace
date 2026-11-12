"""Offline regressions for audit fixes 4, 11 and 12; no model artifacts."""

import json

import numpy as np
import pytest

from prodog_sast import cli
from prodog_sast.data import load_samples
from prodog_sast.email_text import collect_api_to_model_text
from prodog_sast.model import _plan, predict


class NumericPipeline:
    def predict_proba(self, texts):
        return np.array([[1 - float(text), float(text)] for text in texts])


@pytest.mark.parametrize("score", ["nan", "inf", "-inf", "-0.000001", "1.000001"])
def test_predict_rejects_invalid_probability_anywhere_in_batch(score):
    artifact = {"pipeline": NumericPipeline(), "threshold": 0.8}
    with pytest.raises(ValueError, match="probabilit"):
        predict(artifact, ["0.01", score, "0.9"])


def test_predict_keeps_closed_interval_and_threshold_verdicts():
    results = predict(
        {"pipeline": NumericPipeline(), "threshold": 0.8, "suspicious_threshold": 0.05},
        ["0", "0.05", "0.8", "1"],
    )
    assert [item["verdict"] for item in results] == ["safe", "suspicious", "danger", "danger"]
    assert [item["prompt_injection_probability"] for item in results] == [0, 0.05, 0.8, 1]


def test_cli_rejects_nan_instead_of_printing_safe(monkeypatch, tmp_path, capsys):
    class InvalidPipeline:
        def predict_proba(self, texts):
            return np.array([[float("nan"), float("nan")]] * len(texts))

    monkeypatch.setattr(
        cli, "load_artifact", lambda path: {"pipeline": InvalidPipeline(), "threshold": 0.8}
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main([
            "scan", "--text", "synthetic", "--model", str(tmp_path / "unused.joblib"),
            "--transformer-model", str(tmp_path / "absent"), "--fail-on-detect",
        ])
    output = capsys.readouterr()
    assert stopped.value.code == 2
    assert output.out == ""
    assert "probabilities" in output.err


@pytest.mark.parametrize("plain", [None, ""])
def test_html_only_does_not_fall_back_to_analysis_text(plain):
    html = '<!-- retained --><div hidden>hidden instruction</div><p>Hello</p>'
    text = collect_api_to_model_text({
        "text_plain": plain, "text_html": html, "analysis_text": "UNUSED FALLBACK",
    })
    assert "UNUSED FALLBACK" not in text
    assert "[HTML_RAW] " + html in text
    assert "[HTML_TEXT] hidden instruction Hello" in text
    assert "[PLAIN] <!-- retained -->" not in text


def test_analysis_fallback_is_used_only_without_plain_or_html():
    assert "[PLAIN] fallback" in collect_api_to_model_text({"analysis_text": "fallback"})
    assert "fallback" not in collect_api_to_model_text({
        "text_plain": "primary", "analysis_text": "fallback",
    })


@pytest.mark.parametrize("field", ["text_plain", "text_html", "analysis_text"])
def test_body_limit_keeps_tail_and_rejects_overflow(field):
    body = "x" * 299_996 + "TAIL"
    text = collect_api_to_model_text({field: body})
    assert body + "\n" in text
    with pytest.raises(ValueError, match="limit"):
        collect_api_to_model_text({field: body + "x"})


@pytest.mark.parametrize("source_field", ["source_dataset", "dataset", "source"])
@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
def test_paired_variants_retain_source_and_declared_split(tmp_path, source_field, suffix):
    rows = [
        {
            "id": f"group-{index}", source_field: "synthetic-corpus", "split": split,
            "pair": {
                "attack": {"external_content": "attack", "user_prompt": "PRIVATE CONTEXT"},
                "benign": {"external_content": "hello", "user_prompt": "PRIVATE CONTEXT"},
            },
        }
        for index, split in enumerate(("train", "validation", "test"))
    ]
    path = tmp_path / ("paired" + suffix)
    path.write_text(
        json.dumps(rows) if suffix == ".json" else "\n".join(map(json.dumps, rows)),
        encoding="utf-8",
    )
    samples, fallback = load_samples(path)
    assert [(s.group, s.label, s.source, s.split) for s in samples] == [
        (f"group-{index}", label, "synthetic-corpus", split)
        for index, split in enumerate(("train", "validation", "test"))
        for label in (1, 0)
    ]
    assert not fallback
    assert all("PRIVATE CONTEXT" not in sample.text for sample in samples)
    plan = _plan(lambda: samples, seed=7)
    assert plan.split_of == {
        ("synthetic-corpus", "group-0"): "train",
        ("synthetic-corpus", "group-1"): "validation",
        ("synthetic-corpus", "group-2"): "test",
    }


def test_paired_source_precedence_and_invalid_split_validation(tmp_path):
    row = {
        "id": "pair", "source_dataset": "primary", "dataset": "secondary", "source": "last",
        "split": "test", "pair": {
            "attack": {"external_content": "attack"},
            "benign": {"external_content": "hello"},
        },
    }
    path = tmp_path / "paired.json"
    path.write_text(json.dumps([row]))
    samples, _ = load_samples(path)
    assert [(s.source, s.split) for s in samples] == [("primary", "test"), ("primary", "test")]
    row["split"] = "invalid"
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match="unsupported split"):
        load_samples(path)
