"""Exercise schema changes across real planning and review calls without model access."""
import asyncio
import json

import pytest

import backtesting.research as research
from core.lab_service import LabService
from core.llm.base import LLMResponse


@pytest.fixture
def plugin_templates(monkeypatch):
    # Keep the process-wide registry isolated from other tests.
    monkeypatch.setattr(research, "_TEMPLATES", dict(research._TEMPLATES))
    monkeypatch.setattr(research, "_RUNNERS", dict(research._RUNNERS))
    for identifier, parameter in (("plugin_source_fixture", "bins"), ("plugin_destination_fixture", "window")):
        research.register_template(identifier, {
            "name": identifier,
            "target": "fixture_plugin_target",
            "defaults": {parameter: 4},
            "parameters": {parameter: {"type": "integer", "minimum": 2, "maximum": 12, "default": 6}},
        }, runner=lambda frames, spec: {})
    return "plugin_source_fixture", "plugin_destination_fixture"


def test_plugin_metadata_defaults_override_parameter_defaults(plugin_templates):
    service = LabService.__new__(LabService)
    for identifier, parameter in zip(plugin_templates, ("bins", "window")):
        template = next(t for t in service.templates() if t["id"] == identifier)
        assert template["defaults"][parameter] == 4
        assert service.validate_params(identifier, {}) == {parameter: 4}
        assert service.validate_params(identifier, {parameter: 8}) == {parameter: 8}


@pytest.mark.parametrize("switch_stage", ["planning", "validation"])
def test_agent_can_switch_between_distinct_plugin_parameter_schemas(tmp_path, plugin_templates, switch_stage):
    source, destination = plugin_templates
    computed = []
    phases = []

    class Model:
        async def chat(self, messages, **kwargs):
            payload = json.loads(messages[-1]["content"])["input"]
            if "context" in payload:
                phases.append("planning")
                identifier = destination if switch_stage == "planning" else source
                parameter = "window" if identifier == destination else "bins"
                output = {
                    "question": "Which registered risk method has lower validation error?",
                    "hypotheses": ["Different registered features may change validation error."],
                    "experiments": [{"template_id": identifier, "params": {parameter: 5}}],
                    "limitations": ["Synthetic fixture evidence only."],
                }
            else:
                results = payload["results"]
                phases.append(results["phase"])
                revised = None
                if results["phase"] == "validation" and switch_stage == "validation":
                    revised = {"template_id": destination, "params": {"window": 7}}
                output = {
                    "conclusion": "The supplied validation evidence supports a bounded comparison.",
                    "evidence": [{"experiment_id": results["experiments"][-1]["id"], "observation": "Use the recorded validation measurement."}],
                    "limitations": ["Synthetic fixture evidence only."],
                    "revised_spec": revised,
                }
            return LLMResponse(content=json.dumps(output), model="offline-fixture", usage={}, finish_reason="stop")

    async def scenario():
        async def compute(records, spec):
            computed.append(spec)
            return {
                "metrics": {"validation": {"mae": 0.2 if spec["template_id"] == destination else 1.0}, "test": {"mae": 2.0}},
                "equity": [], "manifest": {}, "warnings": [], "lessons": [], "results": {},
            }

        service = LabService(tmp_path, provider_factory=lambda _: Model(), compute=compute)
        await service.start()
        try:
            service.store.save_connection({"id": "fixture", "provider": "openai", "model": "offline"})
            run = service.submit({
                "mode": "agent", "objective": "Compare registered risk methods with distinct schemas",
                "dataset_id": service.store.datasets()[0]["id"], "template_id": source,
                "params": {"bins": 5}, "connection_id": "fixture", "max_experiments": 2,
            })
            for _ in range(200):
                final = service.public_run(run["id"])
                if final["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.03)
            assert final["status"] == "completed", final.get("error")
            assert final["request"]["params"] == {"bins": 5}
            destination_spec = next(spec for spec in computed if spec["template_id"] == destination)
            assert "bins" not in destination_spec
            assert destination_spec["window"] == (5 if switch_stage == "planning" else 7)
            assert final["result"]["selected_experiment"] == ("experiment_1" if switch_stage == "planning" else "experiment_2")
            assert phases == ["planning", "validation", "final"]
        finally:
            await service.stop()

    asyncio.run(scenario())
