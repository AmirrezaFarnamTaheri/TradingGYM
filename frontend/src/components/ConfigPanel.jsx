import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { configAPI } from '../api';
import ConfigEditor from './ConfigEditor';

export default function ConfigPanel() {
  const [searchTerm, setSearchTerm] = useState('');
  const [editingSection, setEditingSection] = useState(null);
  const queryClient = useQueryClient();

  // Cleaned up unused imports (useToast) as per report

  const { data: config, isLoading, error } = useQuery({
    queryKey: ['config'],
    queryFn: () => configAPI.getConfig().then(r => r.data),
    refetchInterval: 10000, // Refresh every 10 seconds
  });

  if (isLoading) {
    return (
      <div className="panel">
        <h2>⚙️ Configuration</h2>
        <div className="loading" role="status" aria-live="polite" aria-busy="true">Loading configuration...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel">
        <h2>⚙️ Configuration</h2>
        <div className="error" role="alert" aria-live="assertive">Error loading config: {error.message}</div>
      </div>
    );
  }

  const configString = JSON.stringify(config, null, 2);
  const filteredConfig = searchTerm
    ? configString.split('\n').filter(line =>
        line.toLowerCase().includes(searchTerm.toLowerCase())
      ).join('\n')
    : configString;

  if (editingSection) {
    return (
      <div className="panel">
        <ConfigEditor
          section={editingSection}
          config={config}
          onClose={() => setEditingSection(null)}
          onSuccess={() => {
            // Refresh config after successful update
            queryClient.invalidateQueries({ queryKey: ['config'] });
          }}
        />
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>⚙️ Configuration</h2>

      <div className="info-box" role="note" aria-label="Configuration management instructions">
        <p>
          <strong>Configuration view and editor</strong>. Click on a section below to edit it.
          Changes are saved immediately to the backend.
        </p>
      </div>

      {/* Section Editor Buttons */}
      {config && (
        <div className="config-sections">
          <h3>Edit Configuration Sections</h3>
          <div className="section-buttons" role="group" aria-label={`${Object.keys(config).length} configuration sections available for editing`}>
            {Object.keys(config).map((section) => (
              <button
                key={section}
                onClick={() => setEditingSection(section)}
                className="btn btn-secondary section-btn"
                aria-label={`Edit ${section} configuration section`}
              >
                ✏️ {section}
              </button>
            ))}
          </div>
        </div>
      )}

      <hr style={{ margin: '20px 0' }} />

      <div className="search-box">
        <label htmlFor="config-search" style={{ display: 'block', marginBottom: '8px' }}>
          Search Configuration
        </label>
        <input
          id="config-search"
          type="text"
          placeholder="Search configuration by keyword..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
          aria-label="Search configuration by keyword"
          aria-describedby="search-help"
        />
        <small id="search-help" style={{ color: '#666', display: 'block', marginTop: '4px' }}>
          {searchTerm ? `Showing results matching "${searchTerm}"` : 'Type to filter configuration values'}
        </small>
      </div>

      <pre
        className="config-display"
        role="region"
        aria-label="Configuration content"
        aria-live="polite"
      >
        {filteredConfig}
      </pre>

      <div className="button-group" role="group" aria-label="Configuration export actions">
        <button
          onClick={() => {
            const blob = new Blob([configString], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `config-${new Date().toISOString().split('T')[0]}.json`;
            a.click();
          }}
          className="btn btn-secondary"
          aria-label={`Download current configuration as JSON file (${Object.keys(config).length} sections)`}
        >
          📥 Download Config
        </button>
      </div>
    </div>
  );
}
