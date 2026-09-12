"""Research execution shared by the UI and external agents.

Only validated method specifications enter the compute process. Model calls and
stage outputs are checkpointed; the final holdout is hidden during refinement.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid

from core.lab_data import cached_records, demo_records, import_csv, metadata
from core.lab_models import Connection, ExperimentParams, MarketDatasetRequest, RunRequest
from core.lab_store import LabStore, LostLease, encode


def safe_message(value):
    """Keep task errors useful without exposing credentials or machine paths."""
    text = str(value)
    text = re.sub(r"(?i)(?:bearer\s+|(?:api[_-]?key|token|secret)\s*[=:]\s*)[^\s,;]+", "[redacted]", text)
    text = re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+", "[redacted]", text)
    text = re.sub(r"(?:[A-Za-z]:\\|/)(?:[^\s:;,\"']+[\\/])+[^\s:;,\"']*", "[local path]", text)
    return text[:1200]


class LabService:
    def __init__(self, directory=None, *, provider_factory=None, compute=None, demo=False):
        base = Path(directory or os.environ.get("TRACKER_LAB_DIR") or Path(__file__).resolve().parents[1] / "data" / "lab")
        # Demo must never reuse a private database, including when a storage
        # override is inherited from the user's normal launch environment.
        self.store = LabStore(base / "demo" if demo else base)
        self.provider_factory = provider_factory
        self.compute_override = compute
        self.demo = demo
        self.owner = uuid.uuid4().hex
        self.worker_task = None
        self.active_task = None
        self.last_heartbeat = 0.
        self.last_error = None
        self.active_run = None
        self._wake = asyncio.Event()

    async def start(self):
        from core.lab_extensions import load_extensions
        load_extensions()
        if self.worker_task and not self.worker_task.done():
            return
        if not any(d["synthetic"] for d in self.store.datasets()):
            records = demo_records()
            self.store.add_dataset(metadata(records, "合成股票 · 方法学习", "synthetic", True), records)
        if not self.demo and not self.store.connections():
            for kind, binary, label in (("codex_cli", "codex", "Codex 官方客户端"), ("claude_cli", "claude", "Claude Code 官方客户端")):
                if shutil.which(binary):
                    self.store.save_connection({"id": kind, "name": label, "provider": kind, "model": "",
                                                "base_url": None, "api_key_env": None, "timeout": 120})
        self.last_heartbeat = time.time()
        self.worker_task = asyncio.create_task(self._worker(), name="research-worker")

    async def stop(self):
        try:
            if self.active_task:
                self.active_task.cancel()
            if self.worker_task:
                self.worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.worker_task
        finally:
            self.store.release(self.owner)

    def health(self):
        alive = bool(self.worker_task and not self.worker_task.done() and time.time() - self.last_heartbeat < 45)
        return {"worker_alive": alive, "active_run": self.active_run, "last_error": self.last_error,
                "storage": "sqlite", "demo": self.demo}

    @staticmethod
    def templates():
        from backtesting.research import templates
        return templates()

    def capabilities(self):
        from providers.llm.research import provider_capabilities
        providers = []
        for adapter in provider_capabilities():
            kind = adapter["kind"]
            executable = {"codex_cli": "codex", "claude_cli": "claude"}.get(kind)
            providers.append({**adapter, "id": kind, "label": adapter["label"],
                  "available": bool(shutil.which(executable)) if executable else True,
                  "reason": "使用官方客户端现有登录" if executable else "使用服务端配置的模型适配器"})
        return {"providers": providers,
                "limits": {"max_experiments": 6, "max_symbols": 10, "max_rows": 25000,
                           "max_upload_bytes": 8 * 1024 * 1024, "max_timeout_seconds": 1800},
                "demo": self.demo, "health": self.health(), "protocol_version": "1",
                "autonomy": "registered_research_tools", "holdout_policy": "validation_only_until_frozen"}

    def import_data(self, content, name="导入的股票行情"):
        records = import_csv(content)
        return self.store.add_dataset(metadata(records, name[:80], "csv"), records)

    async def market_data(self, request):
        req = MarketDatasetRequest.model_validate(request).model_dump(mode="json")
        if req["source"] == "cache":
            database = Path(__file__).resolve().parents[1] / "data" / "backtesting.db"
            records = await asyncio.to_thread(cached_records, req["symbols"], req["start"], req["end"],
                                              "" if req["adjustment"] == "none" else req["adjustment"], database)
        else:
            output = await self._subprocess("core.lab_fetch", req, 120)
            if output.get("error"):
                raise ValueError(output["error"])
            records = output["records"]
        info = metadata(records, req["name"], req["source"])
        info.update({"selection": req, "adjustment": req["adjustment"],
                     "source_note": "本地缓存未必保留原始数据修订记录" if req["source"] == "cache" else "供应商日线；下载时刻可见数据"})
        return self.store.add_dataset(info, records)

    def prepare_request(self, request):
        req = RunRequest.model_validate(request).model_dump(mode="json")
        info, _ = self.store.dataset(req["dataset_id"])
        known = {t["id"] for t in self.templates()}
        if req["template_id"] not in known:
            raise ValueError("Unknown research template")
        req["params"] = self.validate_params(req["template_id"], req["params"])
        if info.get("adjustment") and "adjustment" in req["params"] and req["params"]["adjustment"] != info["adjustment"]:
            raise ValueError("实验的复权声明与所选数据版本不一致")
        if self.demo and (req["mode"] != "manual" or not info["synthetic"] or req["refresh_data"]):
            raise ValueError("演示模式只允许使用合成数据进行手动实验")
        if req["refresh_data"] and not info.get("selection"):
            raise ValueError("此数据集没有可刷新的来源，请导入新的数据版本")
        if req["mode"] == "agent":
            connection = self.store.connection(req["connection_id"])
            # Freeze public connection settings for restart/reproducibility. No secret values.
            req["connection"] = connection
        return req

    def validate_params(self, template_id, values):
        if template_id in {"momentum", "mean_reversion", "volatility"}:
            return ExperimentParams.model_validate(values).model_dump()
        from core.research_agent import validate_experiment_spec
        template = next(t for t in self.templates() if t["id"] == template_id)
        defaults = template.get("defaults", {})
        proposal = validate_experiment_spec({"template_id": template_id, "params": {**defaults, **values}},
                                            {"templates": [template]})
        return proposal["params"]

    def submit(self, request):
        req = self.prepare_request(request)
        key = req.pop("client_request_id", None)
        identifier = self.store.submit(req, key)
        self._wake.set()
        return self.public_run(identifier)

    def public_run(self, identifier):
        run = self.store.get(identifier)
        checkpoint = run.pop("checkpoint", {})
        run["progress"] = {"experiments_completed": len(checkpoint.get("experiments", [])),
                           "model_calls": checkpoint.get("model_calls", 0),
                           "max_model_calls": checkpoint.get("max_model_calls", 0),
                           "plan": checkpoint.get("plan"), "frozen": checkpoint.get("frozen", False)}
        return run

    async def _subprocess(self, module, payload, timeout):
        import json
        base = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="compute-", dir=self.store.directory) as directory:
            source, output = Path(directory) / "input.json", Path(directory) / "output.json"
            source.write_text(encode(payload), encoding="utf-8")
            source.chmod(0o600)
            # No model credentials are needed in deterministic research computation.
            env = {key: value for key, value in os.environ.items()
                   if not re.search(r"(?i)(key|token|secret|password|credential)", key)}
            env.update({"PYTHONPATH": str(base), "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", module, str(source), str(output),
                            cwd=base, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                            start_new_session=True)
            try:
                await asyncio.wait_for(process.wait(), timeout)
                if process.returncode or not output.is_file():
                    raise ValueError("研究计算进程失败，请检查数据和方法参数")
                if output.stat().st_size > 50 * 1024 * 1024:
                    raise ValueError("研究结果超过大小限制")
                return json.loads(output.read_text())
            finally:
                if process.returncode is None:
                    import signal
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()

    async def _compute(self, records, spec, timeout):
        if self.compute_override:
            return await self.compute_override(records, spec)
        output = await self._subprocess("core.lab_compute", {"records": records, "spec": spec}, timeout)
        if output.get("error"):
            raise ValueError(safe_message(output["error"]))
        return output["result"]

    async def _worker(self):
        while True:
            self.last_heartbeat = time.time()
            try:
                if not self.demo:
                    self.store.enqueue_due()
                run = self.store.claim(self.owner)
                if run:
                    self.active_run = run["id"]
                    self.active_task = asyncio.create_task(self._execute(run))
                    while not self.active_task.done():
                        await asyncio.sleep(.25)
                        self.last_heartbeat = time.time()
                        if not self.store.heartbeat(run["id"], self.owner):
                            self.active_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, LostLease):
                        await self.active_task
                    self.active_task = None
                    self.active_run = None
                    self.last_error = None
                    continue
            except asyncio.CancelledError:
                if self.active_task:
                    self.active_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, LostLease):
                        await self.active_task
                raise
            except Exception as exc:
                if self.active_task:
                    self.active_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, LostLease, Exception):
                        await self.active_task
                    self.active_task = None
                    self.active_run = None
                    self.store.release(self.owner)
                self.last_error = safe_message(exc)
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 1)

    async def _execute(self, run):
        identifier, req = run["id"], run["request"]
        cp = run["checkpoint"]
        cp.setdefault("started", time.time())
        cp.setdefault("experiments", [])
        cp.setdefault("model_calls", 0)
        cp.setdefault("max_model_calls", req["max_experiments"] + 2)

        def remaining():
            left = req["timeout_seconds"] - (time.time() - cp["started"])
            if left <= 0:
                raise TimeoutError("研究达到时长上限，可查看已完成阶段或重试")
            return left

        def save(stage, message):
            self.store.checkpoint(identifier, self.owner, stage, cp, message)

        async def model_call(stage, callback):
            if cp["model_calls"] >= cp["max_model_calls"]:
                raise ValueError("研究已达到模型调用上限")
            # Charge before invoking; crash recovery cannot repeat unrecorded calls for free.
            cp["model_calls"] += 1
            save(stage, "模型正在规划研究" if stage == "planning" else "模型正在评价实验依据")
            return await asyncio.wait_for(callback(), remaining())

        try:
            remaining()
            if self.demo:
                demo_data, _ = self.store.dataset(req["dataset_id"])
                if req["mode"] != "manual" or not demo_data.get("synthetic") or req.get("refresh_data"):
                    raise ValueError("演示模式不能执行真实数据或模型任务")
            if not cp.get("dataset_id"):
                if req.get("refresh_data"):
                    info, _ = self.store.dataset(req["dataset_id"])
                    selection = {**info["selection"], "end": __import__("datetime").date.today().isoformat()}
                    updated = await asyncio.wait_for(self.market_data(selection), remaining())
                    cp["dataset_id"] = updated["id"]
                else:
                    cp["dataset_id"] = req["dataset_id"]
                save("data", "数据版本已固定，开始检查研究方案")
            info, records = self.store.dataset(cp["dataset_id"])
            provider = None
            if req["mode"] == "agent":
                from core.research_agent import plan_research, review_research
                if self.provider_factory:
                    provider = self.provider_factory(req["connection"])
                else:
                    from providers.llm.research import make_research_provider
                    provider = make_research_provider(req["connection"])
            target = next(t.get("target") for t in self.templates() if t["id"] == req["template_id"])
            eligible_templates = [t for t in self.templates() if t.get("target") == target]
            if "plan" not in cp:
                spec = {"template_id": req["template_id"], "params": req["params"]}
                if provider is None:
                    cp["plan"] = {"hypothesis": req["objective"], "experiments": [spec]}
                else:
                    cp["plan"] = await model_call("planning", lambda: plan_research(
                        req["objective"], {"dataset": info, "templates": eligible_templates, "initial_spec": spec,
                                           "base_spec": spec, "fixed_parameters": req["params"]},
                        provider, {"max_experiments": req["max_experiments"], "timeout_seconds": min(300, int(remaining()))}))
                save("planned", "研究方案已保存；后续修改只参考验证集")
            proposed = cp["plan"].get("experiments", [])
            if not proposed:
                raise ValueError("模型没有提供可执行的实验方案")
            if "queue" not in cp:
                cp["queue"] = proposed[:req["max_experiments"]]
            while True:
                count = len(cp["experiments"])
                if (provider and count and count == len(cp["queue"]) and count < req["max_experiments"]
                        and count not in cp.get("reviewed_counts", [])):
                    feedback = {**self._validation_feedback(cp["experiments"]), "templates": eligible_templates,
                                "dataset_id": info["id"], "base_spec": {"template_id": req["template_id"], "params": req["params"]},
                                "limits": {"max_experiments": req["max_experiments"], "timeout_seconds": min(300, int(remaining()))}}
                    review = await model_call("refining", lambda: review_research(req["objective"], feedback, provider))
                    cp.setdefault("reviews", []).append(review)
                    cp.setdefault("reviewed_counts", []).append(len(cp["experiments"]))
                    revised = review.get("revised_spec")
                    if revised:
                        cp["queue"].append(revised)
                    save("reviewed", "验证集评审已保存")
                if len(cp["experiments"]) >= min(len(cp["queue"]), req["max_experiments"]):
                    break
                index = len(cp["experiments"])
                raw_spec = cp["queue"][index]
                template = raw_spec.get("template_id", req["template_id"])
                if template not in {t["id"] for t in eligible_templates}:
                    raise ValueError("模型选择了未注册的方法或更换了已固定的预测目标")
                params = raw_spec.get("params", raw_spec.get("parameters", {k: v for k, v in raw_spec.items() if k not in {"template_id", "dataset_id"}}))
                if raw_spec.get("dataset_id", info["id"]) != info["id"]:
                    raise ValueError("自动实验不能更换已选择的数据集")
                for field in ("horizon", "train_ratio", "validation_ratio", "fee_bps", "slippage_bps", "market", "adjustment", "seed"):
                    if field in params and field in req["params"] and params[field] != req["params"][field]:
                        raise ValueError("自动实验不能修改已固定的预测期限、切分或成本假设")
                target_metadata = next(t for t in self.templates() if t["id"] == template)
                allowed = target_metadata.get("parameters", {})
                shared_parameters = {"horizon", "lookback", "train_ratio", "validation_ratio", "fee_bps",
                                     "slippage_bps", "market", "adjustment", "seed"}
                # A new template owns its method-specific defaults. Preserve only
                # compatible shared protocol fields when the agent switches methods.
                inherited = {key: value for key, value in req["params"].items()
                             if key in allowed and (template == req["template_id"] or key in shared_parameters)}
                spec = {"template_id": template, **self.validate_params(template, {**inherited, **params}), "source": info["source"]}
                save("experiment", f"正在运行实验 {index + 1} / {req['max_experiments'] if provider else 1}")
                try:
                    result = await self._compute(records, spec, remaining())
                    entry = {"id": f"experiment_{index + 1}", "spec": spec, "result": result, "status": "completed"}
                except ValueError as exc:
                    entry = {"id": f"experiment_{index + 1}", "spec": spec, "error": safe_message(exc), "status": "failed"}
                cp["experiments"].append(entry)
                save("evaluated", f"实验 {index + 1} 的结果已保存" if entry["status"] == "completed" else f"实验 {index + 1} 失败：{entry['error']}")
            completed = [x for x in cp["experiments"] if x["status"] == "completed"]
            if not completed:
                raise ValueError("实验均未通过，已保存失败原因供修订数据或参数")
            if "selected_id" not in cp:
                # Compare the same target metric only. The agent never sees test values here.
                comparable = [x for x in completed if __import__("math").isfinite(self._validation_score(x))]
                if not comparable:
                    raise ValueError("没有实验提供有效的验证集误差，无法冻结或评价方案")
                selected = min(comparable, key=self._validation_score)
                cp["selected_id"] = selected["id"]
                cp["frozen"] = True
                save("frozen", "方案已冻结；现在查看最终测试集，不再据此调参")
            selected = next(x for x in completed if x["id"] == cp["selected_id"])
            if provider and "final_review" not in cp:
                context = {"phase": "final", "selected_experiment": selected["id"],
                           "experiments": [{"id": selected["id"], "metrics": selected["result"].get("metrics", {}),
                                            "warnings": selected["result"].get("warnings", [])}],
                           "limits": {"timeout_seconds": min(300, int(remaining()))},
                           "instruction": "解释冻结方案的最终评价。不得提出用于本次测试集的新参数。"}
                cp["final_review"] = await model_call("reporting", lambda: review_research(req["objective"], context, provider))
                cp["final_review"].pop("revised_spec", None)
                save("reported", "最终评价已保存")
            output = {**selected["result"], "run_id": identifier, "objective": req["objective"],
                      "dataset": info, "plan": cp["plan"], "selected_experiment": cp["selected_id"],
                      "experiments": [{k: v for k, v in item.items() if k != "result"} | {
                          "result": {"metrics": {"validation": item.get("result", {}).get("metrics", {}).get("validation", {})}}}
                          for item in cp["experiments"]], "agent_review": cp.get("final_review"),
                      "validation_reviews": cp.get("reviews", []),
                      "execution": {"mode": req["mode"], "model_calls": cp["model_calls"],
                                    "holdout_policy": "validation_only_until_frozen", "frozen": True}}
            if info["synthetic"]:
                output.setdefault("warnings", []).insert(0, "这是合成演示数据，仅用于学习方法和检查流程。")
            output.setdefault("warnings", []).append("多次研究同一历史区间会反复使用测试集；再次运行不代表获得全新的独立样本外证据。")
            self.store.finish(identifier, self.owner, result=output)
        except asyncio.CancelledError:
            raise
        except LostLease:
            raise
        except Exception as exc:
            self.store.finish(identifier, self.owner, error=safe_message(exc))

    @staticmethod
    def _validation_feedback(experiments):
        return {"phase": "validation", "experiments": [
            {"id": item["id"], "spec": item["spec"], "status": item["status"], "error": item.get("error"),
             "validation": item.get("result", {}).get("metrics", {}).get("validation", {}),
             "warnings": item.get("result", {}).get("warnings", [])}
            for item in experiments]}

    @staticmethod
    def _validation_score(item):
        metrics = item["result"].get("metrics", {}).get("validation", {})
        value = metrics.get("mae", metrics.get("model_mae", metrics.get("rmse")))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not __import__("math").isfinite(value) or value < 0:
            return float("inf")
        return value
