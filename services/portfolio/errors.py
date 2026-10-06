"""Errors the portfolio service raises; the router maps them to HTTP statuses."""


class UnknownTickerError(Exception):
    pass


class HoldingLimitError(Exception):
    pass


class LotLimitError(Exception):
    pass


class QuoteUnavailableError(Exception):
    """Yahoo could not be reached, so the ticker could not be validated; retryable."""


class PortfolioUnavailableError(Exception):
    """The holdings couldn't be listed or valued."""


class ScopeNotInPortfolioError(Exception):
    """A chat focus ticker the user doesn't hold."""
