# Authing Public Cloud test-tenant release gate

This release gate qualifies Authing as AI-SCDC's first production Customer
Identity Provider. It is intentionally excluded from default automated test
discovery so local and CI tests continue to run against the deterministic fake
without Authing credentials.

Official contracts used by the adapter:

- [Authing as an OpenID Connect identity provider](https://docs.authing.cn/v2/guides/federation/oidc.html)
- [Authorization Code Flow with PKCE S256](https://docs.authing.cn/v2/federation/oidc/pkce/)
- [Authing OIDC endpoints and logout](https://docs.authing.cn/v3/reference/sdk/python/authentication/oidc.html)
- [Authing management SDK authentication](https://docs.authing.cn/v3/reference/sdk/go/install.html)
- [Authing user lifecycle status](https://docs.authing.cn/v2/reference-new/sdk-v5/csharp/%E7%94%A8%E6%88%B7%E7%AE%A1%E7%90%86/list-users.html)

## Tenant isolation

Use a dedicated AI-SCDC user pool and a Standard Web application. Do not model
AI-SCDC Accounts, Workspaces, memberships, roles, or AI Teams in Authing.
Authing is responsible only for external customer identity.

The Authing UserPool ID and UserPool Secret currently form a global management
AK/SK for the entire user pool. Authing documentation describes
fine-grained collaborator AK/SK as still under development. The dedicated user
pool limits the blast radius until a proven least-privilege credential is
available.

## Console configuration

Configure the test application as follows:

| Setting | Required value |
| --- | --- |
| Application type | Standard Web application |
| Login callback URL | `http://localhost:8000/auth/callback` |
| Logout callback URL | `http://localhost:8000/` |
| Grant | `authorization_code` |
| Response type | `code` |
| Token endpoint authentication | `client_secret_basic` |
| ID Token signing algorithm | `RS256` |
| PKCE | `S256` |
| Registration | Public self-registration |
| Sign-in method | Email verification code only |
| Application access control | Allow all users, with no explicit deny rules |

Disable password, phone/SMS, social login, invitation gating, Authing
organizations, and Authing application roles for this slice. AI-SCDC does not
request `offline_access` and does not use an Authing Refresh Token as a User
Session.

Configure one synchronous **OIDC ID Token pre-issuance** Pipeline for this
application. It must add the standard `auth_time` claim from Authing's
server-side last-login evidence and set `acr` to
`urn:ai-scdc:email-verification-code`. Guard the Pipeline by the AI-SCDC
application ID so it cannot change tokens issued to other applications in the
user pool. Neither claim may be accepted from browser input or user-editable
profile metadata.

Authing evaluates explicit application access grants or denials ahead of the
default access setting. A public customer application must therefore use the
default **Allow all users** policy and must not retain an explicit deny entry
for the test customer. A denial at this layer happens after identity proof but
before the OIDC callback and is not an AI-SCDC callback or PKCE failure.

For Recent Authentication, verify that `prompt=login` and `max_age=0` force a
new email verification rather than silently reusing the Authing browser
session. Record the actual ID Token claim names and values that prove the
authentication time and email-code method. Do not infer these claims from the
configured request.

## Secret handling

Never put App Secret, UserPool Secret, email OTPs, authorization codes, OIDC
tokens, Cookie values, or raw provider responses in chat, screenshots, issue
comments, commits, CI variables printed by jobs, application logs, telemetry,
or audits. In particular, never capture or copy the browser address while an
OIDC end-session navigation contains `id_token_hint`.

The repository ignores `.env`, `.env.local`, and `.secrets/`. Prefer a process
environment or an approved secret manager. If a local file is used, confirm it
is ignored before entering either secret. The release test reads:

- `AI_SCDC_AUTHING_APP_HOST`
- `AI_SCDC_AUTHING_ISSUER`
- `AI_SCDC_AUTHING_APP_ID`
- `AI_SCDC_AUTHING_APP_SECRET`
- `AI_SCDC_AUTHING_USER_POOL_ID`
- `AI_SCDC_AUTHING_USER_POOL_SECRET`
- `AI_SCDC_AUTHING_SMOKE_REDIRECT_URI`
- `AI_SCDC_AUTHING_SMOKE_POST_LOGOUT_REDIRECT_URI`
- `AI_SCDC_AUTHING_SMOKE_SUBJECT`

Run the explicitly selected non-interactive probe:

```powershell
python -m pytest apps/api/release_tests/test_authing_ciam_test_tenant.py -q
```

The probe validates live discovery, the PKCE S256 authorization request,
management status access, and the discovered end-session destination. It never
prints configured secrets, tokens, or provider response bodies.

## Local HTTP acceptance server

The explicit app factory at
`release_tests.authing_ciam_test_server:create_authing_test_tenant_app`
assembles the real Authing adapter behind the HTTP Application Boundary. It is
not a production entry point, is not imported by the default app, and must bind
only to `127.0.0.1:8000`. It uses the ignored
`.secrets/authing-release.db` database so repeated test-tenant logins can be
inspected without entering the repository history.

After supplying the required variables to the current process, start it from
the repository root:

```powershell
$env:AI_SCDC_AUTHENTICATION_ENVIRONMENT = "test"
$env:PYTHONPATH = @(
    (Resolve-Path "apps/api/app").Path
    (Resolve-Path "apps/worker/app").Path
    (Resolve-Path "services/llm-gateway/app").Path
) -join [IO.Path]::PathSeparator
Push-Location "apps/api"
python -m uvicorn `
    release_tests.authing_ciam_test_server:create_authing_test_tenant_app `
    --factory `
    --host 127.0.0.1 `
    --port 8000
Pop-Location
```

Open `http://localhost:8000/` in the same browser used for the acceptance
flow. The local page exposes only login, current identity, Recent
Authentication, and Sign Out controls over existing public endpoints. It does
not expose provider credentials or tokens in page content, JSON, JavaScript,
or Cookies. Authing requires the final OIDC end-session navigation to carry an
ID Token hint in its HTTPS URL; that provider-bound 303 `Location` is the sole
documented exception and must use `Cache-Control: no-store` plus
`Referrer-Policy: no-referrer`. Stop the process when the interactive gate is
complete; never publish or tunnel this test-only server.

## Interactive HTTP Application Boundary acceptance

Use a disposable test customer. Record only timestamps, safe correlation IDs,
and pass/fail outcomes.

1. Self-register using an email verification code. Confirm the callback creates
   one User, one Personal Account, one default Workspace, owner access, and one
   Device Session.
2. Sign out and return with the same email. Confirm no duplicate Account or
   Workspace is created.
3. Refresh the successful callback in the same browser. Confirm safe redirect
   without another code exchange. Replay it from another browser and confirm
   rejection.
4. Try an unregistered callback or return destination. Confirm rejection
   without exposing provider details.
5. Start Recent Authentication. Confirm a new email verification, trustworthy
   authentication-time and method evidence, Session Credential rotation, and a
   return to the confirmation screen without executing the sensitive action.
6. Sign out while Authing is available. Confirm local revocation and Cookie
   clearing happen before the Authing end-session redirect, then confirm the
   registered post-logout return.
7. Repeat Sign Out while blocking Authing. Confirm local Sign Out remains
   complete.
8. Through the Authing console, exercise Activated, Suspended, Deactivated,
   Resigned or Archived, and deleted/missing states. Confirm the
   provider-neutral results are active, locked, disabled, and missing.
9. Exercise or safely simulate timeout, network failure, HTTP 429, and HTTP 5xx.
   Confirm they remain distinct unavailable results rather than false identity
   states.
10. Scan responses, application logs, telemetry, audit exports, test output, and
    CI artifacts for every configured canary secret.

## Evidence status

Evidence recorded on 2026-07-28 and 2026-07-29:

| Capability | Status |
| --- | --- |
| Dedicated Authing user pool and Standard Web app | VERIFIED — test tenant created |
| OIDC discovery issuer and endpoint origin | VERIFIED — live public discovery |
| Authorization Code and PKCE S256 request | VERIFIED — live adapter probe against the test tenant |
| `client_secret_basic` and RS256 metadata | VERIFIED — live public discovery advertises both, and a real callback completed code exchange plus RS256 ID Token validation |
| End-session and post-logout return | VERIFIED — after local-first revocation, a real one-time Provider Logout Continuation supplied Authing's required `id_token_hint`; Authing ended its browser session and automatically returned to the exact registered local origin |
| Exact callback and logout allowlists | VERIFIED — Authing accepted the exact login callback and exact registered post-logout return, while the acceptance server rejects alternate callback or logout destinations before startup |
| Email verification-code delivery and self-registration | VERIFIED — a real email code completed Authing login, AI-SCDC callback validation, Personal Account/default Workspace onboarding, and User Session issuance |
| Returning login and idempotent customer resources | VERIFIED — the same real customer completed another email-code login after application/database reopen; User, External Identity, Personal Account, default Workspace, and membership counts remained one while a new independently revocable Device Session was issued |
| Provider Logout Continuation cleanup and audit | VERIFIED — the real continuation was consumed once, its sealed hint and browser-secret hash were erased, both Device Sessions were revoked, and local/provider logout audit events shared their correlation chain |
| Application access control | VERIFIED — after changing the default to allow all users, the real email-code flow reached the registered callback |
| Code exchange and actual RS256 ID Token | VERIFIED — a real callback passed code exchange plus issuer, audience, RS256, nonce, and timing validation |
| Forced email Recent Authentication claims | VERIFIED — the application-scoped synchronous Pipeline emitted standard `auth_time` plus the agreed email-verification `acr`; `prompt=login` and `max_age=0` forced a new email verification, rotated the Session Credential, and returned to explicit confirmation |
| Management Token authentication and missing-subject semantics | VERIFIED — the current UserPool ID and current UserPool Secret acquired a Management Token; the real test customer mapped to `active`, and a valid-shaped unknown subject mapped to provider-neutral `missing` |
| Activated, Suspended, and Deactivated/Resigned/Archived status semantics | PRODUCTION BLOCKER — deterministic provider-contract tests cover every documented mapping, but real lifecycle transitions still require a disposable Authing customer and console rehearsal |
| Timeout, rate-limit, service-failure behavior and quotas | PRODUCTION BLOCKER — deterministic boundary tests distinguish timeout, network, HTTP/body 429, and HTTP/body 5xx; live throttling thresholds and plan quotas remain unknown |
| Production email deliverability and plan quotas | PRODUCTION BLOCKER — the dedicated test tenant delivered real email codes, but production sender policy, reputation, volume, and plan evidence remain pending |

Final pre-commit gates recorded on 2026-07-29:

- Authing provider, HTTP, Sign Out, and acceptance-server suite:
  63 passed.
- Explicit real-tenant release probe: 4 passed.
- Repository test command: 127 JavaScript/TypeScript tests and 895 Python
  tests passed.
- Repository typecheck, lint, Python compilation, and diff validation:
  passed.
- Git candidate-file secret scan: 244 files checked, zero configured-secret
  matches, and the local secret file remained ignored.
- Standards review: passed. Spec review found no remaining code blocker; the
  three production blockers above remain explicit release constraints.

Do not enable public production registration or close #39 until every blocked
row is replaced with dated test-tenant evidence or an explicitly accepted
production limitation.
