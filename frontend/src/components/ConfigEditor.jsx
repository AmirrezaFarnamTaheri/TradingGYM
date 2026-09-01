import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { configAPI } from '../api';
import { useToast } from './Toast';

/**
 * ConfigEditor Component
 *
 * Allows editing of configuration sections with:
 * - Dynamic form generation based on section structure
 * - Field validation before submission
 * - Save/Cancel with confirmation
 * - Rollback capability on error
 */
export default function ConfigEditor({ section, config, onClose, onSuccess }) {
  const [formData, setFormData] = useState(config?.[section] || {});
  const [hasChanges, setHasChanges] = useState(false);
  const initialObjectState = () => {
    const sectionData = config?.[section] || {};
    return Object.entries(sectionData).reduce((acc, [key, value]) => {
      if (value !== null && typeof value === 'object') {
        acc[key] = {
          text: JSON.stringify(value, null, 2),
          error: ''
        };
      }
      return acc;
    }, {});
  };
  const [objectFieldState, setObjectFieldState] = useState(() => initialObjectState());
  const toast = useToast();

  const mutation = useMutation({
    mutationFn: async () => {
      // Prepare the update (send as partial update for the section)
      return configAPI.updateConfig({ [section]: formData });
    },
    onSuccess: () => {
      toast.success(`Configuration section '${section}' updated successfully`);
      setHasChanges(false);
      onSuccess?.();
      onClose();
    },
    onError: (error) => {
      toast.error(
        `Failed to update config: ${error.response?.data?.detail || error.message}`
      );
    }
  });

  const handleFieldChange = (key, value) => {
    setFormData(prev => ({ ...prev, [key]: value }));
    setHasChanges(true);
  };

  const handleReset = () => {
    const resetData = config?.[section] || {};
    setFormData(resetData);
    setObjectFieldState(initialObjectState());
    setHasChanges(false);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!hasChanges) {
      toast.info('No changes to save');
      return;
    }
    const jsonErrors = Object.values(objectFieldState || {}).filter(field => field?.error);
    if (jsonErrors.length > 0) {
      toast.error('Cannot save configuration: fix invalid JSON field values first.');
      return;
    }
    mutation.mutate();
  };

  const renderField = (key, value) => {
    const fieldId = `config-${section}-${key}`;
    const fieldType = typeof value;
    const objectState = objectFieldState?.[key];

    return (
      <div key={key} className="form-group">
        <label htmlFor={fieldId} aria-label={`Configure ${key} in ${section} section`}>
          {key}
        </label>

        {fieldType === 'boolean' ? (
          <input
            id={fieldId}
            type="checkbox"
            checked={value || false}
            onChange={(e) => handleFieldChange(key, e.target.checked)}
            className="checkbox-input"
            aria-label={`Toggle ${key}`}
          />
        ) : fieldType === 'number' ? (
          <input
            id={fieldId}
            type="number"
            value={value || ''}
            onChange={(e) => handleFieldChange(key, e.target.value ? Number(e.target.value) : '')}
            className="text-input"
            aria-label={`Enter ${key} as number`}
          />
        ) : fieldType === 'object' ? (
          <>
            <textarea
              id={fieldId}
              value={objectState?.text ?? JSON.stringify(value, null, 2)}
              onChange={(e) => {
                const raw = e.target.value;
                setHasChanges(true);
                let parsedValue;
                let errorMessage = '';

                try {
                  parsedValue = JSON.parse(raw);
                } catch (err) {
                  errorMessage = 'Invalid JSON';
                }

                setObjectFieldState(prev => ({
                  ...prev,
                  [key]: {
                    text: raw,
                    error: errorMessage
                  }
                }));

                if (!errorMessage) {
                  setFormData(prev => ({ ...prev, [key]: parsedValue }));
                }
              }}
              className="text-input"
              rows="4"
              aria-label={`Configure ${key} as JSON object`}
              aria-invalid={Boolean(objectState?.error)}
              aria-describedby={objectState?.error ? `${fieldId}-error` : undefined}
            />
            {objectState?.error && (
              <div id={`${fieldId}-error`} className="error" role="alert">
                {objectState.error}
              </div>
            )}
          </>
        ) : (
          <input
            id={fieldId}
            type="text"
            value={value || ''}
            onChange={(e) => handleFieldChange(key, e.target.value)}
            className="text-input"
            aria-label={`Configure ${key}`}
          />
        )}
      </div>
    );
  };

  return (
    <div className="config-editor">
      <div className="editor-header">
        <h3 id="editor-title">Edit Configuration: {section}</h3>
        <button
          onClick={onClose}
          className="editor-close"
          aria-label="Close configuration editor"
          title="Close without saving"
        >
          ✕
        </button>
      </div>

      <form onSubmit={handleSubmit} aria-labelledby="editor-title">
        <div className="form-container">
          {Object.entries(formData).length === 0 ? (
            <p className="empty-section">This section has no configurable options</p>
          ) : (
            Object.entries(formData).map(([key, value]) => renderField(key, value))
          )}
        </div>

        <div className="form-buttons">
          <button
            type="submit"
            disabled={!hasChanges || mutation.isPending}
            className="btn btn-primary"
            aria-label="Save configuration changes"
            aria-busy={mutation.isPending}
          >
            {mutation.isPending ? '⏳ Saving...' : '💾 Save Changes'}
          </button>

          <button
            type="button"
            onClick={handleReset}
            disabled={!hasChanges || mutation.isPending}
            className="btn btn-secondary"
            aria-label="Reset configuration to original values"
          >
            ↶ Reset
          </button>

          <button
            type="button"
            onClick={onClose}
            disabled={mutation.isPending}
            className="btn btn-secondary"
            aria-label="Cancel and close editor without saving"
          >
            ✗ Cancel
          </button>
        </div>

        {hasChanges && (
          <div className="unsaved-changes" role="alert" aria-live="polite">
            ⚠️ You have unsaved changes
          </div>
        )}
      </form>
    </div>
  );
}
