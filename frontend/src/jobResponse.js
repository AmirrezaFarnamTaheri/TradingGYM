/** Keep Axios envelopes explicit and refuse a success with no usable identity. */
export function unwrapJobResponse(response, requireJobKey = true) {
  const payload = response?.data;
  if (!Number.isInteger(payload?.pid) || payload.pid <= 0 ||
      (requireJobKey && (typeof payload.job_key !== 'string' || !payload.job_key.trim()))) {
    throw new Error('The server did not return a usable job identity. Check status before retrying.');
  }
  return payload;
}
