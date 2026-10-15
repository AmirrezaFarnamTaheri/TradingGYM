/**
 * Read-only production authority and emergency evidence.
 *
 * The API remains the source of truth. A missing, invalid, or conflicting
 * snapshot never proves that the account is safe to trade. No funded
 * mutation, emergency reset, or credential-bearing control is exposed here.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { productionAPI, tradingAPI } from '../api';
import './ProductionPanel.css';

const statusQueryOptions = {
  retry: false,
  staleTime: 0,
  refetchInterval: 15000,
  refetchOnWindowFocus: true,
};

function isRevision(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

function resolveEmergency(authority, emergency, isUnavailable) {
  if (isUnavailable || !authority || !emergency) {
    return { state: 'unverified', label: 'Unverified', detail: 'evidence unavailable' };
  }
  if (
    typeof authority.emergency_halted !== 'boolean'
    || typeof emergency.halted !== 'boolean'
    || !isRevision(authority.emergency_revision)
    || !isRevision(emergency.revision)
  ) {
    return { state: 'unverified', label: 'Unverified', detail: 'invalid emergency snapshot' };
  }
  // A positive halt indication must never be hidden by an older or
  // contradictory authority snapshot.
  if (emergency.halted || authority.emergency_halted) {
    return { state: 'halted', label: 'Emergency halt active', detail: 'orders not authorized' };
  }
  if (emergency.revision !== authority.emergency_revision) {
    return { state: 'unverified', label: 'Unverified', detail: 'snapshots disagree' };
  }
  return { state: 'not-halted', label: 'Not halted', detail: 'not trading authorization' };
}

export default function ProductionPanel() {
  const queryClient = useQueryClient();
  const authorityQuery = useQuery({
    queryKey: ['production', 'authority'],
    queryFn: async () => (await productionAPI.getAuthority()).data,
    ...statusQueryOptions,
  });
  const emergencyQuery = useQuery({
    queryKey: ['production', 'emergency'],
    queryFn: async () => (await productionAPI.getEmergencyState()).data,
    ...statusQueryOptions,
  });
  const tradingQuery = useQuery({
    queryKey: ['trading', 'status'],
    queryFn: async () => (await tradingAPI.getStatus()).data,
    ...statusQueryOptions,
  });
  const journalQuery = useQuery({
    queryKey: ['production', 'reconciliation'],
    queryFn: async () => (await productionAPI.getReconciliation()).data,
    ...statusQueryOptions,
  });

  const authority = authorityQuery.data;
  const emergency = emergencyQuery.data;
  const emergencyEvidence = resolveEmergency(
    authority,
    emergency,
    authorityQuery.isError || emergencyQuery.isError
      || authorityQuery.isPending || emergencyQuery.isPending
  );
  const revision = (
    authority
    && emergency
    && isRevision(authority.emergency_revision)
    && authority.emergency_revision === emergency.revision
  ) ? emergency.revision : null;
  const processLabel = (
    tradingQuery.isSuccess && typeof tradingQuery.data?.status === 'string'
  ) ? tradingQuery.data.status : 'Unverified';
  const journal = journalQuery.data;
  const journalValid = journalQuery.isSuccess
    && journal
    && ['journal_clear', 'blocked'].includes(journal.state)
    && Number.isSafeInteger(journal.unresolved_total)
    && journal.unresolved_total >= 0
    && journal.can_trade === false
    && journal.authority_granted === false;
  const journalState = journalValid ? journal.state : 'unverified';
  const journalLabel = journalState === 'blocked'
    ? 'Broker reconciliation required'
    : journalState === 'journal_clear'
      ? 'Local journal clear'
      : 'Unverified';
  const anyFetching = authorityQuery.isFetching
    || emergencyQuery.isFetching || tradingQuery.isFetching
    || journalQuery.isFetching;

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['production'] });
    queryClient.invalidateQueries({ queryKey: ['trading', 'status'] });
  };

  return (
    <section className="panel production-panel" aria-label="Production Authority">
      <header className="production-header">
        <div>
          <h2>Production Authority</h2>
          <p className="muted">
            Read-only controls evidence. Funded execution requires the single production coordinator,
            separate operator authorization, and independent broker verification.
          </p>
        </div>
        <button
          type="button" className="btn btn-secondary btn-small"
          onClick={refresh} disabled={anyFetching} aria-label="Refresh production status"
        >
          {anyFetching ? 'Refreshing…' : 'Refresh status'}
        </button>
      </header>

      <div className="production-summary">
        <section className="production-tile" aria-label="Emergency control state">
          <h3>Emergency control</h3>
          <strong
            role="status"
            data-testid="production-emergency"
            className={`production-state production-state--${emergencyEvidence.state}`}
          >
            {emergencyEvidence.label}
          </strong>
          <p>{revision === null ? 'Revision unverified' : `Revision ${revision}`}</p>
          <p className="muted">{emergencyEvidence.detail}</p>
        </section>

        <section className="production-tile" aria-label="Production coordinator">
          <h3>Authority owner</h3>
          <strong className="production-coordinator">
            {typeof authority?.single_live_authority === 'string'
              && authority.single_live_authority.trim()
              ? authority.single_live_authority.split('.').at(-1)
              : 'Unverified'}
          </strong>
          <p>
            Operation-specific authorization:{' '}
            {authority?.operation_specific_authorization === true ? 'Declared' : 'Unverified'}
          </p>
          <p>
            Wallet custody:{' '}
            {authority?.wallet_custody === false ? 'Non-custodial' : 'Unverified'}
          </p>
        </section>

        <section className="production-tile" aria-label="Venue reconciliation journal">
          <h3>Mutation reconciliation</h3>
          <strong
            role="status"
            data-testid="production-reconciliation"
            className={`production-state production-state--${journalState}`}
          >
            {journalLabel}
          </strong>
          {journalValid ? (
            <p>
              {journal.unresolved_total} unresolved
              {' · '}{journal.pending} pending
              {' · '}{journal.ambiguous} ambiguous
            </p>
          ) : (
            <p>Journal evidence unavailable</p>
          )}
          <p className="muted">
            A clear local journal is not broker fill, fee or collateral proof.
          </p>
        </section>

        <section className="production-tile" aria-label="Managed trading process">
          <h3>Managed process</h3>
          <strong>{processLabel}</strong>
          <p>
            Mode:{' '}
            {tradingQuery.isSuccess && typeof tradingQuery.data?.mode === 'string'
              ? tradingQuery.data.mode
              : 'Unverified'}
          </p>
          <p className="muted">Process status does not attest exchange positions or fills.</p>
        </section>
      </div>

      {emergencyEvidence.state === 'halted' && (
        <p className="production-evidence-warning" role="alert">
          Emergency stop remains active. Do not resume funded orders.
          {typeof emergency?.reason === 'string' && emergency.reason
            ? ` Reason: ${emergency.reason.slice(0, 200)}` : ''}
        </p>
      )}
      {emergencyEvidence.state === 'unverified' && (
        <p className="production-evidence-warning" role="alert">
          Production evidence unavailable or inconsistent ({emergencyEvidence.detail}).
          A missing or stale status never authorizes funded trading.
        </p>
      )}
      {journalState === 'blocked' && (
        <p className="production-evidence-warning" role="alert">
          Unresolved broker mutations require operator reconciliation before any
          funded order. Journal status cannot be cleared by this panel.
        </p>
      )}
      {journalState === 'unverified' && (
        <p className="production-evidence-warning" role="alert">
          Journal evidence unavailable or inconsistent. Treat unknown venue
          outcomes as pending, not as rejected or settled.
        </p>
      )}
      {tradingQuery.isError && (
        <p className="production-evidence-warning" role="alert">
          Trading process status unavailable. This does not prove an idle broker account.
        </p>
      )}

      <div className="info-box production-boundary" role="note">
        <p>
          This panel is observational only. An inactive emergency marker is
          <strong> not proof of broker reconciliation</strong>, gross-flat exposure,
          settled fees, or release qualification.
        </p>
        <p>
          All funded mutations require separately verified scopes, durable
          journal reconciliation and the independent live coordinator.
        </p>
      </div>
    </section>
  );
}
