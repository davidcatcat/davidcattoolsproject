"""Errors safe to display without including input, credentials, or API bodies."""


class LLMToolError(Exception):
    """Legacy safe display text, optionally backed by one typed diagnostic value.

    Untouched positional constructors retain Exception's args/str semantics.
    Coded callers use LLMToolError(diagnostic=DiagnosticError(...)); no message
    duplication or implicit classification of legacy strings is performed.
    Coded diagnostics transfer via bounded JSON; pickle/deepcopy are unsupported.
    Legacy uncoded exception pickle behavior is preserved.
    """

    def __init__(self, *args, diagnostic=None, provider_metadata=None):
        if diagnostic is not None:
            # Lazy import keeps every uncoded legacy path unchanged and avoids
            # pulling a diagnostics framework into provider/config initialization.
            from .diagnostics import DiagnosticError
            if type(diagnostic) is not DiagnosticError:
                raise TypeError('LLMToolError requires a typed DiagnosticError')
            if args:
                raise TypeError('coded LLMToolError takes its message from the diagnostic')
            args = (diagnostic.message,)
        super().__init__(*args)
        self.diagnostic = diagnostic
        self._provider_metadata = None
        if provider_metadata is not None:
            try:
                from .diagnostics import validate_provider_usage
                self._provider_metadata = validate_provider_usage(provider_metadata)
            except Exception:
                pass  # Secondary metadata cannot replace the original task error.

    @property
    def provider_metadata(self):
        """Validated immutable usage triplet, or None when unavailable.

        Never part of args/str/repr or DiagnosticError's closed error schema.
        """
        return self._provider_metadata


def coded_error(message, code, component, *, retryable=False, user_action_required=False,
                safe_cause=None, provider_metadata=None):
    """Add optional diagnostics at a known failure site without losing legacy text.

    Construction is secondary: if it fails, the original task error still wins.
    The ask observer accounts for that drop; uninstrumented callers keep str/args.
    """
    exc = LLMToolError(message, provider_metadata=provider_metadata)
    fixed_code = None
    try:
        from .diagnostics import DiagnosticError, ErrorCode
        fixed_code = ErrorCode(code)
        exc.diagnostic = DiagnosticError(
            code=fixed_code, message=message, component=component,
            retryable=retryable, user_action_required=user_action_required,
            safe_cause={} if safe_cause is None else safe_cause,
        )
    except Exception:
        exc.diagnostic_dropped = True
        if fixed_code is not None:
            # Only on construction failure: keep the validated site classification.
            exc.diagnostic_code = fixed_code
    return exc


def http_error_code(status):
    """Classify numeric transport evidence, never exception message text."""
    if status in (401, 403):
        return 'AUTH_PROVIDER_REJECTED'
    if status == 429:
        return 'PROVIDER_RATE_LIMITED'
    return 'PROVIDER_HTTP_ERROR'
