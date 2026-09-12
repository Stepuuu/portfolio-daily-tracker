"""Public contracts for human and agent initiated research."""
from typing import Literal, Any
from datetime import date
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ExperimentParams(StrictModel):
    horizon: int = Field(5, ge=1, le=30)
    lookback: int = Field(20, ge=5, le=120)
    train_ratio: float = Field(.6, ge=.4, le=.8)
    validation_ratio: float = Field(.2, ge=.1, le=.3)
    fee_bps: float = Field(10, ge=0, le=200)
    slippage_bps: float = Field(5, ge=0, le=200)
    seed: int = Field(42, ge=0, le=2147483647)
    market: Literal["a_share", "hk", "us"] = "us"
    adjustment: Literal["qfq", "hfq", "none"] = "qfq"

    @model_validator(mode="after")
    def enough_test(self):
        if self.train_ratio + self.validation_ratio > .9:
            raise ValueError("至少保留 10% 的时间用于最终测试")
        return self


class RunRequest(StrictModel):
    mode: Literal["manual", "agent"] = "manual"
    objective: str = Field("研究股票特征是否具有样本外预测能力", min_length=4, max_length=2000)
    dataset_id: str = Field(pattern=r"^ds_[a-f0-9]{24}$")
    template_id: str = Field("momentum", pattern=r"^[a-z][a-z0-9_]{0,63}$")
    params: dict[str, Any] = Field(default_factory=dict)
    connection_id: str | None = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    max_experiments: int = Field(3, ge=1, le=6)
    timeout_seconds: int = Field(600, ge=30, le=1800)
    client_request_id: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    refresh_data: bool = False

    @model_validator(mode="after")
    def agent_connection(self):
        if self.mode == "agent" and not self.connection_id:
            raise ValueError("自动研究需要选择模型连接")
        return self


class Connection(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    model: str = Field("", max_length=160, pattern=r"^[A-Za-z0-9_./:+-]*$")
    base_url: str | None = Field(None, max_length=500)
    api_key_env: str | None = Field(None, pattern=r"^[A-Z][A-Z0-9_]{0,79}$")
    timeout: int = Field(120, ge=10, le=300)

    @field_validator("base_url")
    @classmethod
    def clean_url(cls, value):
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API 地址必须是 HTTP(S) 地址，不能包含凭据、查询或片段")
        return value.rstrip("/")

    @model_validator(mode="after")
    def required_model(self):
        if self.provider in {"openai", "anthropic"} and not self.model:
            raise ValueError("API 连接需要填写模型名称")
        if self.provider.endswith("_cli") and (self.base_url or self.api_key_env):
            raise ValueError("官方 CLI 连接使用客户端已有登录；第三方 API 请创建 API 连接")
        return self


class MarketDatasetRequest(StrictModel):
    name: str = Field("股票日线研究数据", min_length=1, max_length=80)
    symbols: list[str] = Field(min_length=1, max_length=10)
    start: date
    end: date
    adjustment: Literal["qfq", "hfq", "none"] = "qfq"
    source: Literal["cache", "market"] = "cache"

    @field_validator("symbols")
    @classmethod
    def symbols_valid(cls, values):
        from core.lab_data import SYMBOL
        if len(set(values)) != len(values) or any(not SYMBOL.fullmatch(x) for x in values):
            raise ValueError("股票代码无效或重复")
        return values

    @model_validator(mode="after")
    def range_valid(self):
        if self.start >= self.end or (self.end - self.start).days > 7305:
            raise ValueError("请选择开始日期早于结束日期且不超过 20 年的数据区间")
        return self


class ScheduleRequest(StrictModel):
    id: str | None = Field(None, pattern=r"^sch_[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=80)
    request: RunRequest
    interval_seconds: int = Field(86400, ge=900, le=31536000)
    max_runs: int = Field(10, ge=1, le=365)
    enabled: bool = False
