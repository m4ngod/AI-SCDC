# Use server-managed Web Console sessions

The Web Console will use a server-managed User Session referenced by an opaque `HttpOnly` and `Secure` cookie. OIDC authorization-code exchange and identity-provider tokens remain on the server rather than being exposed to browser JavaScript, allowing AI-SCDC to revoke sessions centrally and to resolve current workspace authority on each request.
