from dataclasses import dataclass
from base64 import urlsafe_b64encode
from hashlib import sha256
from threading import Event, Lock
from time import sleep
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlparse


class CustomerIdentityProviderError(RuntimeError):
    pass


class CustomerIdentityProviderUnavailable(CustomerIdentityProviderError):
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


@dataclass(frozen=True)
class OidcTokenResponse:
    id_token: str
    access_token: str


@dataclass(frozen=True)
class ValidatedExternalIdentity:
    issuer: str
    subject: str
    email: str | None
    nonce: str


class CustomerIdentityProvider(Protocol):
    client_id: str

    def discover(self) -> OidcDiscovery: ...

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

    def end_session_url(self, *, post_logout_redirect_uri: str) -> str | None: ...

    def identity_status(self, *, issuer: str, subject: str) -> str: ...


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
        self._unavailable = False
        self._unavailable_operations: set[str] = set()
        self._failed_operations: set[str] = set()
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

    def authorization_url(self, request: OidcAuthorizationRequest) -> str:
        self._require_available("authorization")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": request.client_id,
                "redirect_uri": request.redirect_uri,
                "scope": "openid email",
                "state": request.state,
                "nonce": request.nonce,
                "code_challenge": request.code_challenge,
                "code_challenge_method": "S256",
            }
        )
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
        )

    def end_session_url(self, *, post_logout_redirect_uri: str) -> str | None:
        self._require_available("end_session")
        endpoint = self.discover().end_session_endpoint
        if endpoint is None:
            return None
        return f"{endpoint}?{urlencode({'post_logout_redirect_uri': post_logout_redirect_uri})}"

    def identity_status(self, *, issuer: str, subject: str) -> str:
        self._require_available("status")
        try:
            return self._identity_statuses[(issuer, subject)]
        except KeyError as exc:
            raise CustomerIdentityProviderError("Identity is not known") from exc

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
            "used": False,
        }
        self._codes[code] = record
        self._identity_statuses[(self.issuer, subject)] = identity_status
        return code

    def set_unavailable(self, unavailable: bool = True) -> None:
        self._unavailable = unavailable

    def set_unavailable_for(self, *operations: str) -> None:
        self._unavailable_operations.update(operations)

    def set_failure_for(self, *operations: str) -> None:
        self._failed_operations.update(operations)

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
