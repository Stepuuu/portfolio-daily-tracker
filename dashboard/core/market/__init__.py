"""Live market data for the daily desk.

``instruments``  exchange-qualified tickers and their per-source symbols
``quotes``       ``LiveQuote``, ``QuoteSource`` and the caching ``QuoteService``
``calendar``     trading sessions in exchange-local time
``valuation``    pure Decimal valuation of a ledger state
``runtime``      the lazily built process-wide service
``today``        the ``/api/today`` and ``/api/quotes`` payloads and their contract

Nothing here places, routes or prepares orders.
"""
