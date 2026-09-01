import { describe, expect, it, vi } from 'vitest';
import { act } from 'react-dom/test-utils';

import BacktestModal from '../BacktestModal';
import { renderWithClient } from '../../test-utils.jsx';

// React tracks controlled inputs; write values through the native setter and
// dispatch a bubbling input event so onChange fires with the new value.
function setInputValue(input, value) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value'
  ).set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}


describe('BacktestModal', () => {
  const defaultProps = {
    modelVersion: 'alpha',
    onClose: vi.fn(),
    onSubmit: vi.fn(),
    isLoading: false,
  };

  it('prevents submitting when start date is not before end date', async () => {
    const onSubmit = vi.fn();
    const { container, cleanup } = renderWithClient(
      <BacktestModal {...defaultProps} onSubmit={onSubmit} />
    );

    const startInput = container.querySelector('#start-date');
    const endInput = container.querySelector('#end-date');

    act(() => {
      setInputValue(startInput, '2024-05-10');
      setInputValue(endInput, '2024-05-01');
    });

    const form = container.querySelector('form');
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Start date must be strictly before end date');
    expect(onSubmit).not.toHaveBeenCalled();

    cleanup();
  });

  it('normalizes valid dates before submitting', async () => {
    const onSubmit = vi.fn();
    const { container, cleanup } = renderWithClient(
      <BacktestModal {...defaultProps} onSubmit={onSubmit} />
    );

    const startInput = container.querySelector('#start-date');
    const endInput = container.querySelector('#end-date');

    act(() => {
      setInputValue(startInput, '2024-01-01');
      setInputValue(endInput, '2024-01-03');
    });

    const form = container.querySelector('form');
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });

    expect(onSubmit).toHaveBeenCalledWith({ startDate: '2024-01-01', endDate: '2024-01-03' });

    cleanup();
  });
});
