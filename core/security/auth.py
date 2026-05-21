# core/security/auth.py
"""
RENOCORP Institutional Authentication Layer
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


# ============================================================
# SECURITY CONTEXT
# ============================================================


@dataclass(frozen=True, slots=True)
class SecurityContext:
    identity: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    issued_at: int
    expires_at: int
    issuer: str
    subject: str
    token_id: Optional[str] = None


# ============================================================
# EXCEPTIONS
# ============================================================


class AuthenticationError(Exception):
    __slots__ = ()


class InvalidTokenError(AuthenticationError):
    __slots__ = ()


class TokenExpiredError(AuthenticationError):
    __slots__ = ()


class InvalidSignatureError(AuthenticationError):
    __slots__ = ()


class InvalidApiKeyError(AuthenticationError):
    __slots__ = ()


# ============================================================
# PROVIDER CONTRACTS
# ============================================================


@runtime_checkable
class KeyProvider(Protocol):
    async def get_signing_key(self, key_id: str) -> str:
        ...


@runtime_checkable
class ApiKeyProvider(Protocol):
    async def resolve_identity(
        self,
        api_key: str,
    ) -> Optional[SecurityContext]:
        ...


@runtime_checkable
class RevocationProvider(Protocol):
    async def is_revoked(self, token_id: str) -> bool:
        ...


# ============================================================
# TTL CACHE
# ============================================================


class _TTLCache:
    __slots__ = (
        "_store",
        "_ttl",
        "_max",
        "_lock",
    )

    def __init__(
        self,
        ttl_seconds: int = 30,
        max_size: int = 10000,
    ) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl_seconds
        self._max = max_size
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)

        if item is None:
            return None

        expires, value = item

        if expires <= time.time():
            self._store.pop(key, None)
            return None

        return value

    async def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max:
            async with self._lock:
                self._evict()

                if len(self._store) >= self._max:
                    self._store.pop(
                        next(iter(self._store)),
                        None,
                    )

        self._store[key] = (
            time.time() + self._ttl,
            value,
        )

    def _evict(self) -> None:
        now = time.time()

        expired = [
            k
            for k, (exp, _) in self._store.items()
            if exp <= now
        ]

        for k in expired:
            self._store.pop(k, None)


# ============================================================
# AUTH MANAGER
# ============================================================


class AuthManager:
    __slots__ = (
        "_key_provider",
        "_api_key_provider",
        "_revocation_provider",
        "_algorithm",
        "_cache",
    )

    REQUIRED_CLAIMS = (
        "exp",
        "iat",
        "iss",
        "sub",
    )

    ACCESS_TYPE = "access"

    def __init__(
        self,
        *,
        key_provider: KeyProvider,
        api_key_provider: Optional[
            ApiKeyProvider
        ] = None,
        revocation_provider: Optional[
            RevocationProvider
        ] = None,
        algorithm: str = "HS256",
        cache_ttl_seconds: int = 30,
    ) -> None:
        self._key_provider = key_provider
        self._api_key_provider = api_key_provider
        self._revocation_provider = revocation_provider
        self._algorithm = algorithm
        self._cache = _TTLCache(
            ttl_seconds=cache_ttl_seconds
        )

    # ========================================================
    # JWT
    # ========================================================

    async def verify_jwt(
        self,
        token: str,
    ) -> SecurityContext:

        cached = await self._cache.get(token)

        if cached is not None:
            return cached

        parts = token.split(".")

        if len(parts) != 3:
            raise InvalidTokenError(
                "malformed_token"
            )

        header_b64, payload_b64, signature_b64 = (
            parts
        )

        header = self._decode_json(
            header_b64
        )

        payload = self._decode_json(
            payload_b64
        )

        self._validate_claims(payload)

        token_type = payload.get("type")

        if token_type != self.ACCESS_TYPE:
            raise InvalidTokenError(
                "invalid_token_type"
            )

        algorithm = str(
            header.get("alg", "")
        )

        if not hmac.compare_digest(
            algorithm,
            self._algorithm,
        ):
            raise InvalidTokenError(
                "unsupported_algorithm"
            )

        key_id = str(
            header.get("kid", "default")
        )

        signing_key = (
            await self._key_provider.get_signing_key(
                key_id
            )
        )

        signing_input = (
            f"{header_b64}.{payload_b64}"
        )

        expected = self._sign(
            signing_input,
            signing_key,
        )

        provided = self._urlsafe_decode(
            signature_b64
        )

        if not hmac.compare_digest(
            expected,
            provided,
        ):
            raise InvalidSignatureError(
                "invalid_signature"
            )

        now = int(time.time())

        expires = int(payload["exp"])

        if expires <= now:
            raise TokenExpiredError(
                "token_expired"
            )

        token_id = payload.get("jti")

        if (
            token_id is not None
            and self._revocation_provider
        ):
            revoked = await (
                self._revocation_provider.is_revoked(
                    token_id
                )
            )

            if revoked:
                raise InvalidTokenError(
                    "token_revoked"
                )

        context = SecurityContext(
            identity=str(
                payload["sub"]
            ),
            roles=tuple(
                str(x)
                for x in payload.get(
                    "roles",
                    (),
                )
            ),
            scopes=tuple(
                str(x)
                for x in payload.get(
                    "scopes",
                    (),
                )
            ),
            issued_at=int(
                payload["iat"]
            ),
            expires_at=expires,
            issuer=str(
                payload["iss"]
            ),
            subject=str(
                payload["sub"]
            ),
            token_id=token_id,
        )

        await self._cache.set(
            token,
            context,
        )

        return context

    # ========================================================
    # API KEY
    # ========================================================

    async def verify_api_key(
        self,
        api_key: str,
    ) -> SecurityContext:

        if self._api_key_provider is None:
            raise InvalidApiKeyError(
                "api_key_provider_not_configured"
            )

        cached = await self._cache.get(
            api_key
        )

        if cached is not None:
            return cached

        context = (
            await self._api_key_provider.resolve_identity(
                api_key
            )
        )

        if context is None:
            raise InvalidApiKeyError(
                "invalid_api_key"
            )

        await self._cache.set(
            api_key,
            context,
        )

        return context

    # ========================================================
    # HMAC
    # ========================================================

    async def verify_hmac(
        self,
        *,
        body: bytes,
        signature: str,
        key_id: str = "default",
    ) -> bool:

        signing_key = (
            await self._key_provider.get_signing_key(
                key_id
            )
        )

        expected = hmac.new(
            signing_key.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(
            expected,
            signature,
        )

    # ========================================================
    # INTERNALS
    # ========================================================

    def _validate_claims(
        self,
        payload: Mapping[str, Any],
    ) -> None:

        for claim in self.REQUIRED_CLAIMS:
            if claim not in payload:
                raise InvalidTokenError(
                    f"missing_claim:{claim}"
                )

    @staticmethod
    def _decode_json(
        value: str,
    ) -> Mapping[str, Any]:

        decoded = (
            AuthManager._urlsafe_decode(
                value
            )
        )

        try:
            obj = json.loads(
                decoded.decode(
                    "utf-8"
                )
            )
        except Exception as exc:
            raise InvalidTokenError(
                "invalid_json"
            ) from exc

        if not isinstance(
            obj,
            Mapping,
        ):
            raise InvalidTokenError(
                "invalid_payload"
            )

        return obj

    @staticmethod
    def _urlsafe_decode(
        value: str,
    ) -> bytes:

        padding = "=" * (
            -len(value) % 4
        )

        try:
            return (
                base64.urlsafe_b64decode(
                    value + padding
                )
            )
        except Exception as exc:
            raise InvalidTokenError(
                "invalid_encoding"
            ) from exc

    @staticmethod
    def _sign(
        message: str,
        secret: str,
    ) -> bytes:

        return hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).digest()


# ============================================================
# STATIC PROVIDER
# ============================================================


class StaticKeyProvider:
    __slots__ = ("_keys",)

    def __init__(
        self,
        keys: Mapping[str, str],
    ) -> None:
        self._keys = dict(keys)

    async def get_signing_key(
        self,
        key_id: str,
    ) -> str:

        key = self._keys.get(
            key_id
        )

        if key is None:
            raise InvalidTokenError(
                "unknown_signing_key"
            )

        return key


__all__ = (
    "AuthManager",
    "SecurityContext",
    "AuthenticationError",
    "InvalidTokenError",
    "TokenExpiredError",
    "InvalidSignatureError",
    "InvalidApiKeyError",
    "KeyProvider",
    "ApiKeyProvider",
    "RevocationProvider",
    "StaticKeyProvider",
)
