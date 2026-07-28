import os
from urllib.parse import parse_qs, urlparse

import pytest

from ai_company_api.services.alibaba_ciam_provider import (
    AlibabaCiamConfig,
    AlibabaCiamCustomerIdentityProvider,
)
from ai_company_api.services.customer_identity_provider import (
    OidcAuthorizationRequest,
)


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.fail(
            f"{name} is required for the explicit CIAM release gate",
            pytrace=False,
        )
    return value


@pytest.fixture(scope="module")
def provider() -> AlibabaCiamCustomerIdentityProvider:
    return AlibabaCiamCustomerIdentityProvider(
        AlibabaCiamConfig(
            tenant_base_url=_required_environment(
                "AI_SCDC_CIAM_TENANT_BASE_URL"
            ),
            management_api_base_url=_required_environment(
                "AI_SCDC_CIAM_MANAGEMENT_API_BASE_URL"
            ),
            issuer=_required_environment(
                "AI_SCDC_CIAM_ISSUER"
            ),
            client_id=_required_environment(
                "AI_SCDC_CIAM_CLIENT_ID"
            ),
            client_secret=_required_environment(
                "AI_SCDC_CIAM_CLIENT_SECRET"
            ),
        )
    )


def test_tenant_discovery_supports_required_oidc_contract(
    provider: AlibabaCiamCustomerIdentityProvider,
) -> None:
    discovery = provider.discover()

    assert discovery.issuer
    assert discovery.authorization_endpoint.startswith("https://")
    assert discovery.token_endpoint.startswith("https://")


def test_tenant_authorization_request_uses_pkce_s256_and_email_flow(
    provider: AlibabaCiamCustomerIdentityProvider,
) -> None:
    authorization_url = provider.authorization_url(
        OidcAuthorizationRequest(
            client_id=provider.client_id,
            redirect_uri=_required_environment(
                "AI_SCDC_CIAM_SMOKE_REDIRECT_URI"
            ),
            state="release-gate-state",
            nonce="release-gate-nonce",
            code_challenge="release-gate-pkce-challenge",
            prompt="login",
            max_age_seconds=0,
            acr_values="urn:ai-scdc:email-verification-code",
        )
    )

    query = parse_qs(urlparse(authorization_url).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["USER_API openid email"]
    assert query["prompt"] == ["login"]
    assert query["max_age"] == ["0"]


def test_tenant_management_status_is_available_and_explicit(
    provider: AlibabaCiamCustomerIdentityProvider,
) -> None:
    status = provider.identity_status(
        issuer=_required_environment("AI_SCDC_CIAM_ISSUER"),
        subject=_required_environment(
            "AI_SCDC_CIAM_SMOKE_SUBJECT"
        ),
    )

    assert status in {"active", "locked", "disabled", "missing"}


def test_tenant_exposes_a_safe_end_session_destination(
    provider: AlibabaCiamCustomerIdentityProvider,
) -> None:
    end_session_url = provider.end_session_url(
        post_logout_redirect_uri=_required_environment(
            "AI_SCDC_CIAM_SMOKE_POST_LOGOUT_REDIRECT_URI"
        )
    )

    assert end_session_url is not None
    assert urlparse(end_session_url).scheme == "https"
