# Alibaba Cloud IDaaS CIAM test-tenant release gate

This release gate validates the first production Customer Identity Provider.
It is intentionally excluded from the default automated suite so local and CI
tests continue to run against the deterministic fake without CIAM credentials.

Official contracts used by the adapter:

- [CIAM authorization and OIDC discovery](https://www.alibabacloud.com/help/en/idaas/ciam/developer-reference/authorization-information)
- [CIAM Open API domain](https://www.alibabacloud.com/help/en/idaas/ciam/developer-reference/api-overview)
- [CIAM management APIs](https://help.aliyun.com/en/idaas/ciam/developer-reference/management-interface)
- [CIAM account lifecycle](https://www.alibabacloud.com/help/en/idaas/ciam/user-guide/account-lifecycle-1)

## Non-interactive tenant probe

Configure a dedicated, non-production CIAM application and a disposable test
customer. Grant only the management read permission needed to retrieve that
customer's status. Set these variables in the release environment:

- `AI_SCDC_CIAM_TENANT_BASE_URL`
- `AI_SCDC_CIAM_MANAGEMENT_API_BASE_URL`
- `AI_SCDC_CIAM_ISSUER`
- `AI_SCDC_CIAM_CLIENT_ID`
- `AI_SCDC_CIAM_CLIENT_SECRET`
- `AI_SCDC_CIAM_SMOKE_REDIRECT_URI`
- `AI_SCDC_CIAM_SMOKE_POST_LOGOUT_REDIRECT_URI`
- `AI_SCDC_CIAM_SMOKE_SUBJECT`

`AI_SCDC_CIAM_TENANT_BASE_URL` is the CIAM login domain used by OIDC and
OAuth token endpoints. `AI_SCDC_CIAM_MANAGEMENT_API_BASE_URL` is the
instance's separate Open API domain shown in the CIAM console. Do not derive
one from the other.

Run the explicitly selected probe:

```powershell
python -m pytest apps/api/release_tests/test_alibaba_ciam_test_tenant.py -q
```

The probe validates discovery metadata, HTTPS endpoint trust, the PKCE S256
authorization request, management-token use, explicit lifecycle status, and
the discovered end-session destination. It never prints the client secret,
management token, user token, authorization code, or provider response body.

## Interactive Web Console acceptance

Use the same dedicated tenant with the Web Console callback and post-logout
destinations registered exactly. Record only timestamps, safe correlation IDs,
and pass/fail outcomes. Do not record email OTPs, authorization codes, tokens,
cookies, screenshots containing those values, or raw provider responses.

1. Self-register a new disposable customer using an email verification code.
   Confirm the callback creates one User, one Personal Account, one default
   Workspace, owner access, and one Device Session.
2. Sign out, then sign in again with the same email. Confirm the same customer
   resources are restored and no duplicate Account or Workspace is created.
3. Refresh the successful callback in the same browser. Confirm it redirects
   safely without a second code exchange. Replay it in another browser and
   confirm rejection.
4. Try an unregistered callback and return destination. Confirm both are
   rejected without exposing provider details.
5. Start Recent Authentication. Confirm CIAM performs a new email verification,
   returns `auth_time` and the configured email-verification `acr`, rotates the
   local Session Credential, and returns to the confirmation screen without
   performing a sensitive action.
6. Sign out while CIAM is available. Confirm local revocation and cookie
   clearing happen before redirect, then confirm the discovered CIAM
   end-session behavior and registered post-logout redirect.
7. Repeat Sign Out with the end-session request blocked. Confirm local Sign Out
   remains complete.
8. Through the test-tenant console, exercise active, locked, disabled, and
   deleted/missing customer states. Confirm the management adapter returns the
   corresponding provider-neutral status.
9. Exercise or simulate timeout, network failure, HTTP 429, and HTTP 5xx.
   Confirm they remain distinct unavailable results and do not become a false
   identity status.
10. Run the release secret scan across responses, application logs, telemetry,
    and audit exports.

## Evidence status

As of 2026-07-28, this workspace has no CIAM test-tenant configuration. The
following items are explicit production blockers until evidence is recorded:

| Capability | Status |
| --- | --- |
| Email verification-code delivery and self-registration | BLOCKED — no dedicated test tenant |
| Authorization Code Flow with PKCE S256 | BLOCKED — no dedicated test tenant |
| Exact callback and safe duplicate/replay behavior | BLOCKED — no dedicated test tenant |
| Forced email Recent Authentication with `auth_time` and `acr` | BLOCKED — no dedicated test tenant |
| Actual CIAM end-session behavior | BLOCKED — no dedicated test tenant |
| Active, locked, disabled, and missing status semantics | BLOCKED — no dedicated test tenant |
| Timeout, network, rate-limit, service-failure behavior and quotas | BLOCKED — no dedicated test tenant |
| Production email deliverability and relevant quotas | BLOCKED — no dedicated test tenant |

Do not enable production registration or close the CIAM adapter release gate
until every row is replaced with dated test-tenant evidence or an explicitly
accepted production limitation.
