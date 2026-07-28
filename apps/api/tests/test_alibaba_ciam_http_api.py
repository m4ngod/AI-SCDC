from datetime import datetime, timezone
import json
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import httpx
import jwt

from ai_company_api.main import create_app
from ai_company_api.services.alibaba_ciam_provider import (
    AlibabaCiamConfig,
    AlibabaCiamCustomerIdentityProvider,
)
from ai_company_api.services.auth_policy import (
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
)
from ai_company_api.services.identity_login import (
    RECENT_AUTHENTICATION_EMAIL_ACR,
)
from ai_company_api.services.user_session_credentials import (
    USER_SESSION_COOKIE,
)


WEB_ORIGIN = "https://console.example.test"
TENANT_BASE_URL = "https://tenant.login.aliyunidaas.com"
ISSUER = (
    f"{TENANT_BASE_URL}/api/bff/v1.2/developer/ciam/oidc/"
    "idaas_ciam_public_cn_http"
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
CLIENT_ID = "ciam-http-client"
CLIENT_SECRET = "ciam-http-client-secret"
NOW = datetime(2026, 7, 28, 3, 0, tzinfo=timezone.utc)
POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {HumanCredentialType.USER_SESSION}
    ),
)


class CiamHttpBoundary:
    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self.tokens_by_code: dict[str, str] = {}
        self.exchange_count = 0
        self.fail_exchange = False

    def issue_code(
        self,
        authorization_url: str,
        *,
        code: str,
        authenticated_at: datetime | None = None,
        authentication_context: str | None = None,
    ) -> None:
        query = parse_qs(urlparse(authorization_url).query)
        timestamp = int(NOW.timestamp())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "ciam-http-user",
            "email": "ciam-http-user@example.test",
            "email_verified": True,
            "nonce": query["nonce"][0],
            "iat": timestamp,
            "nbf": timestamp - 1,
            "exp": timestamp + 300,
        }
        if authenticated_at is not None:
            claims["auth_time"] = int(authenticated_at.timestamp())
        if authentication_context is not None:
            claims["acr"] = authentication_context
        self.tokens_by_code[code] = jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "ciam-http-key"},
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(
                200,
                json={
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
                    "id_token_signing_alg_values_supported": [
                        "RS256"
                    ],
                    "token_endpoint_auth_methods_supported": [
                        "client_secret_post"
                    ],
                },
            )
        if str(request.url) == TOKEN_ENDPOINT:
            self.exchange_count += 1
            if self.fail_exchange:
                return httpx.Response(
                    503,
                    json={
                        "error": "server_error",
                        "error_description": (
                            f"raw {CLIENT_SECRET} provider payload"
                        ),
                    },
                )
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id_token": self.tokens_by_code[payload["code"]],
                    "access_token": "server-only-access-token",
                    "refresh_token": "server-only-refresh-token",
                },
            )
        if str(request.url) == JWKS_URI:
            return httpx.Response(
                200,
                json={
                    "keys": [
                        json.loads(
                            jwt.algorithms.RSAAlgorithm.to_jwk(
                                self.private_key.public_key()
                            )
                        )
                        | {
                            "kid": "ciam-http-key",
                            "use": "sig",
                            "alg": "RS256",
                        }
                    ]
                },
            )
        if str(request.url) == MANAGEMENT_TOKEN_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "access_token": "management-token",
                    "expires_in": 3600,
                },
            )
        if request.url.path.endswith("/management/user"):
            assert request.headers["authorization"] == (
                "Bearer management-token"
            )
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "code": "Operation.Success",
                    "data": {
                        "userId": "ciam-http-user",
                        "enabled": True,
                        "locked": False,
                    },
                },
            )
        raise AssertionError(f"Unexpected CIAM request: {request.url}")


def _provider(
    boundary: CiamHttpBoundary,
) -> AlibabaCiamCustomerIdentityProvider:
    return AlibabaCiamCustomerIdentityProvider(
        AlibabaCiamConfig(
            tenant_base_url=TENANT_BASE_URL,
            management_api_base_url=(
                "https://tenant.api.aliyunidaas.com"
            ),
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(boundary),
        ),
        clock=lambda: NOW,
    )


def _app(database_url: str, boundary: CiamHttpBoundary):
    return create_app(
        database_url=database_url,
        authentication_policy=POLICY,
        customer_identity_provider=_provider(boundary),
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin=WEB_ORIGIN,
        identity_audit_observer_enabled=True,
        identity_clock=lambda: NOW,
    )


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    return {
        "Origin": WEB_ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def test_ciam_adapter_completes_login_reauthentication_and_sign_out(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'ciam-http-flow.db').as_posix()}"
    )
    boundary = CiamHttpBoundary()
    app = _app(database_url, boundary)

    with TestClient(app, base_url=WEB_ORIGIN) as browser:
        login = browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        authorization_query = parse_qs(
            urlparse(authorization_url).query
        )
        boundary.issue_code(
            authorization_url,
            code="first-login-code",
            authenticated_at=NOW,
            authentication_context=(
                RECENT_AUTHENTICATION_EMAIL_ACR
            ),
        )
        callback = browser.get(
            "/auth/callback",
            params={
                "state": authorization_query["state"][0],
                "code": "first-login-code",
            },
            follow_redirects=False,
        )
        first_cookie = browser.cookies.get(USER_SESSION_COOKIE)
        me = browser.get("/me")
        callback_audit = browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": callback.headers[
                    "x-correlation-id"
                ]
            },
        )
        assert callback.status_code == 303, callback_audit.text
        assert callback.headers["location"] == "/console"
        assert first_cookie is not None
        assert me.status_code == 200, me.text
        duplicate_callback = browser.get(
            "/auth/callback",
            params={
                "state": authorization_query["state"][0],
                "code": "first-login-code",
            },
            follow_redirects=False,
        )

        reauthentication = browser.get(
            "/auth/reauthenticate",
            params={"return_to": "/reauthentication/confirm"},
            follow_redirects=False,
        )
        assert reauthentication.status_code == 303, (
            reauthentication.text
        )
        reauthentication_url = reauthentication.headers["location"]
        reauthentication_query = parse_qs(
            urlparse(reauthentication_url).query
        )
        boundary.issue_code(
            reauthentication_url,
            code="recent-authentication-code",
            authenticated_at=NOW,
            authentication_context=(
                RECENT_AUTHENTICATION_EMAIL_ACR
            ),
        )
        reauthenticated = browser.get(
            "/auth/callback",
            params={
                "state": reauthentication_query["state"][0],
                "code": "recent-authentication-code",
            },
            follow_redirects=False,
        )
        rotated_cookie = browser.cookies.get(USER_SESSION_COOKIE)
        credentials = browser.get("/github-credentials")

        signed_out = browser.post(
            "/auth/logout",
            headers=_csrf_headers(browser),
        )
        after_sign_out = browser.get("/me")

    assert login.status_code == 303
    assert authorization_query["code_challenge_method"] == ["S256"]
    assert me.json()["current_account"]["kind"] == "personal"
    assert duplicate_callback.status_code == 303
    assert duplicate_callback.headers["location"] == "/console"
    assert boundary.exchange_count == 2
    assert reauthentication_query["prompt"] == ["login"]
    assert reauthentication_query["max_age"] == ["0"]
    assert reauthentication_query["acr_values"] == [
        RECENT_AUTHENTICATION_EMAIL_ACR
    ]
    assert reauthenticated.status_code == 303
    assert reauthenticated.headers["location"] == (
        "/reauthentication/confirm?reauthentication=confirmed"
    )
    assert rotated_cookie is not None
    assert rotated_cookie != first_cookie
    assert credentials.status_code == 200
    assert credentials.json() == []
    assert signed_out.status_code == 200
    redirect_to = signed_out.json()["redirect_to"]
    assert redirect_to.startswith(END_SESSION_ENDPOINT)
    assert CLIENT_SECRET not in signed_out.text
    assert "server-only" not in signed_out.text
    assert after_sign_out.status_code == 401


def test_ciam_provider_failure_is_safe_at_http_boundary(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'ciam-http-failure.db').as_posix()}"
    )
    boundary = CiamHttpBoundary()
    app = _app(database_url, boundary)

    with TestClient(app, base_url=WEB_ORIGIN) as browser:
        login = browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_query = parse_qs(
            urlparse(login.headers["location"]).query
        )
        boundary.fail_exchange = True
        callback = browser.get(
            "/auth/callback",
            params={
                "state": authorization_query["state"][0],
                "code": "provider-failure-code",
            },
            follow_redirects=False,
        )
        audit = browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": callback.headers[
                    "x-correlation-id"
                ]
            },
        )

    assert callback.status_code == 503
    assert callback.json()["error"] == (
        "identity_provider_unavailable"
    )
    assert USER_SESSION_COOKIE not in callback.headers.get(
        "set-cookie",
        "",
    )
    serialized = callback.text + audit.text
    assert CLIENT_SECRET not in serialized
    assert "raw" not in serialized
    assert [
        (event["event_type"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "identity_provider_unavailable",
            "token_exchange_unavailable",
        )
    ]
