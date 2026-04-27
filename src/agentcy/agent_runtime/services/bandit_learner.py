"""LinUCB contextual bandit for outcome-driven CNP allocation learning.

Feature-gated behind ``CNP_BANDIT_ENABLE=1``.  When disabled every public
function is a cheap no-op so callers do not need guard clauses.
"""
from __future__ import annotations

import logging
import math
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np

from agentcy.pydantic_models.multi_agent_pipeline import (
    BidFeatures,
    ExecutionOutcomeBandit,
    LinUCBModelState,
)

logger = logging.getLogger(__name__)

FEATURE_DIM = 6
FEATURE_NAMES = ["trust", "cost_norm", "load_norm", "failure_penalty", "hist_success", "speed"]


# ── helpers ──────────────────────────────────────────────────────────────


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bandit_enabled() -> bool:
    return os.getenv("CNP_BANDIT_ENABLE", "0") == "1"


def features_to_array(features: BidFeatures) -> np.ndarray:
    """Convert *BidFeatures* to a numpy vector in canonical order."""
    return np.array(
        [
            features.trust,
            features.cost_norm,
            features.load_norm,
            features.failure_penalty,
            features.hist_success,
            features.speed,
        ],
        dtype=np.float64,
    )


def compute_reward(outcome: ExecutionOutcomeBandit) -> float:
    """Composite reward signal from an execution outcome.

    +1 for success, -1 for failure, with additive penalties for retries,
    latency, cost, and policy blocks.  Clamped to [-2, 1.5].
    """
    r = 1.0 if outcome.success else -1.0

    # Retry penalty (first attempt is free)
    r -= 0.5 * max(0, outcome.retries - 1)

    # Latency penalty (capped at 0.3)
    if outcome.latency_seconds is not None and outcome.latency_seconds > 0:
        r -= min(outcome.latency_seconds / 60.0, 0.3)

    # Cost penalty (capped at 0.2)
    if outcome.cost_actual is not None and outcome.cost_actual > 0:
        r -= min(outcome.cost_actual / 10.0, 0.2)

    # Policy-block penalty
    r -= 0.1 * outcome.policy_blocks

    return max(min(r, 1.5), -2.0)


# ── LinUCB context ───────────────────────────────────────────────────────


class LinUCBContext:
    """One LinUCB context for a single task_type."""

    def __init__(self, d: int = FEATURE_DIM) -> None:
        self.d = d
        self.A: np.ndarray = np.eye(d, dtype=np.float64)
        self.b: np.ndarray = np.zeros(d, dtype=np.float64)
        self.n_updates: int = 0

    # ── serialisation ────────────────────────────────────────────────

    @classmethod
    def from_state(cls, state: LinUCBModelState) -> LinUCBContext:
        ctx = cls(d=state.d)
        if state.A_flat and len(state.A_flat) == state.d * state.d:
            ctx.A = np.array(state.A_flat, dtype=np.float64).reshape(state.d, state.d)
        if state.b_flat and len(state.b_flat) == state.d:
            ctx.b = np.array(state.b_flat, dtype=np.float64)
        ctx.n_updates = state.n_updates
        return ctx

    def to_state(self, task_type: str) -> LinUCBModelState:
        now = datetime.now(timezone.utc)
        return LinUCBModelState(
            task_type=task_type,
            d=self.d,
            A_flat=self.A.flatten().tolist(),
            b_flat=self.b.tolist(),
            n_updates=self.n_updates,
            updated_at=now,
        )

    # ── core algorithm ───────────────────────────────────────────────

    def predict(self, x: np.ndarray, alpha: float) -> float:
        """Return UCB score: θᵀx + α√(xᵀA⁻¹x)."""
        A_inv = np.linalg.solve(self.A, np.eye(self.d, dtype=np.float64))
        theta = A_inv @ self.b
        mean = float(theta @ x)
        exploration = alpha * float(math.sqrt(max(x @ A_inv @ x, 0.0)))
        return mean + exploration

    def update(self, x: np.ndarray, reward: float) -> None:
        """Update model with observed (feature, reward) pair."""
        self.A += np.outer(x, x)
        self.b += reward * x
        self.n_updates += 1


# ── BanditLearner (high-level API) ───────────────────────────────────────


class BanditLearner:
    """Manages LinUCB contexts across task_types, backed by a store."""

    def __init__(self, store: Any, username: str) -> None:
        self._store = store
        self._username = username
        self._contexts: Dict[str, LinUCBContext] = {}

    def _load_context(self, task_type: str) -> LinUCBContext:
        if task_type in self._contexts:
            return self._contexts[task_type]

        ctx: LinUCBContext
        if hasattr(self._store, "get_bandit_model"):
            try:
                doc = self._store.get_bandit_model(username=self._username, task_type=task_type)
                if doc:
                    state = LinUCBModelState.model_validate(doc)
                    ctx = LinUCBContext.from_state(state)
                    self._contexts[task_type] = ctx
                    return ctx
            except Exception:
                logger.debug("Failed to load bandit model for %s/%s", self._username, task_type, exc_info=True)

        ctx = LinUCBContext()
        self._contexts[task_type] = ctx
        return ctx

    def _persist_context(self, task_type: str, ctx: LinUCBContext) -> None:
        if not hasattr(self._store, "save_bandit_model"):
            return
        try:
            state = ctx.to_state(task_type)
            self._store.save_bandit_model(username=self._username, model_state=state)
        except Exception:
            logger.debug("Failed to persist bandit model for %s/%s", self._username, task_type, exc_info=True)

    def get_bias(self, task_type: str, features: BidFeatures) -> float:
        """Return the learned context bias for a (task_type, features) pair.

        Returns 0.0 when the bandit is disabled.
        """
        if not _bandit_enabled():
            return 0.0
        ctx = self._load_context(task_type)
        x = features_to_array(features)
        alpha = _get_env_float("CNP_BANDIT_ALPHA", 1.0)
        return ctx.predict(x, alpha)

    def should_explore(self) -> bool:
        """Epsilon-greedy: return True with probability epsilon."""
        if not _bandit_enabled():
            return False
        epsilon = _get_env_float("CNP_BANDIT_EPSILON", 0.1)
        return random.random() < epsilon

    def record_reward(
        self, task_type: str, features: BidFeatures, reward: float
    ) -> None:
        """Update the bandit model with an observed reward."""
        if not _bandit_enabled():
            return
        ctx = self._load_context(task_type)
        x = features_to_array(features)
        ctx.update(x, reward)
        self._persist_context(task_type, ctx)
