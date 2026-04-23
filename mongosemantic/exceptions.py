class MongoSemanticError(Exception):
    """Base exception for mongosemantic."""

class ConfigError(MongoSemanticError):
    """Bad configuration or missing env vars."""

class ProviderError(MongoSemanticError):
    """Embedding provider failure."""

class DimMismatchError(ProviderError):
    """Embedding returned has wrong dimension."""

class TopologyError(MongoSemanticError):
    """Connected cluster doesn't support a required feature."""

class NotConfiguredError(MongoSemanticError):
    """Operation requires apply() to have been run."""
