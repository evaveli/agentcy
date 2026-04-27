# src/agentcy/llm_utilities/provider_validator.py
"""
LLM Provider Configuration Validator.

Validates LLM provider configuration at startup and provides
utilities for checking provider availability.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LLMProviderStatus(str, Enum):
    """Status of an LLM provider configuration."""
    CONFIGURED = "configured"
    STUB = "stub"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""
    name: str
    env_var: str
    provider_value: Optional[str]
    status: LLMProviderStatus
    error: Optional[str] = None
    requires_api_key: bool = False
    api_key_present: bool = False


@dataclass
class LLMConfigValidationResult:
    """Result of validating all LLM provider configurations."""
    valid: bool
    providers: Dict[str, ProviderConfig]
    warnings: List[str]
    errors: List[str]
    mock_mode: bool
    stub_mode_enabled: bool


# Provider environment variable mappings
LLM_PROVIDERS = {
    "strategist": "LLM_STRATEGIST_PROVIDER",
    "supervisor": "LLM_SUPERVISOR_PROVIDER",
    "ethics": "LLM_ETHICS_PROVIDER",
    "plan_validator": "LLM_PLAN_VALIDATOR_PROVIDER",
    "input_validator": "LLM_INPUT_VALIDATOR_PROVIDER",
    "strategist_loop": "LLM_STRATEGIST_LOOP_PROVIDER",
}

# Providers that can operate in stub mode (without LLM)
STUB_CAPABLE_PROVIDERS = {"strategist", "plan_validator", "input_validator", "strategist_loop"}

# Providers that require LLM by default (but can be stubbed with LLM_STUB_MODE=1)
LLM_REQUIRED_PROVIDERS = {"supervisor", "ethics"}


def _normalize_provider(value: Optional[str]) -> Optional[str]:
    """Normalize provider value to canonical form."""
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered in ("openai", "gpt"):
        return "openai"
    if lowered in ("llama", "ollama"):
        return "llama"
    return None


def _check_api_key(provider: Optional[str]) -> bool:
    """Check if required API key is present for the provider."""
    if provider == "openai":
        return bool(os.getenv("OPEN_AI_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip())
    # Ollama doesn't require API key
    return True


def validate_llm_config() -> LLMConfigValidationResult:
    """
    Validate all LLM provider configurations at startup.

    Returns a validation result with details about each provider's status,
    any warnings or errors, and whether stub mode is enabled.
    """
    mock_mode = os.getenv("LLM_MOCK_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    stub_mode = os.getenv("LLM_STUB_MODE", "").strip().lower() in ("1", "true", "yes", "on")

    providers: Dict[str, ProviderConfig] = {}
    warnings: List[str] = []
    errors: List[str] = []

    for name, env_var in LLM_PROVIDERS.items():
        raw_value = os.getenv(env_var, "").strip()
        provider_value = _normalize_provider(raw_value)

        # Determine status
        if mock_mode:
            # Mock mode: all providers use mock responses
            status = LLMProviderStatus.CONFIGURED
            config = ProviderConfig(
                name=name,
                env_var=env_var,
                provider_value=provider_value or "mock",
                status=status,
                requires_api_key=False,
                api_key_present=True,
            )
        elif provider_value:
            # Provider is configured
            api_key_present = _check_api_key(provider_value)
            requires_api_key = provider_value == "openai"

            if requires_api_key and not api_key_present:
                status = LLMProviderStatus.ERROR
                error_msg = f"{env_var}={provider_value} but neither OPEN_AI_KEY nor OPENAI_API_KEY is set"
                errors.append(error_msg)
                config = ProviderConfig(
                    name=name,
                    env_var=env_var,
                    provider_value=provider_value,
                    status=status,
                    error=error_msg,
                    requires_api_key=requires_api_key,
                    api_key_present=api_key_present,
                )
            else:
                status = LLMProviderStatus.CONFIGURED
                config = ProviderConfig(
                    name=name,
                    env_var=env_var,
                    provider_value=provider_value,
                    status=status,
                    requires_api_key=requires_api_key,
                    api_key_present=api_key_present,
                )
        elif name in STUB_CAPABLE_PROVIDERS:
            # Provider not configured but can use stub
            status = LLMProviderStatus.STUB
            config = ProviderConfig(
                name=name,
                env_var=env_var,
                provider_value=None,
                status=status,
            )
            warnings.append(f"{env_var} not set, using stub mode")
        elif name in LLM_REQUIRED_PROVIDERS:
            # Provider not configured and requires LLM
            if stub_mode:
                status = LLMProviderStatus.STUB
                config = ProviderConfig(
                    name=name,
                    env_var=env_var,
                    provider_value=None,
                    status=status,
                )
                warnings.append(f"{env_var} not set, using stub mode (LLM_STUB_MODE=1)")
            else:
                status = LLMProviderStatus.DISABLED
                config = ProviderConfig(
                    name=name,
                    env_var=env_var,
                    provider_value=None,
                    status=status,
                )
                warnings.append(f"{env_var} not set, service will fail if invoked")
        else:
            status = LLMProviderStatus.DISABLED
            config = ProviderConfig(
                name=name,
                env_var=env_var,
                provider_value=None,
                status=status,
            )

        providers[name] = config

    # Check for fallback patterns
    if not providers["strategist_loop"].provider_value and providers["strategist"].provider_value:
        warnings.append("LLM_STRATEGIST_LOOP_PROVIDER not set, will fall back to LLM_STRATEGIST_PROVIDER")

    # Overall validation is valid if no errors
    valid = len(errors) == 0

    return LLMConfigValidationResult(
        valid=valid,
        providers=providers,
        warnings=warnings,
        errors=errors,
        mock_mode=mock_mode,
        stub_mode_enabled=stub_mode,
    )


def log_validation_result(result: LLMConfigValidationResult) -> None:
    """Log the validation result at appropriate levels."""
    if result.mock_mode:
        logger.info("LLM_MOCK_MODE enabled - all LLM services will return mock responses")

    if result.stub_mode_enabled:
        logger.info("LLM_STUB_MODE enabled - services without providers will use stub responses")

    for warning in result.warnings:
        logger.warning("LLM config: %s", warning)

    for error in result.errors:
        logger.error("LLM config: %s", error)

    # Log summary
    configured = sum(1 for p in result.providers.values() if p.status == LLMProviderStatus.CONFIGURED)
    stub = sum(1 for p in result.providers.values() if p.status == LLMProviderStatus.STUB)
    disabled = sum(1 for p in result.providers.values() if p.status == LLMProviderStatus.DISABLED)

    logger.info(
        "LLM providers: %d configured, %d stub, %d disabled",
        configured, stub, disabled
    )


def is_stub_mode() -> bool:
    """Check if global stub mode is enabled."""
    return os.getenv("LLM_STUB_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def is_mock_mode() -> bool:
    """Check if global mock mode is enabled."""
    return os.getenv("LLM_MOCK_MODE", "").strip().lower() in ("1", "true", "yes", "on")
