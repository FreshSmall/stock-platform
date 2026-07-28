"""FastAPI routers (HTTP layer).

Routers here are intentionally thin: they parse request params, delegate to
the service layer, and wrap the result in the unified response envelope. No
business logic belongs in this package.
"""
