from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import httpx
import pytest

from authing_ciam_test_server import (
    create_authing_test_tenant_app,
)


APP_HOST = "https://ai-scdc-test.authing.cn"
ISSUER = f"{APP_HOST}/oidc"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = f"{APP_HOST}/oidc/auth"
TOKEN_ENDPOINT = f"{APP_HOST}/oidc/token"
JWKS_URI = f"{ISSUER}/.well-known/jwks.json"
END_SESSION_ENDPOINT = f"{APP_HOST}/oidc/session/end"
APP_SECRET = "release-app-secret"
USER_POOL_SECRET = "release-user-pool-secret"
ENVIRONMENT = {
    "AI_SCDC_AUTHING_APP_HOST": APP_HOST,
    "AI_SCDC_AUTHING_ISSUER": ISSUER,
    "AI_SCDC_AUTHING_APP_ID": "release-app-id",
    "AI_SCDC_AUTHING_APP_SECRET": APP_SECRET,
    "AI_SCDC_AUTHING_USER_POOL_ID": "222222222222222222222222",
    "AI_SCDC_AUTHING_USER_POOL_SECRET": USER_POOL_SECRET,
    "AI_SCDC_AUTHING_SMOKE_REDIRECT_URI": (
        "http://localhost:8000/auth/callback"
    ),
    "AI_SCDC_AUTHING_SMOKE_POST_LOGOUT_REDIRECT_URI": (
        "http://localhost:8000/"
    ),
}


def _discovery_handler(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == DISCOVERY_URL
    return httpx.Response(
        200,
        json={
            "issuer": ISSUER,
            "authorization_endpoint": AUTHORIZATION_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
            "jwks_uri": JWKS_URI,
            "end_session_endpoint": END_SESSION_ENDPOINT,
            "grant_types_supported": ["authorization_code"],
            "response_types_supported": ["code"],
            "scopes_supported": ["openid", "email", "profile"],
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic"
            ],
        },
    )


def test_authing_test_tenant_server_starts_login_at_http_boundary(
    tmp_path,
) -> None:
    app = create_authing_test_tenant_app(
        environ=ENVIRONMENT,
        database_url=(
            f"sqlite:///{(tmp_path / 'release.db').as_posix()}"
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(_discovery_handler)
        ),
    )

    with TestClient(
        app,
        base_url="http://localhost:8000",
    ) as browser:
        landing = browser.get("/")
        login = browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )

    query = parse_qs(urlparse(login.headers["location"]).query)
    assert landing.status_code == 200
    assert "Start Authing acceptance login" in landing.text
    assert login.status_code == 303
    assert query["redirect_uri"] == [
        "http://localhost:8000/auth/callback"
    ]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert "offline_access" not in query["scope"][0]
    assert APP_SECRET not in landing.text + login.headers["location"]
    assert USER_POOL_SECRET not in (
        landing.text + login.headers["location"]
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AI_SCDC_AUTHING_APP_SECRET", ""),
        (
            "AI_SCDC_AUTHING_SMOKE_REDIRECT_URI",
            "http://localhost:8000/wrong",
        ),
        (
            "AI_SCDC_AUTHING_SMOKE_POST_LOGOUT_REDIRECT_URI",
            "http://localhost:8000/wrong",
        ),
    ],
)
def test_authing_test_tenant_server_rejects_unsafe_configuration(
    name: str,
    value: str,
    tmp_path,
) -> None:
    configured = ENVIRONMENT | {name: value}

    with pytest.raises(ValueError) as exc_info:
        create_authing_test_tenant_app(
            environ=configured,
            database_url=(
                f"sqlite:///{(tmp_path / 'release.db').as_posix()}"
            ),
        )

    message = str(exc_info.value)
    assert name in message
    assert APP_SECRET not in message
    assert USER_POOL_SECRET not in message
