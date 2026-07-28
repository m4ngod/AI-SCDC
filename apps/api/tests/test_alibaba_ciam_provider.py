from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import httpx
import pytest

from ai_company_api.services.alibaba_ciam_provider import (
    AlibabaCiamConfig,
    AlibabaCiamCustomerIdentityProvider,
)
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProviderError,
    CustomerIdentityProviderNetworkError,
    CustomerIdentityProviderRateLimited,
    CustomerIdentityProviderServiceUnavailable,
    CustomerIdentityProviderTimeout,
    OidcAuthorizationRequest,
)


TENANT_BASE_URL = "https://tenant.login.aliyunidaas.com"
MANAGEMENT_API_BASE_URL = "https://tenant.api.aliyunidaas.com"
ISSUER = (
    f"{TENANT_BASE_URL}/api/bff/v1.2/developer/ciam/oidc/"
    "idaas_ciam_public_cn_test"
)
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = (
    f"{TENANT_BASE_URL}/api/bff/v1.2/developer/ciam/oauth/authorize"
)
TOKEN_ENDPOINT = (
    f"{TENANT_BASE_URL}/api/bff/v1.2/developer/ciam/oidc/token"
)
JWKS_URI = f"{ISSUER}/.well-known/jwks.json"
END_SESSION_ENDPOINT = (
    f"{TENANT_BASE_URL}/api/bff/v1.2/developer/ciam/user/logout"
)
MANAGEMENT_TOKEN_ENDPOINT = (
    f"{TENANT_BASE_URL}/api/bff/v1.2/developer/ciam/oauth/token"
)
MANAGEMENT_USER_ENDPOINT = (
    f"{MANAGEMENT_API_BASE_URL}/"
    "api/bff/v1.2/developer/ciam/management/user"
)
CLIENT_ID = "ciam-client-id"
CLIENT_SECRET = "ciam-client-secret-must-not-leak"
NOW = datetime(2026, 7, 28, 2, 0, tzinfo=timezone.utc)


def _config() -> AlibabaCiamConfig:
    return AlibabaCiamConfig(
        tenant_base_url=TENANT_BASE_URL,
        management_api_base_url=MANAGEMENT_API_BASE_URL,
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        request_timeout_seconds=2.0,
        clock_skew_seconds=60,
    )


def _discovery(**overrides):
    payload = {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHORIZATION_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
        "jwks_uri": JWKS_URI,
        "end_session_endpoint": END_SESSION_ENDPOINT,
        "grant_types_supported": [
            "authorization_code",
            "client_credentials",
        ],
        "response_types_supported": ["code"],
        "scopes_supported": [
            "USER_API",
            "MANAGEMENT_APPLICATION_API",
            "openid",
            "email",
        ],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post"
        ],
    }
    payload.update(overrides)
    return payload


def _provider(handler) -> AlibabaCiamCustomerIdentityProvider:
    return AlibabaCiamCustomerIdentityProvider(
        _config(),
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
        ),
        clock=lambda: NOW,
    )


def _json_response(
    status_code: int,
    payload: object,
) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _base64url(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _jwk(private_key, *, kid: str) -> dict[str, str]:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _base64url(
            numbers.n.to_bytes(
                (numbers.n.bit_length() + 7) // 8,
                "big",
            )
        ),
        "e": _base64url(
            numbers.e.to_bytes(
                (numbers.e.bit_length() + 7) // 8,
                "big",
            )
        ),
    }


def _id_token(
    private_key,
    *,
    kid: str,
    claims: dict[str, object] | None = None,
) -> str:
    issued_at = int(NOW.timestamp())
    payload: dict[str, object] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "ciam-user-uuid",
        "email": "customer@example.test",
        "email_verified": True,
        "nonce": "expected-nonce",
        "iat": issued_at,
        "nbf": issued_at - 1,
        "exp": issued_at + 300,
        "auth_time": issued_at,
        "acr": "urn:ai-scdc:email-verification-code",
    }
    if claims:
        payload.update(claims)
    encoded_header = _base64url(
        json.dumps(
            {"alg": "RS256", "kid": kid, "typ": "JWT"},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    encoded_payload = _base64url(
        json.dumps(
            payload,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{encoded_header}.{encoded_payload}".encode(
        "ascii"
    )
    signature = private_key.sign(
        signing_input,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{encoded_header}.{encoded_payload}.{_base64url(signature)}"


def test_discovery_and_authorization_use_validated_ciam_oidc_metadata(
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == DISCOVERY_URL:
            return _json_response(200, _discovery())
        raise AssertionError(f"Unexpected request: {request.url}")

    provider = _provider(handler)
    discovery = provider.discover()
    authorization_url = provider.authorization_url(
        OidcAuthorizationRequest(
            client_id=CLIENT_ID,
            redirect_uri="https://console.example.test/auth/callback",
            state="state-value",
            nonce="nonce-value",
            code_challenge="pkce-challenge",
            prompt="login",
            max_age_seconds=0,
            acr_values="urn:ai-scdc:email-verification-code",
            authentication_requested_at=NOW,
        )
    )

    assert discovery.issuer == ISSUER
    assert discovery.authorization_endpoint == AUTHORIZATION_ENDPOINT
    assert discovery.token_endpoint == TOKEN_ENDPOINT
    assert discovery.end_session_endpoint == END_SESSION_ENDPOINT
    assert len(requests) == 1
    query = parse_qs(urlparse(authorization_url).query)
    assert query == {
        "response_type": ["code"],
        "client_id": [CLIENT_ID],
        "redirect_uri": [
            "https://console.example.test/auth/callback"
        ],
        "scope": ["USER_API openid email"],
        "state": ["state-value"],
        "nonce": ["nonce-value"],
        "code_challenge": ["pkce-challenge"],
        "code_challenge_method": ["S256"],
        "prompt": ["login"],
        "max_age": ["0"],
        "acr_values": [
            "urn:ai-scdc:email-verification-code"
        ],
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "https://other.example.test"},
        {"authorization_endpoint": "http://tenant.example.test/auth"},
        {"jwks_uri": "https://evil.example.test/jwks"},
        {"grant_types_supported": ["client_credentials"]},
        {"response_types_supported": ["token"]},
        {"id_token_signing_alg_values_supported": ["HS256"]},
        {"token_endpoint_auth_methods_supported": ["client_secret_basic"]},
    ],
)
def test_discovery_rejects_untrusted_or_incompatible_metadata(
    overrides,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response(200, _discovery(**overrides))

    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM discovery metadata is not valid",
    ) as exc_info:
        _provider(handler).discover()

    assert CLIENT_SECRET not in str(exc_info.value)
    assert "evil.example.test" not in str(exc_info.value)


def test_code_exchange_and_id_token_validation_keep_tokens_server_side(
) -> None:
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    token = _id_token(private_key, kid="key-1")
    captured_token_request: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return _json_response(200, _discovery())
        if str(request.url) == TOKEN_ENDPOINT:
            captured_token_request.update(json.loads(request.content))
            return _json_response(
                200,
                {
                    "id_token": token,
                    "access_token": "ciam-access-token",
                    "refresh_token": "must-be-ignored",
                },
            )
        if str(request.url) == JWKS_URI:
            return _json_response(
                200,
                {"keys": [_jwk(private_key, kid="key-1")]},
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    provider = _provider(handler)
    token_response = provider.exchange_code(
        code="authorization-code",
        redirect_uri="https://console.example.test/auth/callback",
        code_verifier="pkce-verifier",
    )
    identity = provider.validate_id_token(
        token_response.id_token,
        expected_audience=CLIENT_ID,
    )

    assert captured_token_request == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "scope": "USER_API openid email",
        "code": "authorization-code",
        "redirect_uri": (
            "https://console.example.test/auth/callback"
        ),
        "code_verifier": "pkce-verifier",
    }
    assert identity.issuer == ISSUER
    assert identity.subject == "ciam-user-uuid"
    assert identity.email == "customer@example.test"
    assert identity.nonce == "expected-nonce"
    assert identity.authenticated_at == NOW
    assert identity.authentication_context == (
        "urn:ai-scdc:email-verification-code"
    )
    assert not hasattr(token_response, "refresh_token")


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://other.example.test"},
        {"aud": "other-client"},
        {"aud": [CLIENT_ID, "other-client"]},
        {"nonce": ""},
        {"exp": int((NOW - timedelta(minutes=5)).timestamp())},
        {"nbf": int((NOW + timedelta(minutes=5)).timestamp())},
        {"iat": int((NOW + timedelta(minutes=5)).timestamp())},
        {"email_verified": False},
    ],
)
def test_id_token_rejects_invalid_identity_and_timing_claims(
    claims,
) -> None:
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    token = _id_token(
        private_key,
        kid="key-invalid",
        claims=claims,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return _json_response(200, _discovery())
        if str(request.url) == JWKS_URI:
            return _json_response(
                200,
                {
                    "keys": [
                        _jwk(private_key, kid="key-invalid")
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM ID token is not valid",
    ) as exc_info:
        _provider(handler).validate_id_token(
            token,
            expected_audience=CLIENT_ID,
        )

    assert token not in str(exc_info.value)


def test_unknown_signing_key_refreshes_jwks_once_for_rotation() -> None:
    previous_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    rotated_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    token = _id_token(rotated_key, kid="rotated-key")
    jwks_queries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_queries
        if str(request.url) == DISCOVERY_URL:
            return _json_response(200, _discovery())
        if str(request.url) == JWKS_URI:
            jwks_queries += 1
            key = previous_key if jwks_queries == 1 else rotated_key
            kid = "previous-key" if jwks_queries == 1 else "rotated-key"
            return _json_response(
                200,
                {"keys": [_jwk(key, kid=kid)]},
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    identity = _provider(handler).validate_id_token(
        token,
        expected_audience=CLIENT_ID,
    )

    assert identity.subject == "ciam-user-uuid"
    assert jwks_queries == 2


def test_same_signing_key_id_refreshes_after_signature_rotation() -> None:
    previous_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    rotated_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    token = _id_token(rotated_key, kid="stable-key-id")
    jwks_queries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_queries
        if str(request.url) == DISCOVERY_URL:
            return _json_response(200, _discovery())
        if str(request.url) == JWKS_URI:
            jwks_queries += 1
            key = previous_key if jwks_queries == 1 else rotated_key
            return _json_response(
                200,
                {
                    "keys": [
                        _jwk(key, kid="stable-key-id")
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    identity = _provider(handler).validate_id_token(
        token,
        expected_audience=CLIENT_ID,
    )

    assert identity.subject == "ciam-user-uuid"
    assert jwks_queries == 2


def test_removed_signing_key_is_rejected_after_jwks_cache_expiry() -> None:
    removed_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    replacement_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    token = _id_token(removed_key, kid="removed-key")
    current_time = [NOW]
    jwks_queries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_queries
        if str(request.url) == DISCOVERY_URL:
            return _json_response(200, _discovery())
        if str(request.url) == JWKS_URI:
            jwks_queries += 1
            if jwks_queries == 1:
                keys = [_jwk(removed_key, kid="removed-key")]
            else:
                keys = [_jwk(replacement_key, kid="replacement-key")]
            return _json_response(200, {"keys": keys})
        raise AssertionError(f"Unexpected request: {request.url}")

    provider = AlibabaCiamCustomerIdentityProvider(
        AlibabaCiamConfig(
            tenant_base_url=TENANT_BASE_URL,
            management_api_base_url=MANAGEMENT_API_BASE_URL,
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            jwks_cache_seconds=60,
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
        ),
        clock=lambda: current_time[0],
    )

    assert provider.validate_id_token(
        token,
        expected_audience=CLIENT_ID,
    ).subject == "ciam-user-uuid"

    current_time[0] = NOW + timedelta(seconds=61)
    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM ID token is not valid",
    ):
        provider.validate_id_token(
            token,
            expected_audience=CLIENT_ID,
        )

    assert jwks_queries >= 2


@pytest.mark.parametrize(
    ("failure", "expected_exception"),
    [
        (
            httpx.ReadTimeout("timeout"),
            CustomerIdentityProviderTimeout,
        ),
        (
            httpx.ConnectError("network"),
            CustomerIdentityProviderNetworkError,
        ),
        (429, CustomerIdentityProviderRateLimited),
        (503, CustomerIdentityProviderServiceUnavailable),
    ],
)
def test_provider_failures_are_safely_classified(
    failure,
    expected_exception,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(failure, Exception):
            raise failure
        return _json_response(
            failure,
            {
                "error": "provider_error",
                "error_description": CLIENT_SECRET,
                "raw_provider_payload": "must-not-leak",
            },
        )

    with pytest.raises(expected_exception) as exc_info:
        _provider(handler).discover()

    serialized = str(exc_info.value)
    assert CLIENT_SECRET not in serialized
    assert "raw_provider_payload" not in serialized


def test_token_exchange_transport_failure_drops_secret_request_context(
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return _json_response(200, _discovery())
        if str(request.url) == TOKEN_ENDPOINT:
            raise httpx.ConnectError(
                f"network failure containing {CLIENT_SECRET}",
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    with pytest.raises(
        CustomerIdentityProviderNetworkError,
    ) as exc_info:
        _provider(handler).exchange_code(
            code="authorization-code",
            redirect_uri=(
                "https://console.example.test/auth/callback"
            ),
            code_verifier="pkce-verifier",
        )

    assert CLIENT_SECRET not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.parametrize(
    ("user_payload", "expected_status"),
    [
        ({"enabled": True, "locked": False}, "active"),
        ({"enabled": True, "locked": True}, "locked"),
        ({"enabled": False, "locked": False}, "disabled"),
        (None, "missing"),
    ],
)
def test_management_status_maps_ciam_lifecycle_without_token_leaks(
    user_payload,
    expected_status,
) -> None:
    management_token_requests = 0
    observed_authorization_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal management_token_requests
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            management_token_requests += 1
            assert json.loads(request.content) == {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "client_credentials",
                "scope": "MANAGEMENT_APPLICATION_API",
            }
            return _json_response(
                200,
                {
                    "access_token": "management-access-token",
                    "expires_in": 3600,
                    "token_type": "bearer",
                },
            )
        if request.url.path.endswith("/management/user"):
            assert str(request.url).startswith(MANAGEMENT_API_BASE_URL)
            observed_authorization_headers.append(
                request.headers["authorization"]
            )
            assert request.url.params["userUuid"] == (
                "ciam-user-uuid"
            )
            if user_payload is None:
                return _json_response(
                    404,
                    {"code": "Operation.Failure.User.NotFound"},
                )
            return _json_response(
                200,
                {
                    "success": True,
                    "code": "Operation.Success",
                    "data": {
                        "userId": "ciam-user-uuid",
                        **user_payload,
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    provider = _provider(handler)
    first = provider.identity_status(
        issuer=ISSUER,
        subject="ciam-user-uuid",
    )
    second = provider.identity_status(
        issuer=ISSUER,
        subject="ciam-user-uuid",
    )

    assert first == expected_status
    assert second == expected_status
    assert management_token_requests == 1
    assert observed_authorization_headers == [
        "Bearer management-access-token",
        "Bearer management-access-token",
    ]


def test_management_route_not_found_is_not_a_missing_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return _json_response(
                200,
                {
                    "access_token": "management-access-token",
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("/management/user"):
            return _json_response(
                404,
                {
                    "code": "NotFound",
                    "message": "The requested route was not found",
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM identity status response is not valid",
    ):
        _provider(handler).identity_status(
            issuer=ISSUER,
            subject="ciam-user-uuid",
        )


def test_management_status_refreshes_rejected_access_token_once() -> None:
    management_token_requests = 0
    user_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal management_token_requests, user_requests
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            management_token_requests += 1
            return _json_response(
                200,
                {
                    "access_token": (
                        f"management-token-{management_token_requests}"
                    ),
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("/management/user"):
            user_requests += 1
            if user_requests == 1:
                return _json_response(
                    401,
                    {"error": "invalid_token"},
                )
            assert request.headers["authorization"] == (
                "Bearer management-token-2"
            )
            return _json_response(
                200,
                {
                    "success": True,
                    "code": "Operation.Success",
                    "data": {
                        "userId": "ciam-user-uuid",
                        "enabled": True,
                        "locked": False,
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    status = _provider(handler).identity_status(
        issuer=ISSUER,
        subject="ciam-user-uuid",
    )

    assert status == "active"
    assert management_token_requests == 2
    assert user_requests == 2


def test_management_status_rejects_mismatched_user_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return _json_response(
                200,
                {
                    "access_token": "management-token",
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("/management/user"):
            return _json_response(
                200,
                {
                    "success": True,
                    "code": "Operation.Success",
                    "data": {
                        "userId": "different-user",
                        "enabled": True,
                        "locked": False,
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM identity status response is not valid",
    ):
        _provider(handler).identity_status(
            issuer=ISSUER,
            subject="ciam-user-uuid",
        )


def test_configuration_diagnostics_do_not_expose_client_secret() -> None:
    config = _config()

    assert CLIENT_SECRET not in repr(config)
    assert CLIENT_ID in repr(config)


def test_end_session_uses_discovered_endpoint_and_safe_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DISCOVERY_URL
        return _json_response(200, _discovery())

    url = _provider(handler).end_session_url(
        post_logout_redirect_uri=(
            "https://console.example.test/signed-out"
        )
    )

    assert url is not None
    parsed = urlparse(url)
    assert (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        == END_SESSION_ENDPOINT
    )
    assert parse_qs(parsed.query) == {
        "client_id": [CLIENT_ID],
        "post_logout_redirect_uri": [
            "https://console.example.test/signed-out"
        ],
    }
