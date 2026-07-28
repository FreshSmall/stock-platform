"""SQLAlchemy 2.0 engine, session factory and declarative base.

The ``stock_analysis`` MySQL database already contains a number of tables
populated by external pipelines (``daily_prices``, ``stock_pool``, ...).
These are treated as **read-only** by this service; we never write to them.
"""

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# Build the URL via URL.create so special characters in credentials
# (e.g. the ``@`` in the DB password) are URL-encoded automatically. A naive
# f-string would let such characters bleed into the host portion.
DATABASE_URL = URL.create(
    drivername="mysql+pymysql",
    username=settings.db_user,
    password=settings.db_password,
    host=settings.db_host,
    port=settings.db_port,
    database=settings.db_name,
    query={"charset": "utf8mb4"},
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    echo=False,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM mapping in the project."""
