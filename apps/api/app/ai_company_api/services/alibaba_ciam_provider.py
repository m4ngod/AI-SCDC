from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from jwt import InvalidSignatureError, InvalidTokenError, PyJWK
from jwt.exceptions import PyJWKError

from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProviderError,
    CustomerIdentityProviderNetworkError,
    CustomerIdentityProviderRateLimited,
    CustomerIdentityProviderServiceUnavailable,
    CustomerIdentityProviderTimeout,
    OidcAuthorizationRequest,
    OidcDiscovery,
    OidcTokenResponse,
    ValidatedExternalIdentity,
)


AUTHORIZATION_SCOPE = "USER_API openid email"
MANAGEMENT_SCOPE = "MANAGEMENT_APPLICATION_API"
SUPPORTED_ID_TOKEN_ALGORITHM = "RS256"
MANAGEMENT_TOKEN_EXPIRY_SKEW = timedelta(seconds=30)
MISSING_USER_STATUS_CODES = frozenset({
    "Operation.Failure.User.NotFound",
})


@dataclass(frozen=True)
class AlibabaCiamConfig:
    tenant_base_url: str
    management_api_base_url: str
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    request_timeout_seconds: float = 5.0
    clock_skew_seconds: int = 60
    jwks_cache_seconds: int = 300

    def __post_init__(self) -> None:
        tenant_base_url = self.tenant_base_url.rstrip("/")
        management_api_base_url = (
            self.management_api_base_url.rstrip("/")
        )
        issuer = self.issuer.rstrip("/")
        if (
            not _is_https_url(tenant_base_url)
            or urlparse(tenant_base_url).path not in {"", "/"}
            or not _is_https_url(management_api_base_url)
            or urlparse(management_api_base_url).path not in {"", "/"}
            or not _is_https_url(issuer)
            or not _same_origin(tenant_base_url, issuer)
            or not self.client_id.strip()
            or not self.client_secret
            or self.request_timeout_seconds <= 0
            or not 0 <= self.clock_skew_seconds <= 300
            or not 30 <= self.jwks_cache_seconds <= 900
        ):
            raise ValueError("Alibaba CIAM configuration is not valid")
        object.__setattr__(
            self,
            "tenant_base_url",
            tenant_base_url,
        )
        object.__setattr__(
            self,
            "management_api_base_url",
            management_api_base_url,
        )
        object.__setattr__(self, "issuer", issuer)


@dataclass(frozen=True)
class _ValidatedDiscovery:
    public: OidcDiscovery
    jwks_uri: str


@dataclass(frozen=True)
class _ManagementAccessToken:
    value: str = field(repr=False)
    expires_at: datetime


class AlibabaCiamCustomerIdentityProvider:
    def __init__(
        self,
        config: AlibabaCiamConfig,
        *,
        http_client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self.client_id = config.client_id
        self._http_client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
        )
        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )
        self._cache_lock = Lock()
        self._discovery: _ValidatedDiscovery | None = None
        self._jwks_by_kid: dict[str, Any] | None = None
        self._jwks_expires_at: datetime | None = None
        self._management_token: _ManagementAccessToken | None = None

    def discover(self) -> OidcDiscovery:
        return self._validated_discovery().public

    def authorization_url(
        self,
        request: OidcAuthorizationRequest,
    ) -> str:
        if request.client_id != self.client_id:
            raise CustomerIdentityProviderError(
                "CIAM authorization request is not valid"
            )
        endpoint = self._validated_discovery().public.authorization_endpoint
        parameters: dict[str, str | int] = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": request.redirect_uri,
            "scope": AUTHORIZATION_SCOPE,
            "state": request.state,
            "nonce": request.nonce,
            "code_challenge": request.code_challenge,
            "code_challenge_method": "S256",
        }
        if request.prompt is not None:
            parameters["prompt"] = request.prompt
        if request.max_age_seconds is not None:
            parameters["max_age"] = request.max_age_seconds
        if request.acr_values is not None:
            parameters["acr_values"] = request.acr_values
        return f"{endpoint}?{urlencode(parameters)}"

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OidcTokenResponse:
        endpoint = self._validated_discovery().public.token_endpoint
        payload = self._request_json(
            "POST",
            endpoint,
            json={
                "client_id": self.client_id,
                "client_secret": self._config.client_secret,
                "grant_type": "authorization_code",
                "scope": AUTHORIZATION_SCOPE,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            operation="token exchange",
        )
        id_token = payload.get("id_token")
        access_token = payload.get("access_token")
        if (
            not isinstance(id_token, str)
            or not id_token
            or not isinstance(access_token, str)
            or not access_token
        ):
            raise CustomerIdentityProviderError(
                "CIAM token response is not valid"
            )
        return OidcTokenResponse(
            id_token=id_token,
            access_token=access_token,
        )

    def validate_id_token(
        self,
        id_token: str,
        *,
        expected_audience: str,
    ) -> ValidatedExternalIdentity:
        header = self._unverified_header(id_token)
        signing_key = self._signing_key(
            header,
            refresh=False,
        )
        try:
            claims = self._decode_id_token(
                id_token,
                signing_key=signing_key,
                expected_audience=expected_audience,
            )
        except InvalidSignatureError:
            signing_key = self._signing_key(
                header,
                refresh=True,
            )
            try:
                claims = self._decode_id_token(
                    id_token,
                    signing_key=signing_key,
                    expected_audience=expected_audience,
                )
            except InvalidTokenError:
                raise _invalid_id_token() from None
        except InvalidTokenError:
            raise _invalid_id_token() from None

        self._validate_time_claims(claims)
        subject = claims.get("sub")
        nonce = claims.get("nonce")
        email = claims.get("email")
        email_verified = claims.get("email_verified")
        if (
            not isinstance(subject, str)
            or not subject
            or not isinstance(nonce, str)
            or not nonce
            or not isinstance(email, str)
            or not email
            or email_verified is not True
        ):
            raise _invalid_id_token()
        authenticated_at = _optional_numeric_date(
            claims.get("auth_time")
        )
        authentication_context = claims.get("acr")
        if (
            authentication_context is not None
            and not isinstance(authentication_context, str)
        ):
            raise _invalid_id_token()
        return ValidatedExternalIdentity(
            issuer=self._config.issuer,
            subject=subject,
            email=email,
            nonce=nonce,
            authenticated_at=authenticated_at,
            authentication_context=authentication_context,
        )

    def end_session_url(
        self,
        *,
        post_logout_redirect_uri: str,
    ) -> str | None:
        endpoint = self._validated_discovery().public.end_session_endpoint
        if endpoint is None:
            return None
        parameters = {
            "client_id": self.client_id,
            "post_logout_redirect_uri": post_logout_redirect_uri,
        }
        return f"{endpoint}?{urlencode(parameters)}"

    def identity_status(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> str:
        if issuer.rstrip("/") != self._config.issuer or not subject:
            raise CustomerIdentityProviderError(
                "CIAM identity status request is not valid"
            )
        for attempt in range(2):
            access_token = self._management_access_token(
                force_refresh=attempt > 0,
            )
            response = self._request(
                "GET",
                self._management_user_endpoint,
                params={"userUuid": subject},
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
                operation="identity status",
                accepted_statuses={200, 401, 404},
            )
            if response.status_code == 401 and attempt == 0:
                self._clear_management_token()
                continue
            if response.status_code == 401:
                raise CustomerIdentityProviderError(
                    "CIAM identity status request was rejected"
                )
            return self._status_from_response(
                response,
                expected_subject=subject,
            )
        raise CustomerIdentityProviderError(
            "CIAM identity status request was rejected"
        )

    @property
    def _discovery_url(self) -> str:
        return (
            f"{self._config.issuer}/"
            ".well-known/openid-configuration"
        )

    @property
    def _management_token_endpoint(self) -> str:
        return (
            f"{self._config.tenant_base_url}/"
            "api/bff/v1.2/developer/ciam/oauth/token"
        )

    @property
    def _management_user_endpoint(self) -> str:
        return (
            f"{self._config.management_api_base_url}/"
            "api/bff/v1.2/developer/ciam/management/user"
        )

    def _validated_discovery(self) -> _ValidatedDiscovery:
        with self._cache_lock:
            cached = self._discovery
        if cached is not None:
            return cached
        payload = self._request_json(
            "GET",
            self._discovery_url,
            operation="discovery",
        )
        validated = self._validate_discovery(payload)
        with self._cache_lock:
            if self._discovery is None:
                self._discovery = validated
            return self._discovery

    def _validate_discovery(
        self,
        payload: dict[str, Any],
    ) -> _ValidatedDiscovery:
        issuer = payload.get("issuer")
        authorization_endpoint = payload.get(
            "authorization_endpoint"
        )
        token_endpoint = payload.get("token_endpoint")
        jwks_uri = payload.get("jwks_uri")
        end_session_endpoint = payload.get("end_session_endpoint")
        required_urls = (
            authorization_endpoint,
            token_endpoint,
            jwks_uri,
        )
        if (
            issuer != self._config.issuer
            or not all(
                isinstance(value, str)
                and _is_https_url(value)
                and _same_origin(
                    self._config.tenant_base_url,
                    value,
                )
                for value in required_urls
            )
            or (
                end_session_endpoint is not None
                and (
                    not isinstance(end_session_endpoint, str)
                    or not _is_https_url(end_session_endpoint)
                    or not _same_origin(
                        self._config.tenant_base_url,
                        end_session_endpoint,
                    )
                )
            )
            or "authorization_code"
            not in _string_list(
                payload.get("grant_types_supported")
            )
            or "code"
            not in _string_list(
                payload.get("response_types_supported")
            )
            or not {
                "USER_API",
                "openid",
                "email",
            }.issubset(
                _string_list(payload.get("scopes_supported"))
            )
            or SUPPORTED_ID_TOKEN_ALGORITHM
            not in _string_list(
                payload.get(
                    "id_token_signing_alg_values_supported"
                )
            )
            or "client_secret_post"
            not in _string_list(
                payload.get(
                    "token_endpoint_auth_methods_supported"
                )
            )
        ):
            raise CustomerIdentityProviderError(
                "CIAM discovery metadata is not valid"
            )
        assert isinstance(authorization_endpoint, str)
        assert isinstance(token_endpoint, str)
        assert isinstance(jwks_uri, str)
        return _ValidatedDiscovery(
            public=OidcDiscovery(
                issuer=self._config.issuer,
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                end_session_endpoint=end_session_endpoint,
            ),
            jwks_uri=jwks_uri,
        )

    def _unverified_header(
        self,
        id_token: str,
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except InvalidTokenError:
            raise _invalid_id_token() from None
        if (
            header.get("alg") != SUPPORTED_ID_TOKEN_ALGORITHM
            or not isinstance(header.get("kid"), str)
            or not header["kid"]
        ):
            raise _invalid_id_token()
        return header

    def _signing_key(
        self,
        header: dict[str, Any],
        *,
        refresh: bool,
    ) -> Any:
        kid = str(header["kid"])
        keys = self._load_jwks(refresh=refresh)
        signing_key = keys.get(kid)
        if signing_key is None and not refresh:
            signing_key = self._load_jwks(refresh=True).get(kid)
        if signing_key is None:
            raise _invalid_id_token()
        return signing_key

    def _load_jwks(self, *, refresh: bool) -> dict[str, Any]:
        now = _as_utc(self._clock())
        with self._cache_lock:
            cached = self._jwks_by_kid
            expires_at = self._jwks_expires_at
        if (
            cached is not None
            and expires_at is not None
            and now < expires_at
            and not refresh
        ):
            return cached
        payload = self._request_json(
            "GET",
            self._validated_discovery().jwks_uri,
            operation="signing keys",
        )
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list):
            raise CustomerIdentityProviderError(
                "CIAM signing keys are not valid"
            )
        keys: dict[str, Any] = {}
        try:
            for raw_key in raw_keys:
                if (
                    not isinstance(raw_key, dict)
                    or raw_key.get("kty") != "RSA"
                    or raw_key.get("alg")
                    not in {None, SUPPORTED_ID_TOKEN_ALGORITHM}
                    or raw_key.get("use") not in {None, "sig"}
                    or not isinstance(raw_key.get("kid"), str)
                ):
                    continue
                keys[str(raw_key["kid"])] = PyJWK.from_dict(
                    raw_key,
                    algorithm=SUPPORTED_ID_TOKEN_ALGORITHM,
                ).key
        except (InvalidTokenError, PyJWKError, ValueError):
            raise CustomerIdentityProviderError(
                "CIAM signing keys are not valid"
            ) from None
        if not keys:
            raise CustomerIdentityProviderError(
                "CIAM signing keys are not valid"
            )
        with self._cache_lock:
            self._jwks_by_kid = keys
            self._jwks_expires_at = (
                _as_utc(self._clock())
                + timedelta(seconds=self._config.jwks_cache_seconds)
            )
        return keys

    def _decode_id_token(
        self,
        id_token: str,
        *,
        signing_key: Any,
        expected_audience: str,
    ) -> dict[str, Any]:
        return jwt.decode(
            id_token,
            signing_key,
            algorithms=[SUPPORTED_ID_TOKEN_ALGORITHM],
            audience=expected_audience,
            issuer=self._config.issuer,
            options={
                "strict_aud": True,
                "require": [
                    "iss",
                    "aud",
                    "sub",
                    "nonce",
                    "iat",
                    "nbf",
                    "exp",
                ],
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
            },
        )

    def _validate_time_claims(
        self,
        claims: dict[str, Any],
    ) -> None:
        now = _as_utc(self._clock())
        leeway = timedelta(
            seconds=self._config.clock_skew_seconds
        )
        issued_at = _numeric_date(claims.get("iat"))
        not_before = _numeric_date(claims.get("nbf"))
        expires_at = _numeric_date(claims.get("exp"))
        if (
            issued_at > now + leeway
            or not_before > now + leeway
            or expires_at < now - leeway
        ):
            raise _invalid_id_token()

    def _management_access_token(
        self,
        *,
        force_refresh: bool,
    ) -> str:
        now = _as_utc(self._clock())
        with self._cache_lock:
            cached = self._management_token
            if (
                cached is not None
                and not force_refresh
                and cached.expires_at
                > now + MANAGEMENT_TOKEN_EXPIRY_SKEW
            ):
                return cached.value
            payload = self._request_json(
                "POST",
                self._management_token_endpoint,
                json={
                    "client_id": self.client_id,
                    "client_secret": self._config.client_secret,
                    "grant_type": "client_credentials",
                    "scope": MANAGEMENT_SCOPE,
                },
                operation="management token",
            )
            access_token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if (
                not isinstance(access_token, str)
                or not access_token
                or not isinstance(expires_in, (int, float))
                or isinstance(expires_in, bool)
                or expires_in <= 0
            ):
                raise CustomerIdentityProviderError(
                    "CIAM management token response is not valid"
                )
            self._management_token = _ManagementAccessToken(
                value=access_token,
                expires_at=now + timedelta(seconds=expires_in),
            )
            return access_token

    def _clear_management_token(self) -> None:
        with self._cache_lock:
            self._management_token = None

    def _status_from_response(
        self,
        response: httpx.Response,
        *,
        expected_subject: str,
    ) -> str:
        payload = _response_json_object(
            response,
            operation="identity status",
        )
        code = payload.get("code")
        if _is_missing_status_code(code):
            return "missing"
        data = payload.get("data")
        if (
            payload.get("success") is not True
            or not isinstance(data, dict)
        ):
            raise CustomerIdentityProviderError(
                "CIAM identity status response is not valid"
            )
        enabled = data.get("enabled")
        locked = data.get("locked")
        user_id = data.get("userId")
        if (
            not isinstance(enabled, bool)
            or not isinstance(locked, bool)
            or user_id != expected_subject
        ):
            raise CustomerIdentityProviderError(
                "CIAM identity status response is not valid"
            )
        if not enabled:
            return "disabled"
        if locked:
            return "locked"
        return "active"

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = self._request(
            method,
            url,
            operation=operation,
            accepted_statuses={200},
            **kwargs,
        )
        return _response_json_object(
            response,
            operation=operation,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        accepted_statuses: set[int],
        **kwargs: Any,
    ) -> httpx.Response:
        transport_failure: str | None = None
        try:
            response = self._http_client.request(
                method,
                url,
                timeout=self._config.request_timeout_seconds,
                **kwargs,
            )
        except httpx.TimeoutException:
            transport_failure = "timeout"
        except httpx.TransportError:
            transport_failure = "network"
        if transport_failure == "timeout":
            raise CustomerIdentityProviderTimeout(
                f"CIAM {operation} timed out"
            )
        if transport_failure == "network":
            raise CustomerIdentityProviderNetworkError(
                f"CIAM {operation} network request failed"
            )
        if response.status_code == 429:
            raise CustomerIdentityProviderRateLimited(
                f"CIAM {operation} was rate limited"
            )
        if response.status_code >= 500:
            raise CustomerIdentityProviderServiceUnavailable(
                f"CIAM {operation} is unavailable"
            )
        if response.status_code not in accepted_statuses:
            raise CustomerIdentityProviderError(
                f"CIAM {operation} request was rejected"
            )
        return response


def _response_json_object(
    response: httpx.Response,
    *,
    operation: str,
) -> dict[str, Any]:
    invalid_json = False
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        invalid_json = True
        payload = None
    if invalid_json:
        raise CustomerIdentityProviderError(
            f"CIAM {operation} response is not valid"
        )
    if not isinstance(payload, dict):
        raise CustomerIdentityProviderError(
            f"CIAM {operation} response is not valid"
        )
    return payload


def _invalid_id_token() -> CustomerIdentityProviderError:
    return CustomerIdentityProviderError(
        "CIAM ID token is not valid"
    )


def _numeric_date(value: object) -> datetime:
    parsed = _optional_numeric_date(value)
    if parsed is None:
        raise _invalid_id_token()
    return parsed


def _optional_numeric_date(value: object) -> datetime | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise _invalid_id_token()
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise _invalid_id_token() from None


def _string_list(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        item
        for item in value
        if isinstance(item, str)
    }


def _is_missing_status_code(value: object) -> bool:
    return (
        isinstance(value, str)
        and value in MISSING_USER_STATUS_CODES
    )


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.fragment == ""
    )


def _same_origin(left: str, right: str) -> bool:
    left_url = urlparse(left)
    right_url = urlparse(right)
    return (
        left_url.scheme,
        left_url.hostname,
        left_url.port or 443,
    ) == (
        right_url.scheme,
        right_url.hostname,
        right_url.port or 443,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
