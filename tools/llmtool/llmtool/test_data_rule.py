"""Owner #763 A test-data rule carried by every llmtool Developer route prompt (#1197)."""

PART_1 = ('TEST DATA RULE (Owner #763 A): ordinary tests never contain password-, '
          'API-key-, token- or other credential-shaped strings. Refusal-flow tests mock, '
          'monkeypatch or inject the screen function and still check real behaviour.')
PART_2 = ('Only sensitive-detector tests may use the #981 synthetic-data exception: '
          'labelled synthetic, independently reviewed, never real credentials. Never '
          'disable, bypass or weaken a production check for test convenience. If the work '
          'cannot meet this rule, return BLOCKED.')
TEST_DATA_RULE = PART_1 + ' ' + PART_2
