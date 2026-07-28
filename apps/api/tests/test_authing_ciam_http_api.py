from datetime import datetime, timezone
import json
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import httpx
import jwt

from ai_company_api.main import create_app
from ai_company_api.services.auth_policy import (
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
)
from ai_company_api.services.authing_ciam_provider import (
    AuthingCiamConfig,
    AuthingCustomerIdentityProvider,
)
from ai_company_api.services.identity_login import (
    RECENT_AUTHENTICATION_EMAIL_ACR,
)
from ai_company_api.services.identity_logout import (
    PROVIDER_LOGOUT_CONTINUATION_SECONDS,
    PROVIDER_LOGOUT_COOKIE,
)
from ai_company_api.services.user_session_credentials import (
    USER_SESSION_COOKIE,
)


WEB_ORIGIN = "https://console.example.test"
APP_HOST = "https://ai-scdc-test.authing.cn"
ISSUER = f"{APP_HOST}/oidc"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = f"{APP_HOST}/oidc/auth"
TOKEN_ENDPOINT = f"{APP_HOST}/oidc/token"
JWKS_URI = f"{ISSUER}/.well-known/jwks.json"
END_SESSION_ENDPOINT = f"{APP_HOST}/oidc/session/end"
MANAGEMENT_API_BASE_URL = "https://api.authing.cn"
MANAGEMENT_TOKEN_ENDPOINT = (
    f"{MANAGEMENT_API_BASE_URL}/api/v3/get-management-token"
)
CLIENT_ID = "authing-http-app-id"
APP_SECRET = "authing-http-app-secret"
USER_POOL_ID = "333333333333333333333333"
USER_POOL_SECRET = "authing-http-user-pool-secret"
NOW = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {HumanCredentialType.USER_SESSION}
    ),
)


class AuthingHttpBoundary:
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
        claims: dict[str, object] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "authing-http-user",
            "email": "authing-http-user@example.test",
            "email_verified": True,
            "nonce": query["nonce"][0],
            "iat": timestamp,
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
            headers={"kid": "authing-http-key"},
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
                        "refresh_token",
                    ],
                    "response_types_supported": ["code"],
                    "scopes_supported": [
                        "openid",
                        "email",
                        "profile",
                        "offline_access",
                    ],
                    "code_challenge_methods_supported": [
                        "plain",
                        "S256",
                    ],
                    "id_token_signing_alg_values_supported": [
                        "HS256",
                        "RS256",
                    ],
                    "token_endpoint_auth_methods_supported": [
                        "client_secret_post",
                        "client_secret_basic",
                        "none",
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
                            f"raw {APP_SECRET} provider payload"
                        ),
                    },
                )
            payload = parse_qs(request.content.decode("utf-8"))
            code = payload["code"][0]
            return httpx.Response(
                200,
                json={
                    "id_token": self.tokens_by_code[code],
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
                            "kid": "authing-http-key",
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
                    "statusCode": 200,
                    "data": {
                        "access_token": "management-token",
                        "expires_in": 3600,
                    },
                },
            )
        if request.url.path == "/api/v3/get-user":
            assert request.headers["authorization"] == (
                "Bearer management-token"
            )
            assert request.headers["x-authing-userpool-id"] == (
                USER_POOL_ID
            )
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "userId": "authing-http-user",
                        "status": "Activated",
                    },
                },
            )
        raise AssertionError(
            f"Unexpected Authing request: {request.url}"
        )


def _provider(
    boundary: AuthingHttpBoundary,
) -> AuthingCustomerIdentityProvider:
    return AuthingCustomerIdentityProvider(
        AuthingCiamConfig(
            app_host=APP_HOST,
            issuer=ISSUER,
            client_id=CLIENT_ID,
            app_secret=APP_SECRET,
            user_pool_id=USER_POOL_ID,
            user_pool_secret=USER_POOL_SECRET,
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(boundary),
        ),
        clock=lambda: NOW,
    )


def _app(database_url: str, boundary: AuthingHttpBoundary):
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


def test_authing_adapter_completes_login_reauth_and_sign_out(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'authing-flow.db').as_posix()}"
    )
    boundary = AuthingHttpBoundary()
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
        signed_out = browser.post(
            "/auth/logout",
            headers=_csrf_headers(browser),
        )
        provider_logout = browser.get(
            signed_out.json()["redirect_to"],
            follow_redirects=False,
        )
        after_sign_out = browser.get("/me")

    assert login.status_code == 303
    assert authorization_query["code_challenge_method"] == ["S256"]
    assert "offline_access" not in authorization_query["scope"][0]
    assert callback.status_code == 303
    assert callback.headers["location"] == "/console"
    assert first_cookie is not None
    assert me.status_code == 200
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
    assert signed_out.status_code == 200
    assert signed_out.json() == {
        "redirect_to": "/auth/logout/provider"
    }
    assert provider_logout.status_code == 303
    provider_logout_query = parse_qs(
        urlparse(provider_logout.headers["location"]).query
    )
    assert provider_logout_query["id_token_hint"] == [
        boundary.tokens_by_code["recent-authentication-code"]
    ]
    assert provider_logout_query["post_logout_redirect_uri"] == [
        f"{WEB_ORIGIN}/"
    ]
    assert APP_SECRET not in signed_out.text
    assert USER_POOL_SECRET not in signed_out.text
    assert "server-only" not in signed_out.text
    assert after_sign_out.status_code == 401


def test_authing_sign_out_uses_a_one_time_same_origin_continuation(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'authing-logout.db').as_posix()}"
    )
    boundary = AuthingHttpBoundary()
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
            code="logout-code",
            authenticated_at=NOW,
            authentication_context=(
                RECENT_AUTHENTICATION_EMAIL_ACR
            ),
        )
        callback = browser.get(
            "/auth/callback",
            params={
                "state": authorization_query["state"][0],
                "code": "logout-code",
            },
            follow_redirects=False,
        )
        assert callback.status_code == 303
        id_token = boundary.tokens_by_code["logout-code"]

        signed_out = browser.post(
            "/auth/logout",
            headers=_csrf_headers(browser),
        )
        continuation = signed_out.json()["redirect_to"]
        provider_cookie_headers = [
            value
            for value in signed_out.headers.get_list("set-cookie")
            if value.startswith(f"{PROVIDER_LOGOUT_COOKIE}=")
        ]

        assert continuation == "/auth/logout/provider"
        assert len(provider_cookie_headers) == 1
        provider_cookie_header = provider_cookie_headers[0]
        assert "HttpOnly" in provider_cookie_header
        assert "Secure" in provider_cookie_header
        assert "SameSite=lax" in provider_cookie_header
        assert "Path=/" in provider_cookie_header
        assert (
            f"Max-Age={PROVIDER_LOGOUT_CONTINUATION_SECONDS}"
            in provider_cookie_header
        )
        assert id_token not in signed_out.text
        assert id_token not in provider_cookie_header
        assert APP_SECRET not in signed_out.text
        assert USER_POOL_SECRET not in signed_out.text

        continuation_cookie = browser.cookies.get(
            PROVIDER_LOGOUT_COOKIE
        )
        assert continuation_cookie is not None
        with TestClient(
            app,
            base_url=WEB_ORIGIN,
        ) as other_browser:
            tampered = other_browser.get(
                continuation,
                headers={
                    "Cookie": (
                        f"{PROVIDER_LOGOUT_COOKIE}="
                        f"{continuation_cookie}x"
                    )
                },
                follow_redirects=False,
            )

        provider_redirect = browser.get(
            continuation,
            follow_redirects=False,
        )
        audit = browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": signed_out.headers[
                    "x-correlation-id"
                ]
            },
        )
        replay = browser.get(
            continuation,
            follow_redirects=False,
        )
        after_sign_out = browser.get("/me")

    assert provider_redirect.status_code == 303
    assert tampered.status_code == 303
    assert tampered.headers["location"] == f"{WEB_ORIGIN}/"
    assert id_token not in tampered.text
    assert provider_redirect.headers["cache-control"] == "no-store"
    assert provider_redirect.headers["pragma"] == "no-cache"
    assert (
        provider_redirect.headers["referrer-policy"]
        == "no-referrer"
    )
    parsed_redirect = urlparse(
        provider_redirect.headers["location"]
    )
    assert (
        f"{parsed_redirect.scheme}://"
        f"{parsed_redirect.netloc}{parsed_redirect.path}"
    ) == END_SESSION_ENDPOINT
    assert parse_qs(parsed_redirect.query) == {
        "client_id": [CLIENT_ID],
        "id_token_hint": [id_token],
        "post_logout_redirect_uri": [f"{WEB_ORIGIN}/"],
    }
    assert replay.status_code == 303
    assert replay.headers["location"] == f"{WEB_ORIGIN}/"
    assert after_sign_out.status_code == 401
    assert [
        (
            event["event_type"],
            event["outcome"],
            event["reason_code"],
        )
        for event in audit.json()
    ] == [
        ("session_signed_out", "success", "current_device_revoked"),
        (
            "provider_logout",
            "success",
            "end_session_redirect_prepared",
        ),
    ]


def test_existing_authing_customer_can_return_after_database_reopen(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'authing-upgrade.db').as_posix()}"
    )
    boundary = AuthingHttpBoundary()

    def complete_login(
        browser: TestClient,
        *,
        code: str,
    ):
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
            code=code,
            authenticated_at=NOW,
            authentication_context=(
                RECENT_AUTHENTICATION_EMAIL_ACR
            ),
        )
        callback = browser.get(
            "/auth/callback",
            params={
                "state": authorization_query["state"][0],
                "code": code,
            },
            follow_redirects=False,
        )
        assert callback.status_code == 303
        return browser.get("/me").json()

    with TestClient(
        _app(database_url, boundary),
        base_url=WEB_ORIGIN,
    ) as first_browser:
        first_identity = complete_login(
            first_browser,
            code="first-database-code",
        )

    with TestClient(
        _app(database_url, boundary),
        base_url=WEB_ORIGIN,
    ) as browser:
        returning_identity = complete_login(
            browser,
            code="returning-database-code",
        )
        signed_out = browser.post(
            "/auth/logout",
            headers=_csrf_headers(browser),
        )
        provider_redirect = browser.get(
            signed_out.json()["redirect_to"],
            follow_redirects=False,
        )

    assert returning_identity == first_identity
    assert len(returning_identity["accounts"]) == 1
    assert signed_out.status_code == 200
    assert provider_redirect.status_code == 303
    assert parse_qs(
        urlparse(provider_redirect.headers["location"]).query
    )["id_token_hint"] == [
        boundary.tokens_by_code["returning-database-code"]
    ]


def test_authing_failure_is_redacted_at_http_boundary(tmp_path) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'authing-failure.db').as_posix()}"
    )
    boundary = AuthingHttpBoundary()
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
    assert APP_SECRET not in serialized
    assert USER_POOL_SECRET not in serialized
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
