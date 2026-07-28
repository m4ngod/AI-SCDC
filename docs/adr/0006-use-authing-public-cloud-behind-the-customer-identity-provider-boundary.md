---
status: accepted
---

# Use Authing Public Cloud behind the Customer Identity Provider boundary

AI-SCDC will use Authing Public Cloud as its first production Customer Identity Provider because it supports self-service CIAM for individual customers, while Alibaba Cloud IDaaS CIAM is currently unavailable to this project outside large-enterprise sales. Authing remains behind the provider-neutral OpenID Connect boundary so User Sessions, Accounts, Workspace authorization, and machine credentials do not depend on provider-specific identity concepts.

AI-SCDC will isolate the integration in a dedicated user pool, use public email verification-code registration and sign-in, and keep Authing organizations, roles, and authorization outside the product model. The application and user-pool credentials remain server-side production secrets; changing providers later must preserve the existing Customer Identity Provider contract and externally observable identity behavior.
