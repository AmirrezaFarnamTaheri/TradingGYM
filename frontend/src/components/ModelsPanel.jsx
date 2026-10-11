import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { modelsAPI } from '../api';
import useStore from '../store';
import BacktestModal from './BacktestModal';
import { useToast } from './Toast';

export default function ModelsPanel() {
  const [selectedModel, setSelectedModel] = useState(null);
  const [backtestModel, setBacktestModel] = useState(null);
  const [activationModel, setActivationModel] = useState(null);
  const [activationForm, setActivationForm] = useState({
    proposalId: '', expectedRevision: '', actor: '', actorKey: '', approvalKey: '',
  });
  const queryClient = useQueryClient();
  const toast = useToast();

  const activeModel = useStore(state => state.activeModel);
  const setActiveModel = useStore(state => state.setActiveModel);

  // Fetch models
  const { data: modelsData, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['models'],
    queryFn: () => modelsAPI.list().then(r => r.data),
    refetchInterval: 5000,
  });

  // Fetch active model.  TanStack Query v5 removed the useQuery onSuccess
  // option, so the store is synchronized from the polled data in an effect.
  const { data: activeData } = useQuery({
    queryKey: ['active-model'],
    queryFn: () => modelsAPI.getActive().then(r => r.data),
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (activeData?.active_model) {
      setActiveModel(activeData.active_model);
    }
  }, [activeData?.active_model, setActiveModel]);

  const models = modelsData?.models || [];

  // Set active mutation
  const setActiveMutation = useMutation({
    mutationFn: ({ version, authorisation }) => modelsAPI.setActive(version, authorisation),
    onSuccess: (_, variables) => {
      setActiveModel(variables.version);
      setActivationModel(null);
      setActivationForm({ proposalId: '', expectedRevision: '', actor: '', actorKey: '', approvalKey: '' });
      queryClient.invalidateQueries({ queryKey: ['active-model'] });
      queryClient.invalidateQueries({ queryKey: ['models'] });
      toast.success(`Model ${variables.version} is now active`);
    },
    onError: (error) => {
      toast.error(`Failed to set active model: ${error.response?.data?.detail || error.message}`);
    }
  });

  // Backtest mutation
  const backtestMutation = useMutation({
    mutationFn: ({ version, startDate, endDate }) =>
      modelsAPI.runBacktest(version, startDate, endDate),
    onSuccess: () => {
      toast.success('Backtest started! Check logs for progress.');
      setBacktestModel(null);
    },
    onError: (error) => {
      toast.error(`Failed to start backtest: ${error.response?.data?.detail || error.message}`);
    }
  });

  const handleBacktestSubmit = ({ startDate, endDate }) => {
    backtestMutation.mutate({ version: backtestModel, startDate, endDate });
  };

  if (isLoading) {
    return (
      <div className="panel">
        <h2>📈 Model Registry</h2>
        <div className="loading" role="status" aria-live="polite" aria-busy="true">Loading models...</div>
      </div>
    );
  }

  // A failed fetch must read as an error, not as an empty registry: the empty
  // state below is grey and passive, this one is red and actionable.
  if (isError) {
    return (
      <div className="panel">
        <h2>📈 Model Registry</h2>
        <div className="error" role="alert" aria-live="assertive">
          Failed to load models: {error?.message || 'Unknown error'}
        </div>
        <div style={{ textAlign: 'center', marginTop: '12px' }}>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => refetch()}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>📈 Model Registry</h2>

      <div className="info-box" role="region" aria-label="Model registry summary">
        <p>
          <strong>Active Model:</strong>{' '}
          <span aria-label={`Current active model is ${activeData?.active_model || 'none selected'}`}>
            {activeData?.active_model || 'None'}
          </span>
        </p>
        <p>
          <strong>Total Models:</strong> <span aria-label={`${models.length} models available`}>{models.length}</span>
        </p>
      </div>

      {models.length === 0 ? (
        <div className="empty-state" role="note" aria-label="No models found. Train a model first to see it here.">
          <p>No models found in checkpoints/</p>
          <p>Train a model first to see it here.</p>
        </div>
      ) : (
        <div className="models-table-container" role="region" aria-label={`List of ${models.length} trained models`}>
          <table className="models-table" aria-describedby="models-description">
            <caption id="models-description">Table of trained models with version, status, and actions</caption>
            <thead>
              <tr>
                <th>Version</th>
                <th>Has Model</th>
                <th>Modified</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => {
                const isActive = model.version === activeData?.active_model;
                const modifiedDate = model.created_at
                  ? new Date(model.created_at).toLocaleDateString()
                  : model.timestamp
                    ? new Date(model.timestamp * 1000).toLocaleDateString()
                    : 'Unknown';

                return (
                  <tr
                    key={model.version}
                    className={selectedModel === model.version ? 'selected' : ''}
                    onClick={() => setSelectedModel(model.version)}
                    aria-label={`Model ${model.version}, ${isActive ? 'currently active' : 'inactive'}, ${model.has_model ? 'weights available' : 'no weights'}, modified ${modifiedDate}`}
                  >
                    <td>
                      <strong>{model.version}</strong>
                    </td>
                    <td>
                      {model.has_model ? (
                        <span className="badge badge-success" aria-label={`Model ${model.version} has trained weights`}>✓ Yes</span>
                      ) : (
                        <span className="badge badge-warning" aria-label={`Model ${model.version} does not have trained weights`}>✗ No</span>
                      )}
                    </td>
                    <td aria-label={`Last modified on ${modifiedDate}`}>{modifiedDate}</td>
                    <td>
                      {isActive ? (
                        <span className="badge badge-active" aria-label={`Model ${model.version} is currently active`}>🟢 Active</span>
                      ) : (
                        <span className="badge badge-inactive" aria-label={`Model ${model.version} is inactive`}>⚪ Inactive</span>
                      )}
                    </td>
                    <td>
                      <div className="action-buttons" role="group" aria-label={`Actions for model ${model.version}`}>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setActivationModel(model.version);
                          }}
                          disabled={isActive || !model.has_model || setActiveMutation.isPending}
                          className="btn btn-small btn-primary"
                          aria-label={isActive ? `Model ${model.version} is already active` : `Set model ${model.version} as active for trading`}
                          aria-busy={setActiveMutation.isPending}
                        >
                          {isActive ? '✓ Active' : 'Set Active'}
                        </button>

                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setBacktestModel(model.version);
                          }}
                          disabled={!model.has_model || backtestMutation.isPending}
                          className="btn btn-small btn-secondary"
                          aria-label={`Run backtest for model ${model.version}`}
                          aria-busy={backtestMutation.isPending}
                        >
                          Backtest
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selectedModel && (
        <div className="model-details" role="region" aria-live="polite" aria-atomic="true">
          <h3>Selected: {selectedModel}</h3>
          <p>Activation requires an approved critical proposal, a verified executor principal, and the independent approval factor. Backtesting remains non-authoritative.</p>
        </div>
      )}

      {activationModel && (
        <div className="section" role="dialog" aria-modal="true" aria-label={`Governed activation for ${activationModel}`}>
          <h3>Activate {activationModel}</h3>
          <div className="info-box">
            Activation consumes a previously approved <code>model.activate</code> proposal. Credentials are kept only in this component state and are cleared after submission or cancellation.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '10px' }}>
            <label>Proposal ID
              <input value={activationForm.proposalId} onChange={event => setActivationForm({ ...activationForm, proposalId: event.target.value })} autoComplete="off" />
            </label>
            <label>Expected revision
              <input type="number" min="1" value={activationForm.expectedRevision} onChange={event => setActivationForm({ ...activationForm, expectedRevision: event.target.value })} />
            </label>
            <label>Executor principal
              <input value={activationForm.actor} onChange={event => setActivationForm({ ...activationForm, actor: event.target.value })} autoComplete="username" />
            </label>
            <label>Principal key
              <input type="password" value={activationForm.actorKey} onChange={event => setActivationForm({ ...activationForm, actorKey: event.target.value })} autoComplete="off" />
            </label>
            <label>Approval factor
              <input type="password" value={activationForm.approvalKey} onChange={event => setActivationForm({ ...activationForm, approvalKey: event.target.value })} autoComplete="off" />
            </label>
          </div>
          <div className="action-buttons" style={{ marginTop: '15px' }}>
            <button
              className="btn btn-primary"
              disabled={setActiveMutation.isPending || !activationForm.proposalId || !activationForm.expectedRevision || !activationForm.actor || !activationForm.actorKey || !activationForm.approvalKey}
              onClick={() => setActiveMutation.mutate({
                version: activationModel,
                authorisation: {
                  ...activationForm,
                  expectedRevision: Number(activationForm.expectedRevision),
                },
              })}
            >
              {setActiveMutation.isPending ? 'Activating…' : 'Consume approval and activate'}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => {
                setActivationModel(null);
                setActivationForm({ proposalId: '', expectedRevision: '', actor: '', actorKey: '', approvalKey: '' });
              }}
            >Cancel</button>
          </div>
        </div>
      )}

      {backtestModel && (
        <BacktestModal
          modelVersion={backtestModel}
          onClose={() => setBacktestModel(null)}
          onSubmit={handleBacktestSubmit}
          isLoading={backtestMutation.isPending}
        />
      )}
    </div>
  );
}
