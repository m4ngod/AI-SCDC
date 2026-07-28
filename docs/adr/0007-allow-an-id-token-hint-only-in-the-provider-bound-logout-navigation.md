---
status: accepted
---

# Allow an ID Token hint only in the provider-bound logout navigation

Authing requires an `id_token_hint` before it will honor the registered post-logout return. A strict rule that removes every token from every HTTP response therefore conflicts with automatic browser return after Authing Sign Out: the standard provider navigation itself must carry the ID Token hint.

AI-SCDC will permit the raw ID Token only in the final `303 Location` sent from a one-time same-origin Provider Logout Continuation to Authing's validated HTTPS end-session endpoint. Local User Session revocation and Cookie clearing happen first. Before that final navigation, the browser receives only a short-lived opaque host-only continuation; the sealed provider hint remains server-side. Continuation consumption, hint erasure, the provider-logout outcome, and its Identity Audit Event commit atomically. The final response uses `Cache-Control: no-store`, `Pragma: no-cache`, and `Referrer-Policy: no-referrer`.

This is a narrow provider-bound exception, not permission to expose tokens through JSON, page content, JavaScript, Cookies, application logs, telemetry, audits, errors, tests, issue comments, or screenshots. The browser address and Authing's own request handling can necessarily observe the hint during the final navigation, so operators and testers must not capture that URL. If Authing later supports a safe automatic return without an ID Token in the browser navigation, this exception should be retired.
