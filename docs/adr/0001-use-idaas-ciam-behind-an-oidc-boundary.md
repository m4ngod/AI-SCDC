# Use IDaaS CIAM behind an OIDC boundary

For the initial mainland-China self-service release, AI-SCDC will use Alibaba Cloud IDaaS CIAM as its first human identity provider because it serves external customer identity and aligns with the existing Alibaba Cloud operating environment. AI-SCDC will depend on standard OpenID Connect discovery, authorization, token, and identity claims rather than proprietary login APIs so that the provider can be replaced without changing the platform's User or workspace-authorization model.
