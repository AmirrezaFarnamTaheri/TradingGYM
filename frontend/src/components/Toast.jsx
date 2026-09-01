/**
 * Toast Notification System
 *
 * Provides a global toast notification system with support for:
 * - Multiple concurrent toasts
 * - Auto-dismiss with customizable duration
 * - Different toast types (success, error, warning, info)
 * - Accessible design with ARIA labels
 *
 * @module components/Toast
 * @version 4.2.0
 */

import React, { useState, useCallback, useEffect, createContext, useContext } from 'react';

// =============================================================================
// Toast Context
// =============================================================================

/**
 * Context for toast notifications
 * @type {React.Context}
 */
const ToastContext = createContext(null);

/**
 * Generate unique ID for toasts
 * @returns {string}
 */
let toastId = 0;
const generateId = () => `toast-${++toastId}`;

// =============================================================================
// Toast Provider
// =============================================================================

/**
 * Toast Provider Component
 *
 * Wraps the application to provide toast functionality via useToast hook.
 *
 * @example
 * // In App.jsx
 * <ToastProvider>
 *   <App />
 * </ToastProvider>
 *
 * // In any component
 * const toast = useToast();
 * toast.success('Operation completed!');
 */
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback((type, message, duration = 5000) => {
    const id = generateId();
    const toast = { id, type, message, duration };

    setToasts((prev) => [...prev, toast]);

    // Auto-remove after duration
    if (duration > 0) {
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, duration);
    }

    return id;
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const clearAll = useCallback(() => {
    setToasts([]);
  }, []);

  // Toast methods
  const toast = {
    success: (message, duration) => addToast('success', message, duration),
    error: (message, duration) => addToast('error', message, duration ?? 8000), // Errors show longer
    warning: (message, duration) => addToast('warning', message, duration ?? 6000),
    info: (message, duration) => addToast('info', message, duration),
    remove: removeToast,
    clear: clearAll,
  };

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <ToastContainer toasts={toasts} onClose={removeToast} />
    </ToastContext.Provider>
  );
}

// =============================================================================
// useToast Hook
// =============================================================================

/**
 * Hook to access toast functionality
 *
 * @returns {{
 *   success: (message: string, duration?: number) => string,
 *   error: (message: string, duration?: number) => string,
 *   warning: (message: string, duration?: number) => string,
 *   info: (message: string, duration?: number) => string,
 *   remove: (id: string) => void,
 *   clear: () => void
 * }}
 *
 * @example
 * const toast = useToast();
 * toast.success('Saved successfully!');
 * toast.error('Failed to save');
 * toast.warning('Low disk space');
 * toast.info('New update available');
 */
export function useToast() {
  const context = useContext(ToastContext);

  if (!context) {
    // Return a fallback that logs to console when used outside provider
    console.warn('useToast must be used within a ToastProvider. Falling back to console.');
    return {
      success: (msg) => console.log('✅', msg),
      error: (msg) => console.error('❌', msg),
      warning: (msg) => console.warn('⚠️', msg),
      info: (msg) => console.info('ℹ️', msg),
      remove: () => {},
      clear: () => {},
    };
  }

  return context;
}

// =============================================================================
// Toast Container
// =============================================================================

/**
 * Container component that renders all active toasts
 */
export function ToastContainer({ toasts = [], onClose }) {
  if (toasts.length === 0) return null;

  return (
    <div
      className="toast-container"
      role="region"
      aria-label="Notifications"
      aria-live="polite"
    >
      {toasts.map((toast) => (
        <ToastItem
          key={toast.id}
          {...toast}
          onClose={() => onClose?.(toast.id)}
        />
      ))}
    </div>
  );
}

// =============================================================================
// Toast Item
// =============================================================================

/**
 * Individual toast notification component
 */
function ToastItem({ type = 'info', message, onClose }) {
  // Icon mapping
  const icons = {
    success: '✅',
    error: '❌',
    warning: '⚠️',
    info: 'ℹ️',
  };

  // ARIA labels for screen readers
  const ariaLabels = {
    success: 'Success notification',
    error: 'Error notification',
    warning: 'Warning notification',
    info: 'Information notification',
  };

  return (
    <div
      className={`toast toast-${type}`}
      role="alert"
      aria-label={ariaLabels[type] || ariaLabels.info}
    >
      <span className="toast-icon" aria-hidden="true">
        {icons[type] || icons.info}
      </span>
      <span className="toast-message">{message}</span>
      <button
        onClick={onClose}
        className="toast-close"
        aria-label="Dismiss notification"
      >
        ×
      </button>
    </div>
  );
}

// =============================================================================
// Standalone Toast Component (Legacy Support)
// =============================================================================

/**
 * Standalone Toast component for direct usage without context
 *
 * @deprecated Use ToastProvider + useToast instead
 */
export default function Toast({ type = 'info', message, onClose, duration = 5000 }) {
  // Auto-dismiss
  useEffect(() => {
    if (duration && onClose) {
      const timer = setTimeout(onClose, duration);
      return () => clearTimeout(timer);
    }
  }, [duration, onClose]);

  const icons = {
    success: '✅',
    error: '❌',
    warning: '⚠️',
    info: 'ℹ️',
  };

  return (
    <div
      className={`toast toast-${type}`}
      role="alert"
      style={{
        position: 'fixed',
        top: '20px',
        right: '20px',
        zIndex: 9999,
      }}
    >
      <span className="toast-icon" aria-hidden="true">
        {icons[type] || icons.info}
      </span>
      <span className="toast-message">{message}</span>
      {onClose && (
        <button
          onClick={onClose}
          className="toast-close"
          aria-label="Dismiss notification"
        >
          ×
        </button>
      )}
    </div>
  );
}

// =============================================================================
// Exports
// =============================================================================

export { ToastContext, ToastItem };
