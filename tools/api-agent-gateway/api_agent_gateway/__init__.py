"""API Agent Gateway: a confined, audited agent loop for API-only chat models.

Standalone by design: this package does not import llmtool, and llmtool does
not import it. The only coupling is the CLI/JSON contract (see README.md).
"""

__version__ = '0.1.0'
