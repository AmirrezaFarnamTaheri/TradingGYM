export function TableSkeleton({ rows = 5, columns = 5 }) {
  return (
    <div className="skeleton-table">
      <div className="skeleton-table-header">
        {Array.from({ length: columns }).map((_, i) => (
          <div key={i} className="skeleton skeleton-header" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="skeleton-table-row">
          {Array.from({ length: columns }).map((_, colIndex) => (
            <div key={colIndex} className="skeleton skeleton-cell" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function MetricsSkeleton() {
  return (
    <div className="metrics-grid">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="metric-card">
          <div className="skeleton skeleton-label" />
          <div className="skeleton skeleton-value" />
        </div>
      ))}
    </div>
  );
}

export function CardSkeleton() {
  return (
    <div className="panel">
      <div className="skeleton skeleton-title" />
      <div className="skeleton skeleton-text" />
      <div className="skeleton skeleton-text" />
      <div className="skeleton skeleton-button" />
    </div>
  );
}
