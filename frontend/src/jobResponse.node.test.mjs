import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
const source = await readFile(new URL('./jobResponse.js', import.meta.url), 'utf8');
const { unwrapJobResponse } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('reads identity from a real Axios-shaped response', () => {
  assert.deepEqual(unwrapJobResponse({ status: 200, data: { job_key: 'run-1', pid: 23 } }), { job_key: 'run-1', pid: 23 });
});
test('rejects unwrapped test fakes and incomplete successful responses', () => {
  for (const response of [{ job_key: 'run-1', pid: 23 }, { data: {} }, { data: { pid: -1 } }, { data: { job_key: '', pid: 23 } }]) {
    assert.throws(() => unwrapJobResponse(response), /usable job identity/);
  }
  assert.equal(unwrapJobResponse({ data: { pid: 24 } }, false).pid, 24);
});
