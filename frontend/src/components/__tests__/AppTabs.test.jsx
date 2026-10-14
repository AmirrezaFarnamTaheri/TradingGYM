import { act } from 'react-dom/test-utils';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import App from '../../App';

vi.mock('../InstitutionalOSPanel', () => ({
  default: () => <div>Institutional test panel</div>,
}));
vi.mock('../ConvergencePanel', () => ({
  default: () => <div>Intelligence test panel</div>,
}));

describe('Operator workspace tab accessibility', () => {
  it('binds the visible panel to its tab and supports roving keyboard focus', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => { root.render(<App />); });
      const tabs = Array.from(container.querySelectorAll('[role="tab"]'));
      const selected = tabs.find(tab => tab.getAttribute('aria-selected') === 'true');
      expect(tabs).toHaveLength(12);
      expect(selected.id).toBe('tab-institutional');
      expect(selected.getAttribute('tabindex')).toBe('0');
      expect(tabs.filter(tab => tab.getAttribute('tabindex') === '0')).toHaveLength(1);
      const panel = container.querySelector('[role="tabpanel"]');
      expect(panel.getAttribute('aria-labelledby')).toBe(selected.id);
      expect(selected.getAttribute('aria-controls')).toBe(panel.id);

      await act(async () => {
        selected.dispatchEvent(new KeyboardEvent('keydown', {
          key: 'ArrowDown', bubbles: true,
        }));
      });
      const next = container.querySelector('#tab-convergence');
      expect(next.getAttribute('aria-selected')).toBe('true');
      expect(next.getAttribute('tabindex')).toBe('0');
      expect(document.activeElement).toBe(next);
      expect(panel.getAttribute('aria-labelledby')).toBe(next.id);

      await act(async () => {
        next.dispatchEvent(new KeyboardEvent('keydown', {
          key: 'Home', bubbles: true,
        }));
      });
      expect(container.querySelector('#tab-production').getAttribute('aria-selected')).toBe('true');
    } finally {
      await act(async () => { root.unmount(); });
      container.remove();
    }
  });
});

it('keeps all twelve destinations reachable across four workspaces', async () => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => { root.render(<App />); });
    const labels = Array.from(
      container.querySelectorAll('.sidebar-workspace-label')
    ).map((node) => node.textContent);
    expect(labels).toEqual([
      'Execution & Risk',
      'Research & Playground',
      'Agent & Model Forge',
      'Governance & System',
    ]);
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(12);

    const lastResearchTab = container.querySelector('#tab-data');
    await act(async () => {
      lastResearchTab.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowDown', bubbles: true,
      }));
    });
    const firstModelTab = container.querySelector('#tab-forge');
    expect(firstModelTab.getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(firstModelTab);
  } finally {
    await act(async () => { root.unmount(); });
    container.remove();
  }
});
