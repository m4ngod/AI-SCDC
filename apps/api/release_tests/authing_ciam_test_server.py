from collections.abc import Mapping
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import httpx

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


LOCAL_PUBLIC_ORIGIN = "http://localhost:8000"
LOCAL_CALLBACK = f"{LOCAL_PUBLIC_ORIGIN}/auth/callback"
LOCAL_POST_LOGOUT_REDIRECT = f"{LOCAL_PUBLIC_ORIGIN}/"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_URL = (
    "sqlite:///"
    f"{(_REPOSITORY_ROOT / '.secrets' / 'authing-release.db').as_posix()}"
)

_REQUIRED_ENVIRONMENT = (
    "AI_SCDC_AUTHING_APP_HOST",
    "AI_SCDC_AUTHING_ISSUER",
    "AI_SCDC_AUTHING_APP_ID",
    "AI_SCDC_AUTHING_APP_SECRET",
    "AI_SCDC_AUTHING_USER_POOL_ID",
    "AI_SCDC_AUTHING_USER_POOL_SECRET",
    "AI_SCDC_AUTHING_SMOKE_REDIRECT_URI",
    "AI_SCDC_AUTHING_SMOKE_POST_LOGOUT_REDIRECT_URI",
)

_LANDING_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI-SCDC Authing release gate</title>
</head>
<body>
  <main>
    <h1>AI-SCDC Authing release gate</h1>
    <p>This local-only page exercises the public HTTP identity boundary.</p>
    <p>
      <a href="/auth/login?return_to=/console">
        Start Authing acceptance login
      </a>
    </p>
  </main>
</body>
</html>
"""

_CONSOLE_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI-SCDC Authing acceptance console</title>
</head>
<body>
  <main>
    <h1>Authing acceptance console</h1>
    <pre id="identity">Loading current identity...</pre>
    <p>
      <a href="/auth/reauthenticate?return_to=/reauthentication/confirm">
        Require recent email authentication
      </a>
    </p>
    <button id="sign-out" type="button">Sign out</button>
  </main>
  <script>
    const identity = document.getElementById("identity");
    fetch("/me", {credentials: "same-origin"})
      .then(async (response) => {
        const payload = await response.json();
        identity.textContent = JSON.stringify(payload, null, 2);
      })
      .catch(() => {
        identity.textContent = "Unable to read the current identity.";
      });

    document.getElementById("sign-out").addEventListener(
      "click",
      async () => {
        const csrf = await fetch(
          "/auth/csrf",
          {credentials: "same-origin"}
        ).then((response) => response.json());
        const response = await fetch(
          "/auth/logout",
          {
            method: "POST",
            credentials: "same-origin",
            headers: {"X-CSRF-Token": csrf.csrf_token}
          }
        );
        const result = await response.json();
        window.location.assign(result.redirect_to || "/");
      }
    );
  </script>
</body>
</html>
"""

_REAUTHENTICATION_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI-SCDC recent authentication result</title>
</head>
<body>
  <main>
    <h1>Recent authentication returned to confirmation</h1>
    <p id="result"></p>
    <p><a href="/console">Return to the acceptance console</a></p>
  </main>
  <script>
    const result = new URLSearchParams(window.location.search)
      .get("reauthentication");
    document.getElementById("result").textContent =
      result === "confirmed"
        ? "Fresh email authentication was confirmed."
        : "Fresh email authentication was not confirmed.";
  </script>
</body>
</html>
"""


def create_authing_test_tenant_app(
    *,
    environ: Mapping[str, str] | None = None,
    database_url: str = DEFAULT_DATABASE_URL,
    http_client: httpx.Client | None = None,
) -> FastAPI:
    configured = os.environ if environ is None else environ
    values = {
        name: _required_environment(configured, name)
        for name in _REQUIRED_ENVIRONMENT
    }
    _require_exact(
        values,
        "AI_SCDC_AUTHING_SMOKE_REDIRECT_URI",
        LOCAL_CALLBACK,
    )
    _require_exact(
        values,
        "AI_SCDC_AUTHING_SMOKE_POST_LOGOUT_REDIRECT_URI",
        LOCAL_POST_LOGOUT_REDIRECT,
    )

    provider = AuthingCustomerIdentityProvider(
        AuthingCiamConfig(
            app_host=values["AI_SCDC_AUTHING_APP_HOST"],
            issuer=values["AI_SCDC_AUTHING_ISSUER"],
            client_id=values["AI_SCDC_AUTHING_APP_ID"],
            app_secret=values["AI_SCDC_AUTHING_APP_SECRET"],
            user_pool_id=values["AI_SCDC_AUTHING_USER_POOL_ID"],
            user_pool_secret=values[
                "AI_SCDC_AUTHING_USER_POOL_SECRET"
            ],
        ),
        http_client=http_client,
    )
    app = create_app(
        database_url=database_url,
        cors_origins=(LOCAL_PUBLIC_ORIGIN,),
        authentication_policy=AuthenticationPolicy(
            environment=AuthenticationEnvironment.TEST,
            accepted_human_credentials=frozenset(
                {HumanCredentialType.USER_SESSION}
            ),
        ),
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        allowed_recent_authentication_return_destinations=frozenset(
            {"/reauthentication/confirm"}
        ),
        public_origin=LOCAL_PUBLIC_ORIGIN,
        identity_status_synchronization_poll_seconds=300,
    )

    @app.get(
        "/",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def release_gate_landing() -> HTMLResponse:
        return HTMLResponse(_LANDING_PAGE)

    @app.get(
        "/console",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def release_gate_console() -> HTMLResponse:
        return HTMLResponse(_CONSOLE_PAGE)

    @app.get(
        "/reauthentication/confirm",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def release_gate_reauthentication() -> HTMLResponse:
        return HTMLResponse(_REAUTHENTICATION_PAGE)

    return app


def _required_environment(
    environ: Mapping[str, str],
    name: str,
) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _require_exact(
    values: Mapping[str, str],
    name: str,
    expected: str,
) -> None:
    if values[name] != expected:
        raise ValueError(f"{name} must equal {expected}")
