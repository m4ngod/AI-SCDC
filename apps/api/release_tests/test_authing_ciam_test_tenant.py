from datetime import datetime, timezone
import os
from urllib.parse import parse_qs, urlparse

import pytest

from ai_company_api.services.authing_ciam_provider import (
    AuthingCiamConfig,
    AuthingCustomerIdentityProvider,
)
from ai_company_api.services.customer_identity_provider import (
    OidcAuthorizationRequest,
)
from ai_company_api.services.identity_login import (
    RECENT_AUTHENTICATION_EMAIL_ACR,
)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is required for the explicit Authing release gate",
            pytrace=False,
        )
    return value


@pytest.fixture
def provider() -> AuthingCustomerIdentityProvider:
    return AuthingCustomerIdentityProvider(
        AuthingCiamConfig(
            app_host=_required_environment(
                "AI_SCDC_AUTHING_APP_HOST"
            ),
            issuer=_required_environment(
                "AI_SCDC_AUTHING_ISSUER"
            ),
            client_id=_required_environment(
                "AI_SCDC_AUTHING_APP_ID"
            ),
            app_secret=_required_environment(
                "AI_SCDC_AUTHING_APP_SECRET"
            ),
            user_pool_id=_required_environment(
                "AI_SCDC_AUTHING_USER_POOL_ID"
            ),
            user_pool_secret=_required_environment(
                "AI_SCDC_AUTHING_USER_POOL_SECRET"
            ),
        )
    )


def test_real_authing_tenant_exposes_compatible_oidc(
    provider: AuthingCustomerIdentityProvider,
) -> None:
    discovery = provider.discover()

    assert discovery.issuer == _required_environment(
        "AI_SCDC_AUTHING_ISSUER"
    )
    assert urlparse(discovery.authorization_endpoint).scheme == "https"
    assert urlparse(discovery.token_endpoint).scheme == "https"

    authorization_url = provider.authorization_url(
        OidcAuthorizationRequest(
            client_id=provider.client_id,
            redirect_uri=_required_environment(
                "AI_SCDC_AUTHING_SMOKE_REDIRECT_URI"
            ),
            state="release-gate-state",
            nonce="release-gate-nonce",
            code_challenge=(
                "uQeP5T4bTn8rJmK3zV6xC9sL2wA7dF0hG1yB4pN6qR8"
            ),
            prompt="login",
            max_age_seconds=0,
            acr_values=RECENT_AUTHENTICATION_EMAIL_ACR,
            authentication_requested_at=datetime.now(timezone.utc),
        )
    )
    query = parse_qs(urlparse(authorization_url).query)

    assert query["response_type"] == ["code"]
    assert query["response_mode"] == ["query"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["prompt"] == ["login"]
    assert query["max_age"] == ["0"]
    assert query["acr_values"] == [
        RECENT_AUTHENTICATION_EMAIL_ACR
    ]
    assert "offline_access" not in query["scope"][0]


def test_real_authing_tenant_returns_known_identity_status(
    provider: AuthingCustomerIdentityProvider,
) -> None:
    status = provider.identity_status(
        issuer=_required_environment("AI_SCDC_AUTHING_ISSUER"),
        subject=_required_environment(
            "AI_SCDC_AUTHING_SMOKE_SUBJECT"
        ),
    )

    assert status in {"active", "locked", "disabled"}


def test_real_authing_tenant_management_credentials_reach_missing_subject(
    provider: AuthingCustomerIdentityProvider,
) -> None:
    status = provider.identity_status(
        issuer=_required_environment("AI_SCDC_AUTHING_ISSUER"),
        subject="000000000000000000000000",
    )

    assert status == "missing"


def test_real_authing_tenant_exposes_safe_end_session_destination(
    provider: AuthingCustomerIdentityProvider,
) -> None:
    end_session_url = provider.end_session_url(
        post_logout_redirect_uri=_required_environment(
            "AI_SCDC_AUTHING_SMOKE_POST_LOGOUT_REDIRECT_URI"
        )
    )

    assert end_session_url is not None
    parsed = urlparse(end_session_url)
    assert parsed.scheme == "https"
    assert parse_qs(parsed.query)["client_id"] == [
        provider.client_id
    ]
