from dataclasses import dataclass
from enum import StrEnum


class AuthenticationEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class HumanCredentialType(StrEnum):
    DEV_AUTH = "dev"
    WORKSPACE_API_TOKEN = "api_token"


AUTHENTICATION_ENVIRONMENT_ENV = "AI_SCDC_AUTHENTICATION_ENVIRONMENT"


@dataclass(frozen=True)
class AuthenticationPolicy:
    environment: AuthenticationEnvironment
    accepted_human_credentials: frozenset[HumanCredentialType]

    def __post_init__(self) -> None:
        try:
            environment = AuthenticationEnvironment(self.environment)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported authentication environment: {self.environment}"
            ) from exc
        object.__setattr__(self, "environment", environment)
        accepted_human_credentials: set[HumanCredentialType] = set()
        for credential in self.accepted_human_credentials:
            try:
                accepted_human_credentials.add(HumanCredentialType(credential))
            except ValueError as exc:
                raise ValueError(
                    f"Unsupported human credential: {credential}"
                ) from exc
        normalized_credentials = frozenset(accepted_human_credentials)
        object.__setattr__(
            self,
            "accepted_human_credentials",
            normalized_credentials,
        )
        if not normalized_credentials:
            raise ValueError(
                "Authentication policy must accept at least one human credential"
            )
        if (
            HumanCredentialType.DEV_AUTH in normalized_credentials
            and environment
            not in {
                AuthenticationEnvironment.LOCAL,
                AuthenticationEnvironment.TEST,
            }
        ):
            raise ValueError(
                "Dev Auth is allowed only in local or test environments"
            )


def authentication_policy_for_environment(
    environment_value: str | None,
) -> AuthenticationPolicy:
    if environment_value is None or environment_value.strip() == "":
        raise ValueError(
            f"{AUTHENTICATION_ENVIRONMENT_ENV} must explicitly select an "
            "authentication environment"
        )
    try:
        environment = AuthenticationEnvironment(environment_value.strip())
    except ValueError as exc:
        raise ValueError(
            f"Unsupported authentication environment: {environment_value}"
        ) from exc
    credential_type = (
        HumanCredentialType.DEV_AUTH
        if environment
        in {
            AuthenticationEnvironment.LOCAL,
            AuthenticationEnvironment.TEST,
        }
        else HumanCredentialType.WORKSPACE_API_TOKEN
    )
    return AuthenticationPolicy(
        environment=environment,
        accepted_human_credentials=frozenset({credential_type}),
    )
