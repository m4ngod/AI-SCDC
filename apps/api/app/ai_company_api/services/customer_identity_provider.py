from dataclasses import dataclass, field
from base64 import urlsafe_b64encode
from datetime import datetime
from hashlib import sha256
from threading import Condition, Event, Lock
from time import sleep
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlparse


class CustomerIdentityProviderError(RuntimeError):
    pass


class CustomerIdentityProviderUnavailable(CustomerIdentityProviderError):
    pass


class CustomerIdentityProviderTimeout(
    CustomerIdentityProviderUnavailable
):
    pass


class CustomerIdentityProviderNetworkError(
    CustomerIdentityProviderUnavailable
):
    pass


class CustomerIdentityProviderRateLimited(
    CustomerIdentityProviderUnavailable
):
    pass


class CustomerIdentityProviderServiceUnavailable(
    CustomerIdentityProviderUnavailable
):
    pass


@dataclass(frozen=True)
class OidcDiscovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    end_session_endpoint: str | None


@dataclass(frozen=True)
class OidcAuthorizationRequest:
    client_id: str
    redirect_uri: str
    state: str
    nonce: str
    code_challenge: str
    prompt: str | None = None
    max_age_seconds: int | None = None
    acr_values: str | None = None
    authentication_requested_at: datetime | None = None


@dataclass(frozen=True)
class OidcTokenResponse:
    id_token: str = field(repr=False)
    access_token: str = field(repr=False)
    logout_hint: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ValidatedExternalIdentity:
    issuer: str
    subject: str
    email: str | None
    nonce: str
    authenticated_at: datetime | None
    authentication_context: str | None


class CustomerIdentityProvider(Protocol):
    client_id: str

    def discover(self) -> OidcDiscovery: ...

    def check_availability(self) -> None: ...

    def authorization_url(self, request: OidcAuthorizationRequest) -> str: ...

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OidcTokenResponse: ...

    def validate_id_token(
        self,
        id_token: str,
        *,
        expected_audience: str,
    ) -> ValidatedExternalIdentity: ...

    def end_session_url(
        self,
        *,
        post_logout_redirect_uri: str,
        logout_hint: str | None = None,
    ) -> str | None: ...

    def identity_status(self, *, issuer: str, subject: str) -> str:
        """Return a status using an adapter-enforced finite I/O timeout."""
        ...


class DeterministicFakeCustomerIdentityProvider:
    def __init__(
        self,
        *,
        issuer: str = "https://fake-idp.example.test",
        client_id: str = "ai-scdc-test",
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self._end_session_endpoint: str | None = f"{self.issuer}/logout"
        self._code_sequence = 0
        self._codes: dict[str, dict[str, object]] = {}
        self._id_tokens: dict[str, dict[str, object]] = {}
        self._identity_statuses: dict[tuple[str, str], str] = {}
        self._authentication_requests: dict[
            str,
            tuple[datetime, str],
        ] = {}
        self._unavailable = False
        self._unavailable_operations: set[str] = set()
        self._failed_operations: set[str] = set()
        self._unexpected_failure_operations: set[str] = set()
        self._identity_status_query_count = 0
        self._identity_status_query_condition = Condition()
        self._identity_status_lock = Lock()
        self._block_next_identity_status_query = False
        self._identity_status_query_started = Event()
        self._identity_status_query_release = Event()
        self._identity_status_query_release.set()
        self._exchange_lock = Lock()
        self._exchange_started = Event()
        self._exchange_initial_delay = 0.0
        self._exchange_replay_rejection_delay = 0.0

    def discover(self) -> OidcDiscovery:
        self._require_available("discovery")
        return OidcDiscovery(
            issuer=self.issuer,
            authorization_endpoint=f"{self.issuer}/authorize",
            token_endpoint=f"{self.issuer}/token",
            end_session_endpoint=self._end_session_endpoint,
        )

    def check_availability(self) -> None:
        self._require_available("discovery")

    def authorization_url(self, request: OidcAuthorizationRequest) -> str:
        self._require_available("authorization")
        query_parameters: dict[str, str | int] = {
            "response_type": "code",
            "client_id": request.client_id,
            "redirect_uri": request.redirect_uri,
            "scope": "openid email",
            "state": request.state,
            "nonce": request.nonce,
            "code_challenge": request.code_challenge,
            "code_challenge_method": "S256",
        }
        if request.prompt is not None:
            query_parameters["prompt"] = request.prompt
        if request.max_age_seconds is not None:
            query_parameters["max_age"] = request.max_age_seconds
        if request.acr_values is not None:
            query_parameters["acr_values"] = request.acr_values
        if (
            request.authentication_requested_at is not None
            and request.acr_values is not None
        ):
            self._authentication_requests[request.state] = (
                request.authentication_requested_at,
                request.acr_values,
            )
        query = urlencode(query_parameters)
        return f"{self.discover().authorization_endpoint}?{query}"

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OidcTokenResponse:
        self._require_available("exchange")
        self._exchange_started.set()
        if self._exchange_initial_delay:
            sleep(self._exchange_initial_delay)
        replayed = False
        with self._exchange_lock:
            record = self._codes.get(code)
            if record is None:
                raise CustomerIdentityProviderError("Authorization code is not valid")
            if record["used"]:
                replayed = True
            else:
                if redirect_uri != record["redirect_uri"]:
                    raise CustomerIdentityProviderError(
                        "Authorization code is not valid"
                    )
                challenge = _pkce_s256(code_verifier)
                if (
                    not record["pkce_valid"]
                    or challenge != record["code_challenge"]
                ):
                    raise CustomerIdentityProviderError("PKCE validation failed")
                record["used"] = True
        if replayed:
            if self._exchange_replay_rejection_delay:
                sleep(self._exchange_replay_rejection_delay)
            raise CustomerIdentityProviderError("Authorization code is not valid")
        id_token = f"fake-id-token-{code}"
        self._id_tokens[id_token] = record
        return OidcTokenResponse(
            id_token=id_token,
            access_token=f"fake-access-token-{code}",
            logout_hint=(
                str(record["logout_hint"])
                if record["logout_hint"] is not None
                else None
            ),
        )

    def validate_id_token(
        self,
        id_token: str,
        *,
        expected_audience: str,
    ) -> ValidatedExternalIdentity:
        self._require_available("validation")
        record = self._id_tokens.get(id_token)
        if (
            record is None
            or not record["token_valid"]
            or record["audience"] != expected_audience
        ):
            raise CustomerIdentityProviderError("ID token is not valid")
        return ValidatedExternalIdentity(
            issuer=str(record["issuer"]),
            subject=str(record["subject"]),
            email=str(record["email"]) if record["email"] is not None else None,
            nonce=str(record["nonce"]),
            authenticated_at=(
                record["authenticated_at"]
                if isinstance(record["authenticated_at"], datetime)
                else None
            ),
            authentication_context=(
                str(record["authentication_context"])
                if record["authentication_context"] is not None
                else None
            ),
        )

    def end_session_url(
        self,
        *,
        post_logout_redirect_uri: str,
        logout_hint: str | None = None,
    ) -> str | None:
        self._require_available("end_session")
        endpoint = self.discover().end_session_endpoint
        if endpoint is None:
            return None
        return f"{endpoint}?{urlencode({'post_logout_redirect_uri': post_logout_redirect_uri})}"

    def identity_status(self, *, issuer: str, subject: str) -> str:
        self._require_available("status")
        with self._identity_status_lock:
            try:
                identity_status = self._identity_statuses[(issuer, subject)]
            except KeyError as exc:
                raise CustomerIdentityProviderError(
                    "Identity is not known"
                ) from exc
            should_block = self._block_next_identity_status_query
            if should_block:
                self._block_next_identity_status_query = False
        with self._identity_status_query_condition:
            self._identity_status_query_count += 1
            self._identity_status_query_condition.notify_all()
        if should_block:
            self._identity_status_query_started.set()
            if not self._identity_status_query_release.wait(timeout=5.0):
                raise CustomerIdentityProviderUnavailable(
                    "The fake identity status query timed out"
                )
        return identity_status

    def issue_authorization_code(
        self,
        authorization_url: str,
        *,
        subject: str,
        email: str | None,
        identity_status: str = "active",
        nonce: str | None = None,
        audience: str | None = None,
        pkce_valid: bool = True,
        token_valid: bool = True,
        authenticated_at: datetime | None = None,
        authentication_context: str | None = None,
        satisfy_requested_authentication: bool = True,
        logout_hint: str | None = None,
    ) -> str:
        parsed = urlparse(authorization_url)
        expected = urlparse(self.discover().authorization_endpoint)
        if (parsed.scheme, parsed.netloc, parsed.path) != (
            expected.scheme,
            expected.netloc,
            expected.path,
        ):
            raise ValueError("Authorization URL does not target the fake provider")
        query = parse_qs(parsed.query)
        requested_authentication = self._authentication_requests.get(
            query["state"][0]
        )
        if (
            satisfy_requested_authentication
            and requested_authentication is not None
        ):
            if authenticated_at is None:
                authenticated_at = requested_authentication[0]
            if authentication_context is None:
                authentication_context = requested_authentication[1]
        self._code_sequence += 1
        code = f"fake-code-{self._code_sequence}"
        record: dict[str, object] = {
            "issuer": self.issuer,
            "subject": subject,
            "email": email,
            "nonce": nonce if nonce is not None else query["nonce"][0],
            "audience": audience if audience is not None else query["client_id"][0],
            "redirect_uri": query["redirect_uri"][0],
            "code_challenge": query["code_challenge"][0],
            "pkce_valid": pkce_valid,
            "token_valid": token_valid,
            "authenticated_at": authenticated_at,
            "authentication_context": authentication_context,
            "logout_hint": logout_hint,
            "used": False,
        }
        self._codes[code] = record
        with self._identity_status_lock:
            self._identity_statuses[(self.issuer, subject)] = identity_status
        return code

    def set_unavailable(self, unavailable: bool = True) -> None:
        self._unavailable = unavailable

    def set_unavailable_for(self, *operations: str) -> None:
        self._unavailable_operations.update(operations)

    def set_failure_for(self, *operations: str) -> None:
        self._failed_operations.update(operations)

    def set_unexpected_failure_for(self, *operations: str) -> None:
        self._unexpected_failure_operations.update(operations)

    def clear_unexpected_failure_for(self, *operations: str) -> None:
        self._unexpected_failure_operations.difference_update(operations)

    def set_identity_status(
        self,
        *,
        issuer: str,
        subject: str,
        identity_status: str,
    ) -> None:
        with self._identity_status_lock:
            self._identity_statuses[(issuer, subject)] = identity_status

    def identity_status_query_count(self) -> int:
        with self._identity_status_query_condition:
            return self._identity_status_query_count

    def block_next_identity_status_query(self) -> None:
        with self._identity_status_lock:
            self._identity_status_query_started.clear()
            self._identity_status_query_release.clear()
            self._block_next_identity_status_query = True

    def release_blocked_identity_status_query(self) -> None:
        self._identity_status_query_release.set()

    def wait_for_identity_status_queries(
        self,
        *,
        minimum: int,
        timeout: float,
    ) -> bool:
        with self._identity_status_query_condition:
            return self._identity_status_query_condition.wait_for(
                lambda: self._identity_status_query_count >= minimum,
                timeout=timeout,
            )

    def set_end_session_endpoint(self, endpoint: str | None) -> None:
        self._end_session_endpoint = endpoint

    def set_exchange_delays(
        self,
        *,
        initial: float = 0.0,
        replay_rejection: float = 0.0,
    ) -> None:
        self._exchange_started.clear()
        self._exchange_initial_delay = initial
        self._exchange_replay_rejection_delay = replay_rejection

    def wait_for_exchange_started(self, *, timeout: float) -> bool:
        return self._exchange_started.wait(timeout)

    def _require_available(self, operation: str) -> None:
        if operation in self._unexpected_failure_operations:
            raise RuntimeError(
                "The fake Customer Identity Provider crashed unexpectedly"
            )
        if self._unavailable or operation in self._unavailable_operations:
            raise CustomerIdentityProviderUnavailable(
                "The fake Customer Identity Provider is unavailable"
            )
        if operation in self._failed_operations:
            raise CustomerIdentityProviderError(
                "The fake Customer Identity Provider rejected the operation"
            )


def _pkce_s256(code_verifier: str) -> str:
    digest = sha256(code_verifier.encode("ascii")).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
