"""Service layer (use-case orchestration).

Service modules are intentionally FastAPI-agnostic: they take a SQLAlchemy
``Session`` and return plain Python objects (ORM rows, dicts, primitives). This
keeps them trivially unit/integration-testable without spinning up the HTTP
stack.
"""
