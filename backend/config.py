from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv

ENV_FILE_NAME = ".env"

LLM_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "zhipu": {
        "model": "glm-5",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
    },
    "bailian": {
        "model": "qwen3.5-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "deepseek": {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
    },
    "openai": {
        "model": "gpt-4.1-mini",
        "base_url": "https://api.openai.com/v1",
    },
}

EMBEDDING_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "zhipu": {
        "model": "embedding-3",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
    },
    "bailian": {
        "model": "text-embedding-v4",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "openai": {
        "model": "text-embedding-3-small",
        "base_url": "https://api.openai.com/v1",
    },
    "local": {
        "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "base_url": "",
    },
}

PROVIDER_ALIASES = {
    "glm": "zhipu",
    "zhipuai": "zhipu",
    "bigmodel": "zhipu",
    "aliyun": "bailian",
    "dashscope": "bailian",
    "qwen": "bailian",
    "openai-compatible": "openai",
    "compatible": "openai",
    "hf": "local",
    "huggingface": "local",
}

EXECUTION_PLATFORMS = {"windows", "linux"}


@dataclass(frozen=True)
class Settings:
    backend_dir: Path
    project_root: Path
    llm_provider: str
    llm_model: str
    llm_api_key: str | None
    llm_base_url: str
    llm_thinking_type: str | None
    embedding_provider: str
    embedding_model: str
    embedding_api_key: str | None
    embedding_base_url: str
    component_char_limit: int = 20_000
    terminal_timeout_seconds: int = 30


def _load_env_file() -> Path:
    backend_dir = Path(__file__).resolve().parent
    env_file_path = _resolve_env_file_path()
    if env_file_path is not None:
        load_dotenv(env_file_path)
    return backend_dir


def _resolve_env_file_path() -> Path | None:
    backend_dir = Path(__file__).resolve().parent
    candidate = backend_dir / ENV_FILE_NAME
    if candidate.exists():
        return candidate
    return None


@lru_cache(maxsize=1)
def _env_file_values() -> dict[str, str]:
    env_file_path = _resolve_env_file_path()
    if env_file_path is None:
        return {}
    values = dotenv_values(env_file_path)
    return {
        key: value.strip()
        for key, value in values.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _first_config_value(*names: str) -> str | None:
    env_file_values = _env_file_values()
    for name in names:
        value = env_file_values.get(name)
        if value:
            return value
    return _first_env(*names)


def _first_api_key(*names: str) -> str | None:
    return _first_config_value(*names)


def _normalize_provider(
    value: str | None,
    *,
    default: str,
    defaults: dict[str, dict[str, str]],
) -> str:
    normalized = (value or default).strip().lower()
    normalized = PROVIDER_ALIASES.get(normalized, normalized)
    if normalized in defaults:
        return normalized
    return default


def _resolve_llm_api_key(provider: str) -> str | None:
    if provider == "zhipu":
        return _first_api_key("LLM_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY")
    if provider == "bailian":
        return _first_api_key("LLM_API_KEY", "BAILIAN_API_KEY", "DASHSCOPE_API_KEY")
    if provider == "deepseek":
        return _first_api_key("LLM_API_KEY", "DEEPSEEK_API_KEY")
    return _first_api_key("LLM_API_KEY", "OPENAI_API_KEY")


def _resolve_llm_model(provider: str) -> str:
    if provider == "zhipu":
        return _first_config_value("LLM_MODEL", "ZHIPU_MODEL") or LLM_PROVIDER_DEFAULTS[provider]["model"]
    if provider == "bailian":
        return _first_config_value("LLM_MODEL", "BAILIAN_MODEL") or LLM_PROVIDER_DEFAULTS[provider]["model"]
    if provider == "deepseek":
        return _first_config_value("LLM_MODEL", "DEEPSEEK_MODEL") or LLM_PROVIDER_DEFAULTS[provider]["model"]
    return _first_config_value("LLM_MODEL") or LLM_PROVIDER_DEFAULTS[provider]["model"]


def _resolve_llm_base_url(provider: str) -> str:
    if provider == "zhipu":
        return _first_config_value("LLM_BASE_URL", "ZHIPU_BASE_URL") or LLM_PROVIDER_DEFAULTS[provider]["base_url"]
    if provider == "bailian":
        return _first_config_value("LLM_BASE_URL", "BAILIAN_BASE_URL") or LLM_PROVIDER_DEFAULTS[provider]["base_url"]
    if provider == "deepseek":
        return _first_config_value("LLM_BASE_URL", "DEEPSEEK_BASE_URL") or LLM_PROVIDER_DEFAULTS[provider]["base_url"]
    return _first_config_value("LLM_BASE_URL", "OPENAI_BASE_URL") or LLM_PROVIDER_DEFAULTS[provider]["base_url"]


def _normalize_thinking_type(value: str | None) -> str | None:
    """Return an optional normalized thinking mode string from env input."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"enabled", "disabled"}:
        return normalized
    return None


def _resolve_llm_thinking_type() -> str | None:
    """Read the optional LLM thinking mode override for providers that support it."""
    return _normalize_thinking_type(_first_config_value("LLM_THINKING_TYPE", "KIMI_THINKING_TYPE"))


def _resolve_embedding_api_key(provider: str) -> str | None:
    if provider == "local":
        return None
    if provider == "zhipu":
        return _first_api_key("EMBEDDING_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY")
    if provider == "bailian":
        return _first_api_key("EMBEDDING_API_KEY", "BAILIAN_API_KEY", "DASHSCOPE_API_KEY")
    return _first_api_key("EMBEDDING_API_KEY", "OPENAI_API_KEY")


def _resolve_embedding_model(provider: str) -> str:
    if provider == "local":
        return (
            _first_config_value("EMBEDDING_MODEL", "LOCAL_EMBEDDING_MODEL")
            or EMBEDDING_PROVIDER_DEFAULTS[provider]["model"]
        )
    if provider == "zhipu":
        return (
            _first_config_value("EMBEDDING_MODEL", "ZHIPU_EMBEDDING_MODEL", "ZHIPU_MODEL")
            or EMBEDDING_PROVIDER_DEFAULTS[provider]["model"]
        )
    if provider == "bailian":
        return (
            _first_config_value("EMBEDDING_MODEL", "BAILIAN_EMBEDDING_MODEL", "BAILIAN_MODEL")
            or EMBEDDING_PROVIDER_DEFAULTS[provider]["model"]
        )
    return _first_config_value("EMBEDDING_MODEL") or EMBEDDING_PROVIDER_DEFAULTS[provider]["model"]


def _resolve_embedding_base_url(provider: str) -> str:
    if provider == "local":
        return _first_config_value("EMBEDDING_BASE_URL", "LOCAL_EMBEDDING_BASE_URL") or ""
    if provider == "zhipu":
        return (
            _first_config_value("EMBEDDING_BASE_URL", "ZHIPU_EMBEDDING_BASE_URL", "ZHIPU_BASE_URL")
            or EMBEDDING_PROVIDER_DEFAULTS[provider]["base_url"]
        )
    if provider == "bailian":
        return (
            _first_config_value("EMBEDDING_BASE_URL", "BAILIAN_EMBEDDING_BASE_URL", "BAILIAN_BASE_URL")
            or EMBEDDING_PROVIDER_DEFAULTS[provider]["base_url"]
        )
    return (
        _first_config_value("EMBEDDING_BASE_URL", "OPENAI_BASE_URL")
        or EMBEDDING_PROVIDER_DEFAULTS[provider]["base_url"]
    )


def _detect_host_execution_platform() -> str:
    """Return one canonical execution-platform label for the current host OS."""

    return "windows" if os.name == "nt" else "linux"


def _normalize_execution_platform(value: Any, *, default: str) -> str:
    """Return one canonical execution-platform label from raw config input."""

    normalized = str(value or "").strip().lower()
    if normalized in {"win", "windows"}:
        return "windows"
    if normalized in {"linux", "bash"}:
        return "linux"
    return default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    backend_dir = _load_env_file()
    project_root = backend_dir.parent

    llm_provider = _normalize_provider(
        _first_config_value("LLM_PROVIDER"),
        default="zhipu",
        defaults=LLM_PROVIDER_DEFAULTS,
    )
    embedding_provider = _normalize_provider(
        _first_config_value("EMBEDDING_PROVIDER"),
        default="local",
        defaults=EMBEDDING_PROVIDER_DEFAULTS,
    )

    return Settings(
        backend_dir=backend_dir,
        project_root=project_root,
        llm_provider=llm_provider,
        llm_model=_resolve_llm_model(llm_provider),
        llm_api_key=_resolve_llm_api_key(llm_provider),
        llm_base_url=_resolve_llm_base_url(llm_provider),
        llm_thinking_type=_resolve_llm_thinking_type(),
        embedding_provider=embedding_provider,
        embedding_model=_resolve_embedding_model(embedding_provider),
        embedding_api_key=_resolve_embedding_api_key(embedding_provider),
        embedding_base_url=_resolve_embedding_base_url(embedding_provider),
    )


class RuntimeConfigManager:
    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._lock = threading.Lock()
        self._default_config = {
            "rag_mode": False,
            "execution_platform": _detect_host_execution_platform(),
            "skill_retrieval_enabled": True,
        }

    def _merge_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Return one normalized runtime-config object from raw persisted config data."""

        current = payload or {}
        return {
            "rag_mode": bool(current.get("rag_mode", self._default_config["rag_mode"])),
            "execution_platform": _normalize_execution_platform(
                current.get("execution_platform"),
                default=str(self._default_config["execution_platform"]),
            ),
            "skill_retrieval_enabled": bool(
                current.get("skill_retrieval_enabled", self._default_config["skill_retrieval_enabled"])
            ),
        }

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self._config_path.exists():
                self.save(self._default_config)
            try:
                raw_payload = json.loads(self._config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.save(self._default_config)
                return dict(self._default_config)

            if not isinstance(raw_payload, dict):
                self.save(self._default_config)
                return dict(self._default_config)

            merged = self._merge_payload(raw_payload)
            if merged != raw_payload:
                self.save(merged)
            return merged

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        merged = self._merge_payload(payload)
        self._config_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return merged

    def get_rag_mode(self) -> bool:
        return bool(self.load().get("rag_mode", False))

    def set_rag_mode(self, enabled: bool) -> dict[str, Any]:
        return self.save({"rag_mode": enabled})

    def get_execution_platform(self) -> str:
        return str(self.load().get("execution_platform", self._default_config["execution_platform"]))

    def set_execution_platform(self, platform_name: str) -> dict[str, Any]:
        return self.save({"execution_platform": platform_name})

    def get_skill_retrieval_enabled(self) -> bool:
        return bool(
            self.load().get(
                "skill_retrieval_enabled",
                self._default_config["skill_retrieval_enabled"],
            )
        )

    def set_skill_retrieval_enabled(self, enabled: bool) -> dict[str, Any]:
        return self.save({"skill_retrieval_enabled": enabled})


runtime_config = RuntimeConfigManager(get_settings().backend_dir / "config.json")
