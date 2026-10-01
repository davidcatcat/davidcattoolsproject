"""Errors safe to display without including input, credentials, or API bodies."""


class LLMToolError(Exception):
    """Legacy safe display text, optionally backed by one typed diagnostic value.

    Untouched positional constructors retain Exception's args/str semantics.
    Coded callers use LLMToolError(diagnostic=DiagnosticError(...)); no message
    duplication or implicit classification of legacy strings is performed.
    """

    def __init__(self, *args, diagnostic=None):
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
