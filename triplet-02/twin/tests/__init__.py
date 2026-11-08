"""Person A local tests; no external systems required."""
import os

# Tests may use the documented local-development tokens. Production deployments
# must configure TWIN_TOKEN_<ROLE> environment variables instead.
os.environ.setdefault("TWIN_ALLOW_INSECURE_DEV_TOKENS", "1")
