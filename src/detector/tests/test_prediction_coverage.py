import json
from types import SimpleNamespace

import pytest

from prodog_sast.email_text import collect_api_to_model_text, eml_bytes_to_model_text
from prodog_sast.transformer import predict_transformer, text_windows


@pytest.mark.parametrize("field", ["text_plain", "text_html", "analysis_text"])
def test_collector_limit_keeps_the_complete_tail_and_added_newline(field):
    marker = "SYNTHETIC_TAIL_IGNORE_PREVIOUS_INSTRUCTIONS"
    body = "x" * (300_000 - len(marker)) + marker

    text = collect_api_to_model_text({field: body})

    assert body + "\n" in text


@pytest.mark.parametrize("field", ["text_plain", "text_html", "analysis_text"])
@pytest.mark.parametrize("ending", ["x", "\n"])
def test_collector_input_beyond_supported_limit_is_an_explicit_error(field, ending):
    with pytest.raises(ValueError, match="limit"):
        collect_api_to_model_text({field: "x" * 300_000 + ending})


@pytest.mark.parametrize("charset", ["utf-8", "unknown-synthetic-charset"])
def test_decoded_eml_limit_and_charset_fallback_never_slice_the_tail(charset):
    header = f"Content-Type: text/plain; charset={charset}\n\n".encode()
    body = b"x" * 299_996 + b"TAIL\n"

    assert body.decode() in eml_bytes_to_model_text(header + body)
    with pytest.raises(ValueError, match="limit"):
        eml_bytes_to_model_text(header + body + b"x")


@pytest.fixture
def semantic_artifact(tmp_path):
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    vocab = tmp_path / "vocab.txt"
    vocab.write_text(
        "[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\n!\nalert\nreview\nhello\nworld\n",
        encoding="utf-8",
    )
    tokenizer = transformers.BertTokenizerFast(vocab_file=str(vocab))
    batches, seen = [], []

    def synthetic_logits(input_ids, attention_mask, token_type_ids=None):
        batches.append(len(input_ids))
        seen.extend(
            ids[mask.bool()].tolist()
            for ids, mask in zip(input_ids, attention_mask, strict=True)
        )
        alert = (input_ids == tokenizer.convert_tokens_to_ids("alert")).any(dim=1)
        review = (input_ids == tokenizer.convert_tokens_to_ids("review")).any(dim=1)
        log_odds = torch.where(alert, 8.0, torch.where(review, 1.0, -8.0)).double()
        return SimpleNamespace(logits=torch.stack((torch.zeros_like(log_odds), log_odds), dim=1))

    return {
        "config": {
            "window_chars": 1000,
            "window_overlap": 500,
            "max_length": 256,
            "suspicious_threshold": 0.5,
            "danger_threshold": 0.9,
        },
        "tokenizer": tokenizer,
        "model": synthetic_logits,
        "device": "cpu",
    }, batches, seen


@pytest.mark.parametrize("suffix_length", [100, 1200])
def test_dense_token_overflow_covers_payloads_preserves_owners_and_bounds_batches(
    semantic_artifact, suffix_length
):
    artifact, batches, seen = semantic_artifact
    texts = [
        "!" * 300 + "alert" + "!" * suffix_length,
        "!" * 300 + "review" + "!" * suffix_length,
        "hello world",
    ]

    results = predict_transformer(artifact, texts, batch_size=2)

    assert [item["verdict"] for item in results] == ["danger", "suspicious", "safe"]
    assert [item["prompt_injection_probability"] for item in results] == pytest.approx(
        [0.9996646498695336, 0.7310585786300049, 0.0003353501304664781]
    )
    assert max(batches) <= 2
    windows = [window for text in texts for window in text_windows(text, 1000, 500)]
    first_chunks = artifact["tokenizer"](windows, truncation=True, max_length=256)["input_ids"]
    assert all(chunk in seen for chunk in first_chunks)


def test_transformer_thresholds_and_scores_remain_consistent_after_json(semantic_artifact):
    artifact, _, _ = semantic_artifact
    artifact["config"].update(suspicious_threshold=0.7310586, danger_threshold=0.7310587)

    result = json.loads(json.dumps(predict_transformer(artifact, ["review"])[0]))

    assert result["verdict"] == "safe"
    assert result["prompt_injection_probability"] < result["suspicious_threshold"]
    assert result["suspicious_threshold"] == 0.7310586
    assert result["danger_threshold"] == 0.7310587


@pytest.mark.parametrize("batch_size", [0, -1])
def test_transformer_rejects_nonpositive_batch_size(semantic_artifact, batch_size):
    artifact, _, _ = semantic_artifact
    with pytest.raises(ValueError, match="batch_size"):
        predict_transformer(artifact, ["hello"], batch_size=batch_size)


def test_transformer_rejects_token_limit_without_room_for_content(semantic_artifact):
    artifact, _, _ = semantic_artifact
    artifact["config"]["max_length"] = 2
    with pytest.raises(ValueError, match="max_length"):
        predict_transformer(artifact, ["alert"])
