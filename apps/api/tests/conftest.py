from collections.abc import Iterator
import os

import pytest

from ai_company_api.models.entities import WorkspaceRole
from ai_company_api.services.auth_context import (
    DEV_AUTH_MODE,
    DEV_ORGANIZATION_ID,
    DEV_USER_ID,
    DEV_WORKSPACE_ID,
    AuthContext,
    auth_context_scope,
)
from ai_company_api.services.auth_policy import (
    AUTHENTICATION_ENVIRONMENT_ENV,
    AuthenticationEnvironment,
)


os.environ[AUTHENTICATION_ENVIRONMENT_ENV] = AuthenticationEnvironment.TEST.value


@pytest.fixture
def explicit_dev_auth_context() -> Iterator[None]:
    context = AuthContext(
        user_id=DEV_USER_ID,
        workspace_id=DEV_WORKSPACE_ID,
        organization_id=DEV_ORGANIZATION_ID,
        roles=frozenset({WorkspaceRole.OWNER}),
        auth_mode=DEV_AUTH_MODE,
    )
    with auth_context_scope(context):
        yield
