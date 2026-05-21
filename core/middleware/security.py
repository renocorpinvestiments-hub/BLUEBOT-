"""
RENOCORP CORE SECURITY MIDDLEWARE
=================================

Institutional-grade security interception middleware.

Responsibilities:
- authentication interception
- authorization enforcement
- token validation
- replay protection
- signature verification
- rate limiting hooks
- abuse prevention
- request security validation
- IP filtering
- permission gating
- context hardening

DOES NOT:
- own identity systems
- manage user databases
- manage business permissions
- perform observability analytics
- orchestrate runtime state
- implement transport security stacks
- own cryptographic infrastructure

Architecture Boundaries:
middleware/security.py = interception layer only
security/              = policy ownership + crypto systems
runtime/               = orchestration
observability/         = telemetry analysis
cache/                 = distributed caching
resilience/            = circuit/retry/load management

Design Goals:
- async-first
- stateless validation
- deterministic execution
- replay-safe
- constant-time comparisons
- hot-reload-safe policies
- immutable security context
- fault isolation
- minimal allocations
- zero shared mutable state
- capability-safe interception
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import hmac
import ipaddress
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import (
    Any,
    Awaitable,
    Callable,
    Deque,
    Dict,
    FrozenSet,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
)

# ============================================================
# OPTIONAL CACHE INTEGRATION
# ============================================================

try:
    from core.cache import CacheProtocol  # type: ignore
except Exception:  # pragma: no cover
    CacheProtocol = Any

# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_REPLAY_WINDOW_SECONDS = 300
DEFAULT_RATE_LIMIT_WINDOW = 60
DEFAULT_RATE_LIMIT_REQUESTS = 100
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_CONTEXT_KEY = "security"

# ============================================================
# EXCEPTIONS
# ============================================================


class SecurityMiddlewareError(RuntimeError):
    """Base middleware security exception."""


class AuthenticationError(SecurityMiddlewareError):
    """Authentication failure."""


class AuthorizationError(SecurityMiddlewareError):
    """Authorization failure."""


class ReplayAttackError(SecurityMiddlewareError):
    """Replay attack detected."""


class RateLimitExceeded(SecurityMiddlewareError):
    """Rate limit exceeded."""


class SignatureValidationError(SecurityMiddlewareError):
    """Request signature validation failure."""


class IPBlockedError(SecurityMiddlewareError):
    """IP blocked by policy."""


# ============================================================
# IMMUTABLE CONTRACTS
# ============================================================


@dataclass(frozen=True, slots=True)
class SecurityPrincipal:
    """Immutable authenticated principal."""

    subject: str
    roles: FrozenSet[str] = field(default_factory=frozenset)
    permissions: FrozenSet[str] = field(default_factory=frozenset)
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True, slots=True)
class SecurityContext:
    """Immutable request security context."""

    request_id: str
    authenticated: bool
    principal: Optional[SecurityPrincipal]
    client_ip: Optional[str]
    issued_at: float
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Immutable rate limit policy."""

    max_requests: int = DEFAULT_RATE_LIMIT_REQUESTS
    window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW


# ============================================================
# SECURITY POLICY PROTOCOLS
# ============================================================


class TokenValidator(Protocol):
    async def validate(
        self,
        token: str,
    ) -> Optional[SecurityPrincipal]:
        ...


class PermissionValidator(Protocol):
    async def validate(
        self,
        principal: SecurityPrincipal,
        permissions: FrozenSet[str],
    ) -> bool:
        ...


# ============================================================
# REPLAY PROTECTION
# ============================================================


class ReplayProtector:
    """
    Lightweight replay attack protection.

    Stateless-compatible.
    Cache-aware.
    Distributed-cache compatible.
    """

    def __init__(
        self,
        *,
        cache: Optional[CacheProtocol] = None,
        window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
    ) -> None:
        self._cache = cache
        self._window_seconds = window_seconds
        self._local_cache: Deque[tuple[str, float]] = deque(maxlen=10_000)

    async def validate(self, nonce: str) -> None:
        now = time.time()

        if self._cache:
            exists = await self._cache.get(nonce)

            if exists:
                raise ReplayAttackError("Replay attack detected")

            await self._cache.set(
                nonce,
                True,
                ttl=self._window_seconds,
            )
            return

        for existing_nonce, ts in self._local_cache:
            if existing_nonce == nonce and now - ts < self._window_seconds:
                raise ReplayAttackError("Replay attack detected")

        self._local_cache.append((nonce, now))


# ============================================================
# RATE LIMITER
# ============================================================


class SlidingWindowRateLimiter:
    """
    Async sliding-window limiter.

    Intentionally lightweight.
    Does not own distributed coordination.
    """

    def __init__(
        self,
        policy: Optional[RateLimitPolicy] = None,
    ) -> None:
        self.policy = policy or RateLimitPolicy()
        self._buckets: Dict[str, Deque[float]] = {}

    async def validate(self, identifier: str) -> None:
        now = time.time()

        bucket = self._buckets.setdefault(identifier, deque())

        while bucket and now - bucket[0] > self.policy.window_seconds:
            bucket.popleft()

        if len(bucket) >= self.policy.max_requests:
            raise RateLimitExceeded(
                f"Rate limit exceeded for '{identifier}'"
            )

        bucket.append(now)


# ============================================================
# SIGNATURE VALIDATION
# ============================================================


class SignatureVerifier:
    """
    Constant-time HMAC verifier.

    Crypto ownership belongs to core/security/.
    This is interception-only validation.
    """

    @staticmethod
    def verify(
        payload: bytes,
        signature: str,
        secret: str,
        *,
        algorithm: str = "sha256",
    ) -> None:
        digest = hmac.new(
            secret.encode("utf-8"),
            payload,
            getattr(hashlib, algorithm),
        ).hexdigest()

        if not secrets.compare_digest(digest, signature):
            raise SignatureValidationError("Invalid request signature")


# ============================================================
# BASE SECURITY MIDDLEWARE
# ============================================================


class BaseSecurityMiddleware(abc.ABC):
    """
    Base async interception middleware.

    Compatible with middleware/base.py pipeline execution.

    This file intentionally avoids:
    - pipeline orchestration
    - runtime lifecycle management
    - transport ownership
    - observability analysis
    """

    @abc.abstractmethod
    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Execute middleware interception."""


# ============================================================
# AUTHENTICATION MIDDLEWARE
# ============================================================


class AuthenticationMiddleware(BaseSecurityMiddleware):
    """
    Stateless async authentication interceptor.

    Features:
    - token extraction
    - principal validation
    - immutable security context injection
    - cache-aware compatibility
    """

    def __init__(
        self,
        validator: TokenValidator,
        *,
        context_key: str = DEFAULT_CONTEXT_KEY,
    ) -> None:
        self._validator = validator
        self._context_key = context_key

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        token = self._extract_token(context)

        if not token:
            raise AuthenticationError("Missing authentication token")

        principal = await self._validator.validate(token)

        if not principal:
            raise AuthenticationError("Invalid authentication token")

        security_context = SecurityContext(
            request_id=context.get("request_id", "unknown"),
            authenticated=True,
            principal=principal,
            client_ip=context.get("client_ip"),
            issued_at=time.time(),
        )

        context[self._context_key] = security_context

        return await call_next()

    @staticmethod
    def _extract_token(context: Mapping[str, Any]) -> Optional[str]:
        auth_header = context.get("authorization")

        if not auth_header:
            return None

        if not isinstance(auth_header, str):
            return None

        parts = auth_header.split(" ", 1)

        if len(parts) != 2:
            return None

        return parts[1].strip()


# ============================================================
# AUTHORIZATION MIDDLEWARE
# ============================================================


class AuthorizationMiddleware(BaseSecurityMiddleware):
    """
    Immutable permission interception.

    Business permissions are NOT owned here.
    This middleware only enforces pre-defined capabilities.
    """

    def __init__(
        self,
        required_permissions: Sequence[str],
        validator: PermissionValidator,
        *,
        context_key: str = DEFAULT_CONTEXT_KEY,
    ) -> None:
        self._permissions = frozenset(required_permissions)
        self._validator = validator
        self._context_key = context_key

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        security_context = context.get(self._context_key)

        if not isinstance(security_context, SecurityContext):
            raise AuthorizationError("Missing security context")

        principal = security_context.principal

        if not principal:
            raise AuthorizationError("Missing principal")

        valid = await self._validator.validate(
            principal,
            self._permissions,
        )

        if not valid:
            raise AuthorizationError("Permission denied")

        return await call_next()


# ============================================================
# RATE LIMIT MIDDLEWARE
# ============================================================


class RateLimitMiddleware(BaseSecurityMiddleware):
    """
    Abuse prevention interceptor.

    Lightweight.
    Runtime-safe.
    No distributed coordination ownership.
    """

    def __init__(
        self,
        limiter: SlidingWindowRateLimiter,
        *,
        identity_key: str = "client_ip",
    ) -> None:
        self._limiter = limiter
        self._identity_key = identity_key

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        identifier = str(context.get(self._identity_key, "unknown"))

        await self._limiter.validate(identifier)

        return await call_next()


# ============================================================
# REPLAY PROTECTION MIDDLEWARE
# ============================================================


class ReplayProtectionMiddleware(BaseSecurityMiddleware):
    """
    Nonce replay protection interceptor.

    Prevents replayed requests/events.
    """

    def __init__(
        self,
        protector: ReplayProtector,
        *,
        nonce_key: str = "nonce",
    ) -> None:
        self._protector = protector
        self._nonce_key = nonce_key

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        nonce = context.get(self._nonce_key)

        if not nonce:
            raise ReplayAttackError("Missing nonce")

        await self._protector.validate(str(nonce))

        return await call_next()


# ============================================================
# SIGNATURE MIDDLEWARE
# ============================================================


class SignatureValidationMiddleware(BaseSecurityMiddleware):
    """
    HMAC request validation interceptor.

    Interception-only validation layer.
    """

    def __init__(
        self,
        secret: str,
        *,
        payload_key: str = "payload",
        signature_key: str = "signature",
    ) -> None:
        self._secret = secret
        self._payload_key = payload_key
        self._signature_key = signature_key

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        payload = context.get(self._payload_key)
        signature = context.get(self._signature_key)

        if payload is None or signature is None:
            raise SignatureValidationError(
                "Missing signature validation fields"
            )

        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        SignatureVerifier.verify(
            payload=payload,
            signature=str(signature),
            secret=self._secret,
        )

        return await call_next()


# ============================================================
# IP FILTER MIDDLEWARE
# ============================================================


class IPFilterMiddleware(BaseSecurityMiddleware):
    """
    CIDR-aware IP filtering middleware.

    Stateless.
    Fast.
    Immutable network rules.
    """

    def __init__(
        self,
        *,
        allowed_networks: Optional[Sequence[str]] = None,
        blocked_networks: Optional[Sequence[str]] = None,
        client_ip_key: str = "client_ip",
    ) -> None:
        self._allowed = tuple(
            ipaddress.ip_network(net)
            for net in (allowed_networks or ())
        )

        self._blocked = tuple(
            ipaddress.ip_network(net)
            for net in (blocked_networks or ())
        )

        self._client_ip_key = client_ip_key

    async def intercept(
        self,
        context: MutableMapping[str, Any],
        call_next: Callable[[], Awaitable[Any]],
    ) -> Any:
        raw_ip = context.get(self._client_ip_key)

        if not raw_ip:
            raise IPBlockedError("Missing client IP")

        client_ip = ipaddress.ip_address(str(raw_ip))

        for blocked in self._blocked:
            if client_ip in blocked:
                raise IPBlockedError("Blocked client IP")

        if self._allowed:
            if not any(client_ip in net for net in self._allowed):
                raise IPBlockedError("Client IP not allowed")

        return await call_next()


# ============================================================
# SECURITY CHAIN
# ============================================================


class SecurityChain:
    """
    Lightweight security middleware composition.

    This is NOT a full middleware runtime.
    middleware/base.py owns orchestration.

    This utility only composes security interceptors.
    """

    def __init__(
        self,
        middlewares: Sequence[BaseSecurityMiddleware],
    ) -> None:
        self._middlewares = tuple(middlewares)

    async def execute(
        self,
        context: MutableMapping[str, Any],
        endpoint: Callable[[], Awaitable[Any]],
    ) -> Any:
        async def invoke(index: int) -> Any:
            if index >= len(self._middlewares):
                return await endpoint()

            middleware = self._middlewares[index]

            return await middleware.intercept(
                context,
                lambda: invoke(index + 1),
            )

        return await invoke(0)


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "AuthenticationError",
    "AuthenticationMiddleware",
    "AuthorizationError",
    "AuthorizationMiddleware",
    "BaseSecurityMiddleware",
    "IPBlockedError",
    "IPFilterMiddleware",
    "PermissionValidator",
    "RateLimitExceeded",
    "RateLimitMiddleware",
    "RateLimitPolicy",
    "ReplayAttackError",
    "ReplayProtectionMiddleware",
    "ReplayProtector",
    "SecurityChain",
    "SecurityContext",
    "SecurityMiddlewareError",
    "SecurityPrincipal",
    "SignatureValidationError",
    "SignatureValidationMiddleware",
    "SignatureVerifier",
    "SlidingWindowRateLimiter",
    "TokenValidator",
]
