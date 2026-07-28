"""Data ingestion layer.

Thin, mockable boundary around external data sources (AkShare), plus
validation and idempotent UPSERT sync into the ``stock_analysis`` schema.
"""
