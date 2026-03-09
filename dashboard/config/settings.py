"""
配置管理
"""
from typing import Optional
from pathlib import Path
import json
import os


class Config:
    """全局配置"""

    def __init__(self, config_file: str = "config.json"):
        self.config_file = Path(config_file)
        self._data = {}
        self._load()

    def _load(self):
        """加载配置"""
        if self.config_file.exists():
            with open(self.config_file, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            # 使用默认配置
            self._data = self._default_config()
            self._save()

    def _save(self):
        """保存配置"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _default_config(self) -> dict:
        """默认配置"""
        return {
            "current_api_group": "openai_official",  # 当前使用的 API 组
            "api_groups": {
                "openai_compatible": {
                    "name": "OpenAI-Compatible API",
                    "description": "Any OpenAI-compatible endpoint (e.g. Ollama, LiteLLM, vLLM)",
                    "base_url": "http://localhost:11434/v1",
                    "api_key": "",
                    "headers": {
                        "Authorization": "Bearer ",
                        "Content-Type": "application/json"
                    },
                    "models": [
                        {
                            "id": "llama3",
                            "name": "Llama 3",
                            "description": "Local model via Ollama / vLLM",
                            "supports_vision": False
                        }
                    ]
                },
                "third_party": {
                    "name": "Third-Party API",
                    "description": "Third-party aggregation API (set your own base_url)",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "",
                    "headers": {
                        "Authorization": "",
                        "Content-Type": "application/json"
                    },
                    "models": [
                        {
                            "id": "claude-sonnet-4-5-20250929",
                            "name": "Claude Sonnet 4.5",
                            "description": "Anthropic 最新 Claude 4.5 模型（2025年9月）",
                            "supports_vision": True
                        },
                        {
                            "id": "claude-opus-4-5-20251101",
                            "name": "Claude Opus 4.5",
                            "description": "Claude 最强推理模型（2025年11月）",
                            "supports_vision": True
                        },
                        {
                            "id": "claude-3-7-sonnet-20250219",
                            "name": "Claude 3.7 Sonnet",
                            "description": "Claude 3.7 代模型（2025年2月）",
                            "supports_vision": True
                        },
                        {
                            "id": "claude-sonnet-4-20250514",
                            "name": "Claude Sonnet 4",
                            "description": "Claude 4 代模型（2025年5月）",
                            "supports_vision": True
                        },
                        {
                            "id": "gpt-4o",
                            "name": "GPT-4o",
                            "description": "OpenAI 多模态旗舰模型，支持图片识别",
                            "supports_vision": True
                        },
                        {
                            "id": "gpt-5",
                            "name": "GPT-5",
                            "description": "OpenAI 最新 GPT-5 模型（2025年8月）",
                            "supports_vision": True
                        },
                        {
                            "id": "gpt-4o-mini",
                            "name": "GPT-4o Mini",
                            "description": "轻量级 GPT-4o，速度快成本低",
                            "supports_vision": True
                        },
                        {
                            "id": "gpt-4-turbo",
                            "name": "GPT-4 Turbo",
                            "description": "GPT-4 增强版，上下文更长",
                            "supports_vision": True
                        },
                        {
                            "id": "o1",
                            "name": "OpenAI o1",
                            "description": "OpenAI 推理模型，擅长复杂问题",
                            "supports_vision": False
                        },
                        {
                            "id": "o1-mini",
                            "name": "OpenAI o1-mini",
                            "description": "轻量级推理模型",
                            "supports_vision": False
                        },
                        {
                            "id": "gemini-2.5-pro",
                            "name": "Gemini 2.5 Pro",
                            "description": "Google 最新 Gemini 2.5 Pro 模型",
                            "supports_vision": True
                        },
                        {
                            "id": "deepseek-chat",
                            "name": "DeepSeek Chat",
                            "description": "DeepSeek 对话模型，性价比高",
                            "supports_vision": False
                        }
                    ]
                },
                "anthropic_official": {
                    "name": "Anthropic 官方",
                    "description": "Claude 官方 API",
                    "base_url": "https://api.anthropic.com/v1",
                    "api_key": "",
                    "headers": None,
                    "provider_type": "claude",
                    "models": [
                        {
                            "id": "claude-sonnet-4-20250514",
                            "name": "Claude Sonnet 4",
                            "description": "Claude 官方模型",
                            "supports_vision": True
                        }
                    ]
                },
                "openai_official": {
                    "name": "OpenAI 官方",
                    "description": "GPT 官方 API",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "",
                    "headers": None,
                    "provider_type": "openai",
                    "models": [
                        {
                            "id": "gpt-4o",
                            "name": "GPT-4o",
                            "description": "OpenAI 官方模型",
                            "supports_vision": True
                        }
                    ]
                }
            },
            "llm": {
                "current_model": "gpt-4o",
                "max_tokens": 4096,
                "temperature": 0.0
            },
            "market_data": {
                "provider": "akshare"  # akshare
            },
            "portfolio": {
                "provider": "manual",  # manual
                "data_file": "data/portfolio.json",
                "auto_refresh": True,
                "refresh_interval": 60  # 秒
            },
            "monitor": {
                "enabled": True,
                "check_interval": 5,  # 秒
                "default_conditions": {
                    "change_pct_above": 5,
                    "change_pct_below": -5
                }
            },
            "storage": {
                "conversations_dir": "data/conversations",
                "alerts_file": "data/alerts.json"
            }
        }

    def get(self, key: str, default=None):
        """获取配置值（支持点号分隔的路径）"""
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def reload_from_disk(self):
        """从磁盘重新加载配置（用于热重载）"""
        self._load()

    def set(self, key: str, value):
        """设置配置值"""
        keys = key.split(".")
        data = self._data
        for k in keys[:-1]:
            if k not in data:
                data[k] = {}
            data = data[k]
        data[keys[-1]] = value
        self._save()

    @property
    def llm_config(self) -> dict:
        """LLM 配置"""
        return self._data.get("llm", {})

    @property
    def market_data_config(self) -> dict:
        """市场数据配置"""
        return self._data.get("market_data", {})

    @property
    def portfolio_config(self) -> dict:
        """持仓配置"""
        return self._data.get("portfolio", {})

    @property
    def monitor_config(self) -> dict:
        """监控配置"""
        return self._data.get("monitor", {})

    @property
    def storage_config(self) -> dict:
        """存储配置"""
        return self._data.get("storage", {})

    def get_current_api_group(self) -> dict:
        """获取当前使用的 API 组"""
        group_name = self._data.get("current_api_group", "xhub")
        return self._data.get("api_groups", {}).get(group_name, {})

    def get_all_api_groups(self) -> dict:
        """获取所有 API 组"""
        return self._data.get("api_groups", {})

    def switch_api_group(self, group_name: str) -> bool:
        """切换 API 组"""
        if group_name in self._data.get("api_groups", {}):
            self._data["current_api_group"] = group_name
            self._save()
            return True
        return False

    def get_current_model(self) -> str:
        """获取当前模型"""
        return self._data.get("llm", {}).get("current_model", "gpt-4o")

    def switch_model(self, model_id: str) -> bool:
        """切换模型"""
        # 检查模型是否在当前 API 组中
        current_group = self.get_current_api_group()
        models = current_group.get("models", [])

        for model in models:
            if model["id"] == model_id:
                if "llm" not in self._data:
                    self._data["llm"] = {}
                self._data["llm"]["current_model"] = model_id
                self._save()
                return True
        return False

    def get_available_models(self) -> list:
        """获取当前 API 组可用的模型列表"""
        current_group = self.get_current_api_group()
        return current_group.get("models", [])


# 全局配置实例
_config: Optional[Config] = None


def get_config(config_file: str = "config.json") -> Config:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = Config(config_file)
    return _config
