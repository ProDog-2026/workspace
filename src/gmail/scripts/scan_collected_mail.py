"""Private JSON-stdin worker. Run only with the detector's Python and trusted model.

Usage: <detector-python> scan_collected_mail.py <trusted-model.joblib>
No Gmail environment imports or model training happen here.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

MAX_INPUT_BYTES = 2 * 1024 * 1024


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError
        mail = json.loads(raw.decode("utf-8"))
        if not isinstance(mail, dict):
            raise ValueError
        fields = ("subject", "sender", "text_plain", "text_html", "analysis_text")
        if any(mail.get(key) is not None and not isinstance(mail[key], str) for key in fields):
            raise ValueError
        # Resolve imports from the trusted checkout, never mail input or the working directory.
        detector_src = Path(__file__).resolve().parents[2] / "detector" / "src"
        sys.path.insert(0, str(detector_src))
        # Dependency warnings and exceptions must not echo model paths or mail content.
        # Offline artifacts only; these settings never come from mail/model output.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                from prodog_sast.email_text import collect_api_to_model_text
                from prodog_sast.model import load_artifact, predict

                model_path = Path(sys.argv[1])
                artifact = load_artifact(model_path)
                texts = [collect_api_to_model_text(mail)]
                predictions = predict(artifact, texts)
                if len(predictions) != 1:
                    raise ValueError
                prediction = predictions[0]
                result = {
                    "score": prediction["prompt_injection_probability"],
                    "verdict": prediction["verdict"],
                    "suspicious_threshold": prediction["suspicious_threshold"],
                    "danger_threshold": prediction["danger_threshold"],
                }
                transformer_path = model_path.parent / "quadrat-transformer"
                if (
                    model_path.resolve()
                    == (detector_src.parent / "models/prompt_injection.joblib").resolve()
                    and not transformer_path.is_dir()
                ):
                    raise ValueError("team Transformer is required")
                if transformer_path.exists():
                    from prodog_sast.transformer import (
                        combine_predictions,
                        load_transformer,
                        predict_transformer,
                    )

                    semantic = predict_transformer(load_transformer(transformer_path), texts)
                    combined = combine_predictions(predictions, semantic)[0]
                    detectors = {
                        name: {
                            "score": item["prompt_injection_probability"],
                            "verdict": item["verdict"],
                            "suspicious_threshold": item["suspicious_threshold"],
                            "danger_threshold": item["danger_threshold"],
                        }
                        for name, item in combined["detectors"].items()
                    }
                    # A representative deciding score, never a cross-model maximum probability.
                    result = {
                        **detectors[combined["decided_by"][0]],
                        "mode": "ensemble",
                        "decided_by": combined["decided_by"],
                        "detectors": detectors,
                    }
        output = json.dumps(result, ensure_ascii=True, allow_nan=False)
        exit_code = 0
    except Exception:
        output = json.dumps(
            {
                "error": {
                    "code": "static_inference_failed",
                    "message": "정적 ML 분석에 실패했습니다. 탐지기 환경과 모델을 확인하세요.",
                }
            }
        )
        exit_code = 1
    sys.stdout.write(output + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
