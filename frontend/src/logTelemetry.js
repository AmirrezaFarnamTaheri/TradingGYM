"""Pure browser telemetry guard; no network or authority side effects."""

const VALID_LEVELS = new Set([
  'DEBUG', 'INFO', 'WARNING', 'WARN', 'ERROR', 'CRITICAL', 'SUCCESS',
]);
const SOURCE_PATTERN = /^[A-Za-z0-9_.:-]{1,80}$/;
const MAX_FRAME_CHARACTERS = 16 * 1024;
const MAX_MESSAGE_CHARACTERS = 4096;

function redactCredentials(message) {
  const withoutBearer = message.replace(
    /\bBearer\s+[A-Za-z0-9._~+/-]{4,}=*/gi,
    'Bearer [REDACTED]'
  );
  return withoutBearer.replace(
    /(\b(?:api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|secret|password|authorization)\b["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;]+)/gi,
    '$1[REDACTED]'
  );
}

export function parseTrainingLogEvent(raw) {
  // Even valid JSON is untrusted telemetry. Do not retain opaque metadata,
  // imported HTML, tool credentials, or unbounded strings in the UI store.
  if (typeof raw !== 'string' || raw.length > MAX_FRAME_CHARACTERS) {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (
    !parsed ||
    typeof parsed !== 'object' ||
    Array.isArray(parsed) ||
    typeof parsed.level !== 'string' ||
    typeof parsed.source !== 'string' ||
    typeof parsed.message !== 'string' ||
    parsed.message.length > MAX_MESSAGE_CHARACTERS ||
    !SOURCE_PATTERN.test(parsed.source)
  ) {
    return null;
  }
  const level = parsed.level.toUpperCase();
  if (!VALID_LEVELS.has(level)) return null;
  return {
    level,
    source: parsed.source,
    message: redactCredentials(parsed.message),
  };
}
