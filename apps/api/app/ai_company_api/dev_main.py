from importlib import import_module
import os

from ai_company_api.services.auth_policy import (
    AUTHENTICATION_ENVIRONMENT_ENV,
    AuthenticationEnvironment,
)


os.environ[AUTHENTICATION_ENVIRONMENT_ENV] = AuthenticationEnvironment.LOCAL.value

app = import_module("ai_company_api.main").app


__all__ = ["app"]
