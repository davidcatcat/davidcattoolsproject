"""Errors safe to display without including input, credentials, or API bodies."""


class LLMToolError(Exception):
    """User-facing diagnostic containing only application-controlled text."""
