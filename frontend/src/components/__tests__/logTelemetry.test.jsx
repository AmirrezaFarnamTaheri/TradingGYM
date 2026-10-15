import { describe, expect, it } from 'vitest';
import { parseTrainingLogEvent } from '../../logTelemetry';

describe('bounded training telemetry', () => {
  it('normalizes severity and discards unrelated payload fields', () => {
    expect(parseTrainingLogEvent(JSON.stringify({
      level: 'info', source: 'TRAIN', message: 'progress',
      credential: 'DO_NOT_STORE', sequence: 12,
    }))).toEqual({ level: 'INFO', source: 'TRAIN', message: 'progress' });
  });

  it('redacts credential labels and bearer tokens before display', () => {
    const log = parseTrainingLogEvent(JSON.stringify({
      level: 'ERROR', source: 'PROVIDER',
      message: 'api_key=unsafe-test-token password: supersecret Authorization: Bearer eyTestToken',
    }));
    expect(log.message).toContain('[REDACTED]');
    expect(log.message).not.toContain('unsafe-test-token');
    expect(log.message).not.toContain('supersecret');
    expect(log.message).not.toContain('eyTestToken');
  });

  it('rejects malformed, unsafe and oversized frames', () => {
    for (const raw of [
      '{invalid', 42, 'x'.repeat(16385),
      JSON.stringify({ level: 'WARN', message: 'no source' }),
      JSON.stringify({ level: 'NOTICE injection', source: 'TRAIN', message: 'x' }),
      JSON.stringify({ level: 'INFO', source: '../unsafe', message: 'x' }),
      JSON.stringify({ level: 'INFO', source: 'TRAIN', message: 'x'.repeat(4097) }),
      JSON.stringify({ level: 'INFO', source: 'TRAIN', message: 42 }),
    ]) {
      expect(parseTrainingLogEvent(raw)).toBeNull();
    }
  });
});
