import asyncio
import json

import pytest

from prodog_gmail.experiment_model import ollama_context_fits
from prodog_gmail.experiments import ExperimentInput, run_agent
from prodog_gmail.sandbox import ExperimentError


class CapturedShell:
    def __init__(self, text):
        self.text = text
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        return {"stdout": self.text, "stderr": "", "exit_code": 0, "duration_ms": 1}

    async def snapshot(self):
        return {"files": [], "complete": True}


def test_total_output_limit_archives_crossing_result_and_final_file_snapshot(monkeypatch):
    async def check():
        monkeypatch.setattr("prodog_gmail.experiments.RUN_OUTPUT_BYTES", 5)
        sandbox, events = CapturedShell("abc"), []

        async def reply(messages):
            return call("run_shell", command="synthetic command")

        with pytest.raises(ExperimentError, match="run_output_limit"):
            await run_agent(
                ExperimentInput(provider="ollama", model="test", task="summary", mail="synthetic"),
                sandbox,
                reply,
                lambda kind, data: events.append((kind, data)),
            )
        results = [data for kind, data in events if kind == "tool_result"]
        assert len(results) == 2 and len(sandbox.commands) == 2
        assert results[-1]["capture_truncated"] is True
        assert sum(len(data["stdout"].encode()) for data in results) <= 5
        assert events[-1][0] == "files"

    asyncio.run(check())


def call(name, **arguments):
    return {
        "message": {
            "role": "assistant",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


def test_large_output_is_archived_but_model_can_page_to_hidden_middle():
    async def check():
        raw = "A" * 18000 + "MIDDLE_TEST_MARKER" + "B" * 12000
        sandbox, events = CapturedShell(raw), []
        seen = []

        async def reply(messages):
            seen.append(list(messages))
            assert ollama_context_fits(messages)
            if len(seen) == 1:
                return call("run_shell", command="cat /workspace/mail.txt")
            output = json.loads(messages[-1]["content"])
            if len(seen) == 2:
                assert output["preview_truncated"] is True
                assert len(output["stdout"].encode()) <= 3072
                return call(
                    "read_output",
                    output_id=output["output_id"],
                    stream="stdout",
                    offset=18000,
                )
            assert output["text"].startswith("MIDDLE_TEST_MARKER")
            assert output["offset"] == 18000
            return {"message": {"role": "assistant", "content": "done"}}

        result = await run_agent(
            ExperimentInput(provider="ollama", model="test", task="summary", mail="synthetic"),
            sandbox,
            reply,
            lambda kind, data: events.append((kind, data)),
        )
        assert result == "completed"
        assert sandbox.commands == ["cat /workspace/mail.txt"]
        shell = next(data for kind, data in events if kind == "tool_result")
        assert shell["stdout"] == raw

    asyncio.run(check())


def test_utf8_paging_and_explicit_sliding_context_preserve_trace():
    async def check():
        raw = "회의🙂" * 6000
        sandbox, events = CapturedShell(raw), []
        position, output_id, pages = 0, None, []

        async def reply(messages):
            nonlocal position, output_id
            assert ollama_context_fits(messages)
            assert messages[0]["role"] == "system" and messages[1]["content"] == "summary"
            if not output_id:
                if messages[-1]["role"] == "user":
                    return call("run_shell", command="cat /workspace/mail.txt")
                data = json.loads(messages[-1]["content"])
                output_id = data["output_id"]
                text = data["stdout"]
                position = data["stdout_next_offset"]
            else:
                data = json.loads(messages[-1]["content"])
                text = data["text"]
                assert data["offset"] == position
                position = data["next_offset"]
            assert "�" not in text
            pages.append(text)
            if position < len(raw):
                return call("read_output", output_id=output_id, stream="stdout", offset=position)
            return {"message": {"role": "assistant", "content": "done"}}

        result = await run_agent(
            ExperimentInput(
                provider="ollama", model="test", task="summary", mail="synthetic", max_steps=24
            ),
            sandbox,
            reply,
            lambda kind, data: events.append((kind, data)),
        )
        assert result == "completed"
        assert "".join(pages) == raw
        assert any(kind == "context_window" for kind, _ in events)
        assert len(sandbox.commands) == 1

    asyncio.run(check())


def test_four_parallel_outputs_with_json_escapes_fit_one_context_round():
    async def check():
        raw = '\x00\x1f\\"회의🙂' * 2000
        sandbox, events = CapturedShell(raw), []
        count = 0

        async def reply(messages):
            nonlocal count
            count += 1
            assert ollama_context_fits(messages)
            if count == 1:
                return {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "run_shell",
                                    "arguments": {"command": "cat file"},
                                }
                            }
                            for _ in range(4)
                        ],
                    }
                }
            tools = [
                json.loads(message["content"]) for message in messages if message["role"] == "tool"
            ]
            assert len(tools) == 4
            for data in tools:
                assert data["stdout_next_offset"] > 0
                assert raw.startswith(data["stdout"])
                assert data["preview_truncated"]
            return {"message": {"role": "assistant", "content": "done"}}

        assert (
            await run_agent(
                ExperimentInput(provider="ollama", model="test", task="summary", mail="synthetic"),
                sandbox,
                reply,
                lambda kind, data: events.append((kind, data)),
            )
            == "completed"
        )
        inputs = [data for kind, data in events if kind == "model_input"]
        assert len(inputs) == 2 and inputs[-1]["messages"][-1]["role"] == "tool"
        assert len([data for kind, data in events if kind == "tool_result"]) == 4

    asyncio.run(check())


@pytest.mark.parametrize(
    "args",
    [
        {"output_id": "../host-file"},
        {"output_id": "output_1", "offset": -1},
        {"output_id": "output_1", "offset": True},
        {"output_id": "output_1", "limit": 1000000},
        {"output_id": "output_1", "stream": "host"},
    ],
)
def test_invalid_page_requests_cannot_read_host_files_or_execute_shell(args):
    async def check():
        sandbox = CapturedShell("test")

        async def reply(messages):
            return call("read_output", **args)

        with pytest.raises(ExperimentError, match="invalid_tool"):
            await run_agent(
                ExperimentInput(provider="ollama", model="test", task="summary", mail="synthetic"),
                sandbox,
                reply,
                lambda *_: None,
            )
        assert sandbox.commands == []

    asyncio.run(check())
