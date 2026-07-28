# AI-SCDC Control Plane

AI-SCDC provides human-facing control over software-engineering work executed within Account and Workspace boundaries.

## Client Surfaces

**Web Console**:
The browser-based interactive client for human control-plane work and the first production authentication surface.
_Avoid_: Desktop, Desktop Client

**Web Application Origin**:
The single public browser origin that serves the Web Console and routes `/api` and `/auth` to the control plane. Browser session cookies are host-only, and unsafe Cookie-authenticated requests require both an exact Origin match and a User Session-bound CSRF token. Internal services may remain separately deployed.
_Avoid_: Physical Server, Native Desktop Origin, Public Worker Identity

**Native Desktop Client**:
A future installed client for human control-plane work, with an authentication lifecycle distinct from the Web Console.
_Avoid_: Web Console

## Identity and Access

**User**:
A person recognized by AI-SCDC who may own or participate in Accounts and hold workspace roles.
_Avoid_: Account, External Identity

**Individual Customer**:
The primary paying User who operates AI-SCDC with personally supplied model-provider credentials.
_Avoid_: Organization Member, AI Team

**Account**:
The ownership and commercial boundary for subscriptions, credits, model credentials, Workspaces, and customer data.
_Avoid_: Organization, AI Team

**Personal Account**:
An Account created for an Individual Customer as the default owner of that customer's AI-SCDC resources.
_Avoid_: User, Workspace

**Legacy Account Storage**:
The transitional use of the existing `organization` table as the physical persistence for the Account domain. New product and API language uses Account, new self-service ownership is marked explicitly as personal, and ambiguous legacy rows remain legacy until a controlled migration establishes ownership. Physical table renaming is outside the production identity slice.
_Avoid_: Product Organization, AI Team, Automatic Personal Account

**Personal Onboarding**:
The first authenticated entry that atomically establishes a User, links an External Identity, creates that User's single Personal Account and default Workspace, grants owner access, and creates a User Session. The operation is idempotent by External Identity and rolls back as a whole if any step fails. A matching email alone never merges Users. Human invitations and shared-account onboarding are outside the initial production slice.
_Avoid_: Invitation, Public Workspace

**Account Link Required**:
A migration state returned when a newly authenticated External Identity has an email matching a legacy User that lacks an explicit identity link. AI-SCDC neither merges by email nor creates a duplicate User; an operator-controlled mapping of `(issuer, subject)` to `user_id` must resolve the state.
_Avoid_: Personal Onboarding, Email Identity, Automatic Merge

**Workspace**:
A work boundary inside an Account that groups projects, tasks, execution records, and AI Teams.
_Avoid_: Account, Organization

**Active Workspace**:
The Workspace currently selected within one Device Session. It is a navigation preference, not proof of authority. Every protected request resolves the User's current membership and role from server-side state. Losing access fails the request and never causes a write to be silently retried against another Workspace.
_Avoid_: Workspace Membership, Role, Token Scope

**AI Team**:
A customer-configured group of model-backed agent roles within a Workspace that collaborate on software-engineering work.
_Avoid_: Human Team, Organization

**Model Credential**:
A customer-supplied model-provider secret owned commercially by an Account and isolated operationally to one Workspace in the initial production slice. Production stores only an Alibaba Cloud KMS-backed ciphertext envelope and display-safe suffix; plaintext is accepted only for create or replace, never returned, and opened only by an audited execution path with an explicit reason. The development vault is forbidden in production.
_Avoid_: Workspace API Token, Worker Callback Token, Displayable API Key

**External Identity**:
An identity asserted by an external identity provider, uniquely recognized by its issuer and subject, and linked to an AI-SCDC User. It establishes who the person is but does not grant AI-SCDC workspace authority; email is descriptive rather than identifying.
_Avoid_: User, Workspace Member

**Identity Status Synchronization**:
The production check that reconciles a User's linked CIAM account state with AI-SCDC authorization. It runs during login and Recent Authentication and at least every five minutes for Users with active Device Sessions. A locked, disabled, or missing CIAM account revokes all human User Sessions and Workspace API Tokens; restricted Worker Callback Tokens remain independently valid for already-running work. A later verified active status permits a locked identity to start a fresh login, while a disabled or missing identity also requires an audited, recently authenticated operator restoration before any fresh login; restoration never revives revoked credentials.
_Avoid_: Login, Role Resolution, Worker Authorization

**Last Confirmed Identity Status**:
The most recent successfully retrieved CIAM account state for an External Identity. CIAM timeout, network failure, or service error leaves this state unchanged and does not suspend existing User Sessions or Workspace API Tokens. The state may remain authoritative indefinitely while CIAM is unavailable; only an explicit verified locked, disabled, or missing result changes local access.
_Avoid_: Live CIAM Status, Local User Status, Session Expiry

**Customer Identity Provider**:
Alibaba Cloud IDaaS CIAM is the initial production identity provider for individual, self-service customers in the mainland-China release. It authenticates people and supplies OIDC identity assertions; AI-SCDC still owns User, Account, Workspace, authorization, and User Session state. Using CIAM does not imply invitation-only registration or a real-world company membership model.
_Avoid_: Account, User Session, Workspace Authorization

**Email Sign-In**:
The initial self-service registration and login method: Alibaba Cloud IDaaS CIAM verifies a one-time code sent to the customer's email address. AI-SCDC does not store a password or require a phone number, and other password, SMS, and social-login methods remain outside the initial production slice.
_Avoid_: Password Login, SMS Login, Social Login

**Login Transaction**:
A short-lived, single-use server-side record that correlates one Web Console OIDC Authorization Code flow. It binds `state`, `nonce`, an S256 PKCE verifier, the exact callback, and an allowlisted post-login destination. CIAM tokens are exchanged and validated only by the server. A repeated callback never reuses the authorization code; it may only redirect an already authenticated browser to the safe destination when the original transaction has completed successfully.
_Avoid_: User Session, Refresh Token, Desktop Callback

**User Session**:
A revocable server-side login context through which an authenticated User accesses the Web Console. The browser holds only an opaque secure cookie, which is renewed and rotated transparently while the User remains active; no browser refresh token exists. A User Session has no fixed absolute lifetime, expires after 30 days without activity, and may end earlier through logout, account disablement, explicit revocation, or detected compromise.
_Avoid_: External Identity, API Token, Worker Callback Token

**Session Credential**:
The opaque `session_id` and 256-bit secret carried by the Web Console's host-only cookie. The server stores only the secret hash in the authoritative SQL Device Session record, rotates the secret every 24 hours and after Recent Authentication, and accepts the prior secret for at most two minutes to cover in-flight requests. Prior-secret reuse after that window revokes the Device Session as suspected replay.
_Avoid_: Workspace API Token, Refresh Token, OIDC Token

**Recent Authentication**:
Fresh CIAM email verification associated with one Device Session. Security-sensitive credential, identity, account, and all-other-session operations require Recent Authentication no more than 15 minutes old. Reauthentication never executes the pending operation automatically; it returns the User to an explicit confirmation step.
_Avoid_: User Session Renewal, Role Check, Routine Workspace Activity

**Device Session**:
One independently revocable User Session created for a browser profile or future native installation. A User may have multiple concurrent Device Sessions without a fixed product limit. AI-SCDC records only coarse operational metadata rather than a persistent device fingerprint. A revoked device must complete fresh CIAM email verification before it can create another User Session.
_Avoid_: User, Device Fingerprint, Worker Session

**Sign Out**:
An explicit user action that first revokes the current AI-SCDC User Session and clears its persistent cookie, then makes a best-effort redirect to the CIAM end-session endpoint for the current browser. Closing a tab, browser, or computer is not Sign Out and preserves the User Session. Failure of CIAM logout never reverses the local revocation.
_Avoid_: Close, Session Expiry, Sign Out All Devices

**Workspace API Token**:
The existing bearer credential for human-owned API or CLI automation within exactly one Workspace. It remains separate from Cookie-based User Sessions, resolves live membership and role on every request, and is not revoked by browser Sign Out. A request carrying both a User Session cookie and a Workspace API Token is rejected as ambiguous. New token-management UI and a multi-token PAT model are outside the initial production identity slice.
_Avoid_: User Session, Refresh Token, Worker Callback Token

**Worker Callback Token**:
A route-constrained machine credential used only for worker-to-control-plane callbacks. It is never accepted as a User Session or Workspace API Token and cannot acquire human workspace authority.
_Avoid_: User Session, Workspace API Token, OIDC Token

**Authentication Policy**:
The environment-scoped set of credential types that an API process may accept. Local and test environments may explicitly use Dev Auth without CIAM. Staging and production accept User Sessions and Workspace API Tokens while worker routes retain their separate machine boundary; they reject Dev Auth and unsafe production combinations at startup. Missing AuthContext never falls back to a development identity in production code paths.
_Avoid_: Auth Mode, User Role, Workspace Permission

**Dev Auth**:
An explicit local/test-only identity mechanism that supplies deterministic development Users, Workspaces, and roles without CIAM. Development identity headers and test injection are forbidden in staging and production.
_Avoid_: User Session, Production Login, Workspace API Token

## Audit

**Identity Audit Log**:
The append-only record of authentication, External Identity, User Session, CSRF, replay, device-revocation, and identity-status events. It may exist before a User or Workspace is known.
_Avoid_: Workspace Audit Log, Secret Access Audit Log

**Workspace Audit Log**:
The append-only record of authorized, denied, and high-value operations against Workspace business resources.
_Avoid_: Identity Audit Log, Secret Access Audit Log

**Secret Access Audit Log**:
The append-only record of creating, opening, rotating, and deleting protected credentials, including the explicit access reason and outcome without secret material.
_Avoid_: Identity Audit Log, Workspace Audit Log

The three audit logs remain distinct typed stores but share stable correlation fields such as `request_id` so one cross-boundary operation can be reconstructed and exported into a unified operational view.

**Audit Retention**:
Identity Audit Log network details are retained for 90 days and then removed while the core event remains for 365 days. Workspace Audit Log, Secret Access Audit Log, and core Identity Audit Log events are retained for 365 days by default. Retention may be lengthened by production policy; shortening or changing it requires an explicit privacy and compliance review.

## Identity Test Seams

**HTTP Application Boundary**:
The primary public test seam for production identity behavior. Tests exercise the authentication-enabled application through real HTTP requests, redirects, cookies, CSRF headers, bearer credentials, and observable persistence and audit outcomes rather than asserting internal service calls.

**Customer Identity Provider Boundary**:
The single provider-neutral external seam for OIDC discovery, authorization, code exchange, ID Token validation, end-session discovery, and verified account-status lookup. It distinguishes account status from provider unavailability. Production uses the CIAM adapter; automated tests use a local fake provider and release acceptance uses a real CIAM test tenant.

Session storage, Personal Onboarding, and Identity Audit remain internal modules whose effects are verified through the HTTP Application Boundary. Authentication tests may inject a controllable Clock, secure token generator, and failures as test controls rather than public seams. The existing SecretVault remains the sole credential-vault seam. Release acceptance includes migration and rollback rehearsal, browser Cookie and CSRF tests, secret-leak scanning, and regression coverage for Workspace API Token, RBAC, SecretVault, and Worker Callback Token boundaries.
