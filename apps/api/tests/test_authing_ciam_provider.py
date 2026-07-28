from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import httpx
import pytest

from ai_company_api.services.authing_ciam_provider import (
    AuthingCiamConfig,
    AuthingCustomerIdentityProvider,
)
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProviderError,
    CustomerIdentityProviderNetworkError,
    CustomerIdentityProviderRateLimited,
    CustomerIdentityProviderServiceUnavailable,
    CustomerIdentityProviderTimeout,
    OidcAuthorizationRequest,
)


APP_HOST = "https://ai-scdc-test.authing.cn"
ISSUER = f"{APP_HOST}/oidc"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = f"{APP_HOST}/oidc/auth"
TOKEN_ENDPOINT = f"{APP_HOST}/oidc/token"
JWKS_URI = f"{ISSUER}/.well-known/jwks.json"
END_SESSION_ENDPOINT = f"{APP_HOST}/oidc/session/end"
CLIENT_ID = "authing-app-id"
APP_SECRET = "authing-app-secret-must-not-leak"
USER_POOL_ID = "222222222222222222222222"
USER_POOL_SECRET = "authing-user-pool-secret-must-not-leak"
MANAGEMENT_API_BASE_URL = "https://api.authing.cn"
MANAGEMENT_TOKEN_ENDPOINT = (
    f"{MANAGEMENT_API_BASE_URL}/api/v3/get-management-token"
)
MANAGEMENT_USER_ENDPOINT = (
    f"{MANAGEMENT_API_BASE_URL}/api/v3/get-user"
)
NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


def _config() -> AuthingCiamConfig:
    return AuthingCiamConfig(
        app_host=APP_HOST,
        issuer=ISSUER,
        client_id=CLIENT_ID,
        app_secret=APP_SECRET,
        user_pool_id=USER_POOL_ID,
        user_pool_secret=USER_POOL_SECRET,
        request_timeout_seconds=2.0,
        clock_skew_seconds=60,
    )


def test_authing_rejects_non_user_pool_id_before_network() -> None:
    misplaced_secret = "not-a-24-character-user-pool-id"

    with pytest.raises(
        ValueError,
        match="Authing CIAM configuration is not valid",
    ) as exc_info:
        AuthingCiamConfig(
            app_host=APP_HOST,
            issuer=ISSUER,
            client_id=CLIENT_ID,
            app_secret=APP_SECRET,
            user_pool_id=misplaced_secret,
            user_pool_secret=USER_POOL_SECRET,
        )

    assert misplaced_secret not in str(exc_info.value)
    assert USER_POOL_SECRET not in str(exc_info.value)


def _discovery(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHORIZATION_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
        "jwks_uri": JWKS_URI,
        "end_session_endpoint": END_SESSION_ENDPOINT,
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
        ],
        "response_types_supported": ["code"],
        "scopes_supported": [
            "openid",
            "email",
            "profile",
            "offline_access",
        ],
        "code_challenge_methods_supported": ["plain", "S256"],
        "id_token_signing_alg_values_supported": ["HS256", "RS256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
            "none",
        ],
    }
    payload.update(overrides)
    return payload


def _provider(handler) -> AuthingCustomerIdentityProvider:
    return AuthingCustomerIdentityProvider(
        _config(),
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
        ),
        clock=lambda: NOW,
    )


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
        "sub": "authing-user-id",
        "email": "customer@example.test",
        "email_verified": True,
        "nonce": "expected-nonce",
        "iat": issued_at,
        "nbf": issued_at - 1,
        "exp": issued_at + 300,
        "auth_time": issued_at,
        "acr": "email-passcode",
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


def test_authing_discovery_drives_pkce_email_authorization() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == DISCOVERY_URL
        return httpx.Response(200, json=_discovery())

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
            authentication_requested_at=NOW,
        )
    )

    assert discovery.issuer == ISSUER
    assert discovery.authorization_endpoint == AUTHORIZATION_ENDPOINT
    assert discovery.token_endpoint == TOKEN_ENDPOINT
    assert discovery.end_session_endpoint == END_SESSION_ENDPOINT
    assert len(requests) == 1
    assert parse_qs(urlparse(authorization_url).query) == {
        "response_type": ["code"],
        "response_mode": ["query"],
        "client_id": [CLIENT_ID],
        "redirect_uri": [
            "https://console.example.test/auth/callback"
        ],
        "scope": ["openid email profile"],
        "state": ["state-value"],
        "nonce": ["nonce-value"],
        "code_challenge": ["pkce-challenge"],
        "code_challenge_method": ["S256"],
        "prompt": ["login"],
        "max_age": ["0"],
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "https://other.example.test/oidc"},
        {"authorization_endpoint": "http://ai-scdc-test.authing.cn/auth"},
        {"jwks_uri": "https://evil.example.test/jwks"},
        {"grant_types_supported": ["refresh_token"]},
        {"response_types_supported": ["token"]},
        {"code_challenge_methods_supported": ["plain"]},
        {"id_token_signing_alg_values_supported": ["HS256"]},
        {"token_endpoint_auth_methods_supported": ["none"]},
    ],
)
def test_authing_rejects_untrusted_or_incompatible_discovery(
    overrides: dict[str, object],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_discovery(**overrides),
        )

    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM discovery metadata is not valid",
    ) as exc_info:
        _provider(handler).discover()

    message = str(exc_info.value)
    assert APP_SECRET not in message
    assert USER_POOL_SECRET not in message
    assert "evil.example.test" not in message


def test_authing_code_exchange_and_rs256_identity_validation() -> None:
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    token = _id_token(private_key, kid="key-1")
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery())
        if str(request.url) == TOKEN_ENDPOINT:
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "id_token": token,
                    "access_token": "authing-access-token",
                    "refresh_token": "must-be-ignored",
                },
            )
        if str(request.url) == JWKS_URI:
            return httpx.Response(
                200,
                json={"keys": [_jwk(private_key, kid="key-1")]},
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

    assert captured_request is not None
    assert captured_request.headers["authorization"].startswith(
        "Basic "
    )
    assert APP_SECRET not in captured_request.content.decode("utf-8")
    assert parse_qs(captured_request.content.decode("utf-8")) == {
        "grant_type": ["authorization_code"],
        "code": ["authorization-code"],
        "redirect_uri": [
            "https://console.example.test/auth/callback"
        ],
        "code_verifier": ["pkce-verifier"],
    }
    assert identity.issuer == ISSUER
    assert identity.subject == "authing-user-id"
    assert identity.email == "customer@example.test"
    assert identity.nonce == "expected-nonce"
    assert identity.authenticated_at == NOW
    assert identity.authentication_context == "email-passcode"
    assert not hasattr(token_response, "refresh_token")


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://other.example.test/oidc"},
        {"aud": "other-client"},
        {"aud": [CLIENT_ID, "other-client"]},
        {"nonce": ""},
        {"exp": int((NOW - timedelta(minutes=5)).timestamp())},
        {"nbf": int((NOW + timedelta(minutes=5)).timestamp())},
        {"iat": int((NOW + timedelta(minutes=5)).timestamp())},
        {"email_verified": False},
    ],
)
def test_authing_rejects_invalid_identity_and_timing_claims(
    claims: dict[str, object],
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
            return httpx.Response(200, json=_discovery())
        if str(request.url) == JWKS_URI:
            return httpx.Response(
                200,
                json={
                    "keys": [
                        _jwk(private_key, kid="key-invalid")
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM ID token is not valid",
    ):
        _provider(handler).validate_id_token(
            token,
            expected_audience=CLIENT_ID,
        )


def test_authing_refreshes_jwks_when_signing_key_rotates() -> None:
    old_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    new_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    token = _id_token(new_key, kid="rotated-key")
    jwks_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_requests
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery())
        if str(request.url) == JWKS_URI:
            jwks_requests += 1
            key = old_key if jwks_requests == 1 else new_key
            kid = "old-key" if jwks_requests == 1 else "rotated-key"
            return httpx.Response(
                200,
                json={"keys": [_jwk(key, kid=kid)]},
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    identity = _provider(handler).validate_id_token(
        token,
        expected_audience=CLIENT_ID,
    )

    assert identity.subject == "authing-user-id"
    assert jwks_requests == 2


def test_authing_refreshes_same_kid_after_signature_rotation() -> None:
    previous_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    rotated_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    token = _id_token(rotated_key, kid="stable-key-id")
    jwks_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_requests
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery())
        if str(request.url) == JWKS_URI:
            jwks_requests += 1
            key = previous_key if jwks_requests == 1 else rotated_key
            return httpx.Response(
                200,
                json={
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

    assert identity.subject == "authing-user-id"
    assert jwks_requests == 2


def test_authing_rejects_removed_key_after_jwks_cache_expiry() -> None:
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
    jwks_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_requests
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery())
        if str(request.url) == JWKS_URI:
            jwks_requests += 1
            if jwks_requests == 1:
                keys = [_jwk(removed_key, kid="removed-key")]
            else:
                keys = [
                    _jwk(
                        replacement_key,
                        kid="replacement-key",
                    )
                ]
            return httpx.Response(200, json={"keys": keys})
        raise AssertionError(f"Unexpected request: {request.url}")

    provider = AuthingCustomerIdentityProvider(
        AuthingCiamConfig(
            app_host=APP_HOST,
            issuer=ISSUER,
            client_id=CLIENT_ID,
            app_secret=APP_SECRET,
            user_pool_id=USER_POOL_ID,
            user_pool_secret=USER_POOL_SECRET,
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
    ).subject == "authing-user-id"

    current_time[0] = NOW + timedelta(seconds=61)
    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM ID token is not valid",
    ):
        provider.validate_id_token(
            token,
            expected_audience=CLIENT_ID,
        )

    assert jwks_requests >= 2


def test_authing_transport_failure_drops_secret_request_context(
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery())
        if str(request.url) == TOKEN_ENDPOINT:
            raise httpx.ConnectError(
                f"network failure containing {APP_SECRET}",
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

    assert APP_SECRET not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.parametrize(
    ("authing_status", "provider_status"),
    [
        ("Activated", "active"),
        ("Suspended", "locked"),
        ("Deactivated", "disabled"),
        ("Resigned", "disabled"),
        ("Archived", "disabled"),
    ],
)
def test_authing_management_status_maps_account_lifecycle(
    authing_status: str,
    provider_status: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            assert request.method == "POST"
            assert json.loads(request.content) == {
                "accessKeyId": USER_POOL_ID,
                "accessKeySecret": USER_POOL_SECRET,
            }
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": "management-access-token",
                        "expires_in": 3600,
                    },
                },
            )
        if request.url.path == "/api/v3/get-user":
            assert request.method == "GET"
            assert request.headers["authorization"] == (
                "Bearer management-access-token"
            )
            assert request.headers["x-authing-userpool-id"] == (
                USER_POOL_ID
            )
            assert dict(request.url.params) == {
                "userId": "authing-user-id",
                "userIdType": "user_id",
            }
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "userId": "authing-user-id",
                        "status": authing_status,
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    status = _provider(handler).identity_status(
        issuer=ISSUER,
        subject="authing-user-id",
    )

    assert status == provider_status
    assert len(requests) == 2


def test_authing_management_status_maps_missing_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": "management-access-token",
                        "expires_in": 3600,
                    },
                },
            )
        if request.url.path == "/api/v3/get-user":
            return httpx.Response(
                200,
                json={
                    "statusCode": 404,
                    "apiCode": 2004,
                    "message": "user not found",
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    assert _provider(handler).identity_status(
        issuer=ISSUER,
        subject="deleted-authing-user",
    ) == "missing"


def test_authing_management_route_404_is_not_a_missing_identity(
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": "management-access-token",
                        "expires_in": 3600,
                    },
                },
            )
        if request.url.path == "/api/v3/get-user":
            return httpx.Response(404)
        raise AssertionError(f"Unexpected request: {request.url}")

    with pytest.raises(
        CustomerIdentityProviderError,
        match="identity status response is not valid",
    ):
        _provider(handler).identity_status(
            issuer=ISSUER,
            subject="deleted-authing-user",
        )


def test_authing_management_status_rejects_mismatched_identity(
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": "management-access-token",
                        "expires_in": 3600,
                    },
                },
            )
        if request.url.path == "/api/v3/get-user":
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "userId": "different-authing-user",
                        "status": "Activated",
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    with pytest.raises(
        CustomerIdentityProviderError,
        match="identity status response is not valid",
    ):
        _provider(handler).identity_status(
            issuer=ISSUER,
            subject="authing-user-id",
        )


def test_authing_management_status_refreshes_rejected_cached_token(
) -> None:
    management_tokens = iter(("stale-token", "fresh-token"))
    token_requests = 0
    user_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests, user_requests
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            token_requests += 1
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": next(management_tokens),
                        "expires_in": 3600,
                    },
                },
            )
        if request.url.path == "/api/v3/get-user":
            user_requests += 1
            if request.headers["authorization"] == (
                "Bearer stale-token"
            ):
                return httpx.Response(
                    200,
                    json={
                        "statusCode": 401,
                        "apiCode": 1001,
                        "message": "management token expired",
                    },
                )
            assert request.headers["authorization"] == (
                "Bearer fresh-token"
            )
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "userId": "authing-user-id",
                        "status": "Activated",
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    assert _provider(handler).identity_status(
        issuer=ISSUER,
        subject="authing-user-id",
    ) == "active"
    assert token_requests == 2
    assert user_requests == 2


def test_authing_management_status_refreshes_bare_http_401() -> None:
    management_tokens = iter(("stale-token", "fresh-token"))

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": next(management_tokens),
                        "expires_in": 3600,
                    },
                },
            )
        if request.url.path == "/api/v3/get-user":
            if request.headers["authorization"] == (
                "Bearer stale-token"
            ):
                return httpx.Response(401)
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "userId": "authing-user-id",
                        "status": "Activated",
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    assert _provider(handler).identity_status(
        issuer=ISSUER,
        subject="authing-user-id",
    ) == "active"


@pytest.mark.parametrize(
    ("json_status", "expected_error"),
    [
        (429, CustomerIdentityProviderRateLimited),
        (503, CustomerIdentityProviderServiceUnavailable),
    ],
)
def test_authing_management_status_classifies_json_unavailability(
    json_status: int,
    expected_error: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": "management-token",
                        "expires_in": 3600,
                    },
                },
            )
        if request.url.path == "/api/v3/get-user":
            return httpx.Response(
                200,
                json={
                    "statusCode": json_status,
                    "message": APP_SECRET,
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    with pytest.raises(expected_error) as exc_info:
        _provider(handler).identity_status(
            issuer=ISSUER,
            subject="authing-user-id",
        )

    assert APP_SECRET not in str(exc_info.value)
    assert USER_POOL_SECRET not in str(exc_info.value)


@pytest.mark.parametrize(
    ("json_status", "expected_error"),
    [
        (429, CustomerIdentityProviderRateLimited),
        (503, CustomerIdentityProviderServiceUnavailable),
    ],
)
def test_authing_management_token_classifies_json_unavailability(
    json_status: int,
    expected_error: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == MANAGEMENT_TOKEN_ENDPOINT
        return httpx.Response(
            200,
            json={
                "statusCode": json_status,
                "message": USER_POOL_SECRET,
            },
        )

    with pytest.raises(expected_error) as exc_info:
        _provider(handler).identity_status(
            issuer=ISSUER,
            subject="authing-user-id",
        )

    assert APP_SECRET not in str(exc_info.value)
    assert USER_POOL_SECRET not in str(exc_info.value)


@pytest.mark.parametrize(
    ("failure", "expected_error"),
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
def test_authing_provider_distinguishes_unavailable_results(
    failure: Exception | int,
    expected_error: type[Exception],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(failure, Exception):
            raise failure
        return httpx.Response(failure, json={"secret": APP_SECRET})

    with pytest.raises(expected_error) as exc_info:
        _provider(handler).discover()

    message = str(exc_info.value)
    assert APP_SECRET not in message
    assert USER_POOL_SECRET not in message


def test_authing_availability_check_bypasses_cached_discovery() -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(200, json=_discovery())
        return httpx.Response(503)

    provider = _provider(handler)
    provider.discover()

    with pytest.raises(CustomerIdentityProviderServiceUnavailable):
        provider.check_availability()

    assert request_count == 2


def test_authing_availability_check_validates_management_and_oidc_credentials() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery())
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": "management-access-token",
                        "expires_in": 3600,
                    },
                },
            )
        assert str(request.url) == f"{ISSUER}/token/introspection"
        assert parse_qs(request.content.decode("utf-8")) == {
            "token": ["ai-scdc-readiness-invalid-token"],
            "token_type_hint": ["access_token"],
            "client_id": [CLIENT_ID],
            "client_secret": [APP_SECRET],
        }
        return httpx.Response(
            200,
            json={"active": False},
        )

    _provider(handler).check_availability()

    assert requested_urls == [
        DISCOVERY_URL,
        f"{MANAGEMENT_API_BASE_URL}/api/v3/get-management-token",
        f"{ISSUER}/token/introspection",
    ]


def test_authing_availability_rejects_invalid_app_secret_without_leaking_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery())
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "access_token": "management-access-token",
                        "expires_in": 3600,
                    },
                },
            )
        return httpx.Response(401, json={"secret": APP_SECRET})

    with pytest.raises(CustomerIdentityProviderError) as exc_info:
        _provider(handler).check_availability()

    assert APP_SECRET not in str(exc_info.value)
    assert USER_POOL_SECRET not in str(exc_info.value)


def test_authing_end_session_uses_allowlisted_return_without_tokens(
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DISCOVERY_URL
        return httpx.Response(200, json=_discovery())

    logout_url = _provider(handler).end_session_url(
        post_logout_redirect_uri="https://console.example.test/",
    )

    assert logout_url is not None
    parsed = urlparse(logout_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        END_SESSION_ENDPOINT
    )
    assert parse_qs(parsed.query) == {
        "client_id": [CLIENT_ID],
        "post_logout_redirect_uri": [
            "https://console.example.test/"
        ],
    }
    assert "id_token_hint" not in parsed.query


def test_authing_end_session_uses_an_opaque_exchange_bound_logout_hint(
) -> None:
    id_token = "signed.authing.id-token"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery())
        if str(request.url) == TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "id_token": id_token,
                    "access_token": "server-only-access-token",
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    provider = _provider(handler)
    token_response = provider.exchange_code(
        code="authorization-code",
        redirect_uri="https://console.example.test/auth/callback",
        code_verifier="pkce-verifier",
    )

    assert token_response.logout_hint is not None
    assert token_response.logout_hint != id_token
    assert id_token not in repr(token_response)

    logout_url = provider.end_session_url(
        post_logout_redirect_uri="https://console.example.test/",
        logout_hint=token_response.logout_hint,
    )

    assert logout_url is not None
    assert parse_qs(urlparse(logout_url).query) == {
        "client_id": [CLIENT_ID],
        "id_token_hint": [id_token],
        "post_logout_redirect_uri": [
            "https://console.example.test/"
        ],
    }


def test_authing_end_session_rejects_a_tampered_logout_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DISCOVERY_URL
        return httpx.Response(200, json=_discovery())

    with pytest.raises(
        CustomerIdentityProviderError,
        match="CIAM logout hint is not valid",
    ) as exc_info:
        _provider(handler).end_session_url(
            post_logout_redirect_uri=(
                "https://console.example.test/"
            ),
            logout_hint="tampered-opaque-logout-hint",
        )

    rendered = str(exc_info.value)
    assert APP_SECRET not in rendered
    assert USER_POOL_SECRET not in rendered


def test_authing_configuration_repr_redacts_both_secrets() -> None:
    rendered = repr(_config())

    assert APP_SECRET not in rendered
    assert USER_POOL_SECRET not in rendered
    assert CLIENT_ID in rendered
    assert USER_POOL_ID in rendered
