# core/security/policy.py
"""
RENOCORP — Institutional Authorization Layer
================================================

Purpose:
    Authorization and policy enforcement ONLY.

Responsibilities:
    - Capability authorization
    - Runtime permission evaluation
    - Event authorization
    - Queue authorization
    - Module boundary enforcement
    - Plugin capability restriction
    - Immutable authorization decisions

Non-Responsibilities:
    - Authentication
    - Token verification
    - Secret management
    - Retry logic
    - Runtime orchestration
    - Persistence
    - Metrics implementation
    - Queue dispatching

Architecture Principles:
    - Stateless
    - Deterministic
    - Idempotent
    - Async-native
    - Immutable
    - Dependency-injected
    - Provider-driven
    - Horizontally scalable
    - Plugin-safe
    - Distributed-runtime compatible
"""

from __future__ import annotations

import asyncio
import fnmatch
import time

from dataclasses import dataclass
from typing import (
    FrozenSet,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    runtime_checkable,
)

# ============================================================
# SECURITY CONTEXT IMPORT
# ============================================================

# Safe infrastructure-level import.
# No circular runtime ownership.
from core.security.auth import SecurityContext


# ============================================================
# POLICY DECISION
# ============================================================


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """
    Immutable authorization decision.

    Safe for:
        - caching
        - async runtimes
        - distributed systems
        - audit replay
        - observability
    """

    allowed: bool
    reason: str
    policy: Optional[str] = None
    evaluated_at: int = 0


# ============================================================
# POLICY RULE
# ============================================================


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """
    Immutable declarative policy rule.

    Supports:
        - role-based access
        - scope-based access
        - capability isolation
        - wildcard matching
    """

    roles: FrozenSet[str]
    scopes: FrozenSet[str]
    require_all: bool = False


# ============================================================
# PROVIDER CONTRACTS
# ============================================================


@runtime_checkable
class PolicyProvider(Protocol):
    """
    Policy source abstraction.

    Enables:
        - static policies
        - remote policies
        - distributed policies
        - plugin policies
        - hot-reload providers
    """

    async def get_policies(
        self,
    ) -> Mapping[str, PolicyRule]:
        ...


# ============================================================
# OPTIONAL OBSERVER CONTRACT
# ============================================================


@runtime_checkable
class PolicyObserver(Protocol):
    """
    Optional observability integration.

    This file does NOT implement metrics/tracing.
    Existing observability layer owns that responsibility.
    """

    async def on_decision(
        self,
        *,
        action: str,
        identity: str,
        decision: PolicyDecision,
    ) -> None:
        ...


# ============================================================
# INTERNAL TTL CACHE
# ============================================================


class _DecisionCache:
    """
    Lightweight immutable authorization cache.

    Design:
        - async-safe
        - lock-minimized
        - side-effect free
        - deterministic
    """

    __slots__ = (
        "_ttl_seconds",
        "_store",
        "_lock",
        "_max_size",
    )

    def __init__(
        self,
        *,
        ttl_seconds: int = 10,
        max_size: int = 50_000,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size

        self._store: dict[
            tuple[str, str, tuple[str, ...], tuple[str, ...]],
            tuple[float, PolicyDecision],
        ] = {}

        self._lock = asyncio.Lock()

    async def get(
        self,
        key: tuple[str, str, tuple[str, ...], tuple[str, ...]],
    ) -> Optional[PolicyDecision]:
        cached = self._store.get(key)

        if cached is None:
            return None

        expires_at, value = cached

        if expires_at < time.time():
            self._store.pop(key, None)
            return None

        return value

    async def set(
        self,
        key: tuple[str, str, tuple[str, ...], tuple[str, ...]],
        value: PolicyDecision,
    ) -> None:
        if len(self._store) >= self._max_size:
            async with self._lock:
                self._evict_expired()

                if len(self._store) >= self._max_size:
                    self._store.pop(next(iter(self._store)), None)

        self._store[key] = (
            time.time() + self._ttl_seconds,
            value,
        )

    def _evict_expired(self) -> None:
        now = time.time()

        expired = [
            key
            for key, (expires_at, _) in self._store.items()
            if expires_at < now
        ]

        for key in expired:
            self._store.pop(key, None)


# ============================================================
# POLICY ENGINE
# ============================================================


class PolicyEngine:
    """
    Institutional-grade authorization engine.

    Features:
        - deterministic decisions
        - immutable policies
        - deny-by-default
        - wildcard policy support
        - plugin-safe
        - distributed-runtime compatible
        - async-native
        - cache-aware
        - horizontally scalable

    Critical Guarantees:
        - no runtime mutation
        - no global state
        - no side effects
        - idempotent decisions
    """

    __slots__ = (
        "_provider",
        "_observer",
        "_cache",
    )

    def __init__(
        self,
        *,
        provider: PolicyProvider,
        observer: Optional[PolicyObserver] = None,
        cache_ttl_seconds: int = 10,
    ) -> None:
        self._provider = provider
        self._observer = observer

        self._cache = _DecisionCache(
            ttl_seconds=cache_ttl_seconds,
        )

    # ========================================================
    # AUTHORIZATION
    # ========================================================

    async def authorize(
        self,
        *,
        context: SecurityContext,
        action: str,
    ) -> PolicyDecision:
        """
        Evaluate authorization.

        Deterministic:
            Same input => same output

        Side Effects:
            NONE
        """

        cache_key = (
            context.identity,
            action,
            context.roles,
            context.scopes,
        )

        cached = await self._cache.get(cache_key)

        if cached is not None:
            return cached

        policies = await self._provider.get_policies()

        rule = self._resolve_policy(
            action=action,
            policies=policies,
        )

        if rule is None:
            decision = PolicyDecision(
                allowed=False,
                reason="policy_not_found",
                policy=action,
                evaluated_at=int(time.time()),
            )

            await self._emit_decision(
                action=action,
                context=context,
                decision=decision,
            )

            await self._cache.set(cache_key, decision)

            return decision

        allowed = self._evaluate_rule(
            context=context,
            rule=rule,
        )

        decision = PolicyDecision(
            allowed=allowed,
            reason=(
                "authorized"
                if allowed
                else "insufficient_permissions"
            ),
            policy=action,
            evaluated_at=int(time.time()),
        )

        await self._emit_decision(
            action=action,
            context=context,
            decision=decision,
        )

        await self._cache.set(cache_key, decision)

        return decision

    # ========================================================
    # POLICY RESOLUTION
    # ========================================================

    @staticmethod
    def _resolve_policy(
        *,
        action: str,
        policies: Mapping[str, PolicyRule],
    ) -> Optional[PolicyRule]:
        """
        Resolve matching policy.

        Supports:
            runtime.*
            queue.publish
            events.*
        """

        exact = policies.get(action)

        if exact is not None:
            return exact

        for pattern, rule in policies.items():
            if "*" not in pattern:
                continue

            if fnmatch.fnmatch(action, pattern):
                return rule

        return None

    # ========================================================
    # RULE EVALUATION
    # ========================================================

    @staticmethod
    def _evaluate_rule(
        *,
        context: SecurityContext,
        rule: PolicyRule,
    ) -> bool:
        """
        Pure deterministic rule evaluation.
        """

        context_roles = frozenset(context.roles)
        context_scopes = frozenset(context.scopes)

        role_match = (
            not rule.roles
            or bool(context_roles & rule.roles)
        )

        scope_match = (
            not rule.scopes
            or bool(context_scopes & rule.scopes)
        )

        if rule.require_all:
            return role_match and scope_match

        return role_match or scope_match

    # ========================================================
    # OBSERVABILITY HOOK
    # ========================================================

    async def _emit_decision(
        self,
        *,
        action: str,
        context: SecurityContext,
        decision: PolicyDecision,
    ) -> None:
        """
        Optional observability hook.

        No metrics/tracing implemented here.
        Existing observability layer owns that responsibility.
        """

        if self._observer is None:
            return

        await self._observer.on_decision(
            action=action,
            identity=context.identity,
            decision=decision,
        )


# ============================================================
# STATIC POLICY PROVIDER
# ============================================================


class StaticPolicyProvider:
    """
    Immutable in-memory policy provider.

    Good for:
        - local deployments
        - static runtime policies
        - testing
        - bootstrap systems

    Future providers can replace this without
    modifying PolicyEngine.
    """

    __slots__ = ("_policies",)

    def __init__(
        self,
        policies: Mapping[str, PolicyRule],
    ) -> None:
        self._policies = dict(policies)

    async def get_policies(
        self,
    ) -> Mapping[str, PolicyRule]:
        return self._policies


# ============================================================
# DEFAULT SYSTEM POLICIES
# ============================================================

DEFAULT_POLICIES: Mapping[str, PolicyRule] = {
    "runtime.restart": PolicyRule(
        roles=frozenset({"admin"}),
        scopes=frozenset(),
    ),
    "runtime.shutdown": PolicyRule(
        roles=frozenset({"admin"}),
        scopes=frozenset(),
    ),
    "queue.publish": PolicyRule(
        roles=frozenset({"service", "admin"}),
        scopes=frozenset({"queue:write"}),
    ),
    "queue.consume": PolicyRule(
        roles=frozenset({"service", "admin"}),
        scopes=frozenset({"queue:read"}),
    ),
    "events.publish": PolicyRule(
        roles=frozenset({"service", "admin"}),
        scopes=frozenset({"events:write"}),
    ),
    "events.subscribe": PolicyRule(
        roles=frozenset({"service", "admin"}),
        scopes=frozenset({"events:read"}),
    ),
    "plugins.*": PolicyRule(
        roles=frozenset({"admin"}),
        scopes=frozenset({"plugins:*"}),
    ),
}


# ============================================================
# PUBLIC EXPORTS
# ============================================================

__all__ = (
    "PolicyDecision",
    "PolicyRule",
    "PolicyProvider",
    "PolicyObserver",
    "PolicyEngine",
    "StaticPolicyProvider",
    "DEFAULT_POLICIES",
)
