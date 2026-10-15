/**
 * AuthzPanel Component
 *
 * Wildcard capability grant viewer (ADR-028): list active grants, check a
 * scope, revoke. Revocation is terminal and latches — the panel says so.
 *
 * @component
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { authzAPI } from '../api';
import { useToast } from './Toast';

export default function AuthzPanel() {
  const [actor, setActor] = useState('');
  const [actorKey, setActorKey] = useState('');
  const [checkScope, setCheckScope] = useState('trade.paper.order.submit');
  const [checkPrincipal, setCheckPrincipal] = useState('');
  const [checkResult, setCheckResult] = useState(null);
  const [pendingRevoke, setPendingRevoke] = useState(null);
  const queryClient = useQueryClient();
  const toast = useToast();

  const grantsQuery = useQuery({
    queryKey: ['authz-grants'],
    queryFn: async () => {
      const payload = (await authzAPI.listGrants()).data;
      if (!Array.isArray(payload?.grants)) {
        throw new Error('Invalid grants response');
      }
      return payload;
    },
    refetchInterval: 15000,
  });

  const healthQuery = useQuery({
    queryKey: ['authz-health'],
    queryFn: () => authzAPI.health().then((response) => response.data),
    refetchInterval: 30000,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['authz-grants'] });
    queryClient.invalidateQueries({ queryKey: ['authz-health'] });
  };

  const revokeMutation = useMutation({
    mutationFn: ({ grantId }) => authzAPI.revoke(grantId, actor, actorKey),
    onSuccess: () => {
      toast.success('Grant revoked (terminal)');
      setPendingRevoke(null);
      // Previously checked scopes may have lost permission with this
      // revocation. Never leave their old ALLOWED verdict on screen.
      setCheckResult(null);
      refresh();
    },
    onError: (error) => toast.error(error.message || 'Revocation failed'),
  });

  const checkMutation = useMutation({
    mutationFn: async ({ scope, principal }) => {
      const response = await authzAPI.check(scope, principal || undefined);
      if (typeof response.data?.allowed !== 'boolean') {
        throw new Error('Invalid scope-check response');
      }
      return response.data;
    },
    onSuccess: (result, variables) => {
      // Bind the verdict to the inputs used when the request was sent,
      // not whatever scope/principal is now displayed by the operator.
      setCheckResult({ ...result, ...variables });
    },
    onError: (error) => {
      setCheckResult(null);
      toast.error(error.message || 'Check failed');
    },
  });

  const grants = grantsQuery.data?.grants ?? [];
  // An absent or failed health response is not proof that the grant ledger
  // is valid. Only an affirmative response may be displayed as healthy.
  const legacyWildcardCount = healthQuery.data?.legacy_high_risk_wildcard_grants;
  const legacyStatusKnown =
    Number.isSafeInteger(legacyWildcardCount) && legacyWildcardCount >= 0;
  const ledgerState = healthQuery.isPending
    ? 'checking'
    : healthQuery.isError
      ? 'UNAVAILABLE'
      : healthQuery.data?.ledger_valid === true
        ? 'valid'
        : healthQuery.data?.ledger_valid === false
          ? 'CORRUPT'
          : 'UNAVAILABLE';

  return (
    <section className="panel authz-panel" aria-label="Authorization grants">
      <h2>Authorization Grants</h2>
      <p className="panel-hint">
        Wildcard capability grants delegate bounded authority to principals.
        Ledger:{' '}
        <strong data-testid="ledger-state">
          {ledgerState}
        </strong>{' '}
        ({healthQuery.data?.entries ?? 0} entries). Revocation is terminal.
      </p>

      {ledgerState === 'valid' && !legacyStatusKnown && (
        <div className="error" role="alert" data-testid="legacy-grants-unknown">
          Legacy economic wildcard-grant review is unavailable. Verify the
          current authorization service before funded execution.
        </div>
      )}
      {ledgerState === 'valid' && legacyStatusKnown && legacyWildcardCount > 0 && (
        <div className="error" role="alert" data-testid="legacy-grants-warning">
          {legacyWildcardCount} legacy high-risk wildcard grant(s) require
          operator review and explicit revocation before funded execution.
        </div>
      )}

      <form
        className="credential-row"
        onSubmit={(event) => event.preventDefault()}
        aria-label="Actor credentials for mutations"
      >
        <input
          value={actor}
          onChange={(event) => setActor(event.target.value)}
          placeholder="X-Actor (e.g. operator-1)"
          aria-label="Actor principal"
        />
        <input
          type="password"
          value={actorKey}
          onChange={(event) => setActorKey(event.target.value)}
          placeholder="X-Actor-Key"
          aria-label="Actor key"
        />
      </form>

      <form
        className="check-row"
        onSubmit={(event) => {
          event.preventDefault();
          setCheckResult(null);
          checkMutation.mutate({ scope: checkScope, principal: checkPrincipal });
        }}
        aria-label="Scope check"
      >
        <input
          value={checkPrincipal}
          onChange={(event) => setCheckPrincipal(event.target.value)}
          placeholder="principal (optional; defaults to caller)"
          aria-label="Check principal"
        />
        <input
          value={checkScope}
          onChange={(event) => setCheckScope(event.target.value)}
          placeholder="scope.to.check"
          aria-label="Check scope"
        />
        <button type="submit" disabled={checkMutation.isPending}>
          Check
        </button>
        {checkResult
          && checkResult.scope === checkScope
          && checkResult.principal === checkPrincipal && (
          <span
            className={`check-result ${checkResult.allowed ? 'allowed' : 'denied'}`}
            data-testid="check-result"
          >
            {checkResult.allowed ? 'ALLOWED' : 'DENIED'} ({checkResult.reason})
          </span>
        )}
      </form>

      {grantsQuery.isLoading ? (
        <p>Loading grants…</p>
      ) : grantsQuery.isError ? (
        // A failed fetch must not masquerade as an empty grant set: this
        // branch is red and actionable, the empty state below is not.
        <div className="error" role="alert" aria-live="assertive">
          Failed to load grants: {grantsQuery.error?.message || 'Unknown error'}
          <div style={{ marginTop: '10px' }}>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => grantsQuery.refetch()}
            >
              Retry
            </button>
          </div>
        </div>
      ) : grants.length === 0 ? (
        <p className="empty-state">No active grants.</p>
      ) : (
        <table className="grants-table">
          <thead>
            <tr>
              <th scope="col">Grant</th>
              <th scope="col">Principal</th>
              <th scope="col">Scopes</th>
              <th scope="col">Expires</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {grants.map((grant) => (
              <tr key={grant.grant_id}>
                <td>
                  <code>{grant.grant_id}</code>
                </td>
                <td>{grant.principal}</td>
                <td>
                  {Array.isArray(grant.scopes)
                    ? grant.scopes.map((scope) => (
                        <code key={scope} className="scope-chip">
                          {scope}
                        </code>
                      ))
                    : grant.scopes}
                </td>
                <td>{grant.expires_at}</td>
                <td>
                  {pendingRevoke === grant.grant_id ? (
                    <div className="button-group" role="group"
                      aria-label={`Confirm revocation of grant ${grant.grant_id}`}>
                      <span role="alert">
                        Terminal revocation of {grant.grant_id}
                      </span>
                      <button
                        type="button" className="btn btn-danger btn-small"
                        disabled={!actor || !actorKey || revokeMutation.isPending}
                        onClick={() => revokeMutation.mutate({ grantId: grant.grant_id })}
                      >
                        Confirm revoke
                      </button>
                      <button
                        type="button" className="btn btn-secondary btn-small"
                        disabled={revokeMutation.isPending}
                        onClick={() => setPendingRevoke(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      disabled={!actor || !actorKey || revokeMutation.isPending}
                      onClick={() => setPendingRevoke(grant.grant_id)}
                      aria-label={`Revoke grant ${grant.grant_id}`}
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
