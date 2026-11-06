"""Exception hierarchy used across the service."""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for all service errors."""


class ConfigError(ServiceError):
    """The configuration is missing, malformed or unsafe."""


class OrderValidationError(ServiceError):
    """An order request is malformed or cannot be normalised."""


class NotFoundError(ServiceError):
    """A requested object does not exist."""


class ConflictError(ServiceError):
    """The operation is not allowed in the current state."""


class BrokerError(ServiceError):
    """Base class for broker adapter errors."""


class BrokerUnavailable(BrokerError):
    """The broker client cannot be reached or is not supported here."""


class PermanentBrokerError(BrokerError):
    """The broker definitively rejected the order; nothing was placed."""


class TransientBrokerError(BrokerError):
    """A read-only broker call failed transiently and may be retried."""


class UnknownSubmitOutcome(BrokerError):
    """The order submission raised after it may already have reached the broker.

    The service must not retry such an order automatically, because doing so can
    create a duplicate real-money order.
    """


class InsufficientFunds(PermanentBrokerError):
    """Paper account does not have enough cash."""


class InsufficientPosition(PermanentBrokerError):
    """Paper account does not have enough sellable shares."""
