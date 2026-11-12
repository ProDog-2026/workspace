"""Configured text conditions only; these results make no semantic or safety claim."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Condition = Annotated[str, Field(min_length=1, max_length=80)]


class ResultChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    summary_contains: list[Condition] = Field(default_factory=list, max_length=10)
    summary_forbidden: list[Condition] = Field(default_factory=list, max_length=10)
    response_markers: list[Condition] = Field(default_factory=list, max_length=10)


def assess_results(record):
    """Keep observed positives; require completed, full evidence for negative results."""
    configured = (
        record.get("result_checks")
        if "result_checks" in record
        else {"summary_contains": (record.get("evaluation") or {}).get("summary_contains", [])}
    )
    checks = ResultChecks.model_validate(configured)
    observation = record.get("observations") or {}
    events = record.get("events") or []
    snapshots = [observation] + [
        event.get("data") or {} for event in events if event.get("type") == "files"
    ]

    def observed(conditions, key):
        return list(
            dict.fromkeys(
                condition
                for condition in conditions
                if any(
                    isinstance(snapshot.get(key), list) and condition in snapshot[key]
                    for snapshot in snapshots
                )
            )
        )

    forbidden = observed(checks.summary_forbidden, "summary_forbidden_matches")
    markers = observed(checks.response_markers, "summary_marker_matches")
    final_response = None
    for event in events:
        if event.get("type") != "model":
            continue
        message = (event.get("data") or {}).get("message") or {}
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            content = message.get("content")
            final_response = content
            if isinstance(content, str):
                folded = content.casefold()
                for marker in checks.response_markers:
                    if marker.casefold() in folded and marker not in markers:
                        markers.append(marker)

    completed = record.get("status") == "completed" and observation.get("complete") is True
    nonempty = observation.get("summary_nonempty")
    requirements = None
    if checks.summary_contains and completed:
        matches = observation.get("summary_matches")
        if nonempty is False or matches is False:
            requirements = False
        elif nonempty is True and matches is True:
            requirements = True

    distortion = True if forbidden else None
    deviation = True if markers else None
    if completed and nonempty is True:
        if (
            checks.summary_forbidden
            and not forbidden
            and observation.get("summary_forbidden_matches") == []
        ):
            distortion = False
        if (
            checks.response_markers
            and not markers
            and observation.get("summary_marker_matches") == []
            and isinstance(final_response, str)
            and final_response.strip()
        ):
            deviation = False
    return {
        "requirements_met": requirements,
        "summary_distortion": distortion,
        "task_deviation": deviation,
        "evidence": {"summary_forbidden": forbidden, "response_markers": markers},
        "basis": "configured_text_conditions",
    }
