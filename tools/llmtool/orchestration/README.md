# A9 orchestration examples

These repository-local examples define explicit role-to-provider-profile mappings.
They contain no credentials, private endpoints, or implicit provider routing.

A9 is a bounded sequential text pipeline. Role output is untrusted data and is never
executed. Provider capabilities do not grant shell, Git, code-edit, or filesystem
permission. Anthropic HTTP API remains future A2.3 work; any registered provider
profile can use the same orchestration format.
