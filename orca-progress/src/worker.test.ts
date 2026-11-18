import { assert, test } from 'orca/testing'
import { summarize } from './worker'
test('does not turn empty checks into passes', () => {
  const result = summarize('test', { oracle: true, rows: [{ checks: [] }, { checks: [['x', true, '']] }, { checks: [['x', false, '']] }] }, 'now')
  assert.equal(result.passed, 1)
  assert.equal(result.failed, 1)
  assert.equal(result.unchecked, 1)
})
test('rejects malformed results instead of reporting success', () => {
  assert.throws(() => summarize('bad', { oracle: true, rows: [{ checks: [['x', 'false']] }] }, 'now'))
})
