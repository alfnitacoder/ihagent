"""Device label and token reporting."""

from types import SimpleNamespace

from hfagent.agent import Agent, AgentUI
from hfagent.config import Config
from hfagent.runtime import device_from_vram
from hfagent.tools import default_registry
from tests.test_agent import Chunk, Delta, FakeClient


def test_vram_split_labels_gpu_and_cpu():
    assert device_from_vram(1000, 1000) == "GPU"
    assert device_from_vram(1000, 0) == "CPU"
    assert device_from_vram(1000, 400) == "GPU+CPU"


def test_step_records_token_usage(tmp_path):
    class UsageChunk(Chunk):
        def __init__(self, delta, prompt, completion):
            super().__init__(delta)
            self.usage = SimpleNamespace(
                prompt_tokens=prompt,
                completion_tokens=completion,
            )

    seen = {}

    class UI(AgentUI):
        def on_runtime(self, device, prompt_tokens, completion_tokens):
            seen["prompt"] = prompt_tokens
            seen["completion"] = completion_tokens
            seen["device"] = device

    agent = Agent(
        Config(api_key="fake", base_url="http://127.0.0.1:9/v1", model="qwen3:8b"),
        default_registry(),
        UI(),
    )
    agent.client = FakeClient([[UsageChunk(Delta(content="ok"), 120, 4)]])
    assert agent.run("hi") == "ok"
    assert seen["prompt"] == 120
    assert seen["completion"] == 4
