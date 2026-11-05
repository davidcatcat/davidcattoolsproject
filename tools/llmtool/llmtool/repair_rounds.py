"""Classify Developer assignments that provably did no repair work."""


_NO_WORK_EXECUTION_CODES = frozenset((
    'CODEX_BLOCKED',
    'CODEX_FINAL_JSON_INVALID',
    'CODEX_FINAL_MESSAGE_INVALID',
    'CODEX_FINAL_MESSAGE_MISSING',
))


def counts_as_repair_round(developer_record):
    """Return False only for the explicitly evidenced no-work cases."""
    if type(developer_record) is not dict:
        return True

    state = developer_record.get('state')
    error_code = developer_record.get('error_code')
    execution = developer_record.get('execution')

    if state == 'DEVELOPER_PENDING':
        return False
    if state == 'DEVELOPER_UNAVAILABLE':
        if developer_record.get('provider_type') == 'gw-agent-cli':
            return error_code != 'DEVELOPER_GW_AGENT_CONFIG_INVALID'
        return False
    if type(execution) is dict and execution.get('changed_files') is False:
        if type(error_code) is str and error_code in _NO_WORK_EXECUTION_CODES:
            return False
        if (state == 'DEVELOPER_OUTPUT_REJECTED' and
                error_code == 'DEVELOPER_OUTPUT_INVALID_ENVELOPE'):
            return False
    return True
