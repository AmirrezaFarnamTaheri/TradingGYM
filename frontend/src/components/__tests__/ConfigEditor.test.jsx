import { describe, expect, it, beforeEach, vi } from 'vitest';
import { act } from 'react-dom/test-utils';

import ConfigEditor from '../ConfigEditor';
import { renderWithClient, flushPromises } from '../../test-utils.jsx';

function setTextareaValue(textarea, value) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value'
  ).set;
  setter.call(textarea, value);
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

function setInputValue(input, value) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value'
  ).set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

const { toastSpies, updateConfigSpy } = vi.hoisted(() => ({
  toastSpies: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
  updateConfigSpy: vi.fn(() => Promise.resolve({ status: 'ok' })),
}));

vi.mock('../../api', () => ({
  configAPI: {
    updateConfig: updateConfigSpy,
  },
}));

vi.mock('../Toast', () => ({
  useToast: () => toastSpies,
}));

describe('ConfigEditor', () => {
  beforeEach(() => {
    updateConfigSpy.mockClear();
    Object.values(toastSpies).forEach((fn) => fn.mockClear());
  });

  const baseConfig = {
    trading: {
      leverage: 2,
      flags: {
        hedge: true,
      },
    },
  };

  it('blocks submission when JSON field is invalid', async () => {
    const onClose = vi.fn();
    const { container, cleanup } = renderWithClient(
      <ConfigEditor section="trading" config={baseConfig} onClose={onClose} />
    );

    const jsonField = container.querySelector('#config-trading-flags');
    act(() => {
      setTextareaValue(jsonField, '{ "hedge": true');
    });

    const saveButton = container.querySelector('button[aria-label="Save configuration changes"]');
    await act(async () => {
      saveButton.click();
      await Promise.resolve();
    });

    const errorBanner = container.querySelector('#config-trading-flags-error');
    expect(errorBanner?.textContent).toContain('Invalid JSON');
    expect(updateConfigSpy).not.toHaveBeenCalled();
    expect(toastSpies.error).toHaveBeenCalledWith(
      'Cannot save configuration: fix invalid JSON field values first.'
    );
    expect(onClose).not.toHaveBeenCalled();

    cleanup();
  });

  it('submits parsed configuration when JSON is valid', async () => {
    const onClose = vi.fn();
    const onSuccess = vi.fn();
    const { container, cleanup } = renderWithClient(
      <ConfigEditor
        section="trading"
        config={baseConfig}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const leverageField = container.querySelector('#config-trading-leverage');
    const jsonField = container.querySelector('#config-trading-flags');

    act(() => {
      setInputValue(leverageField, '3');
      setTextareaValue(jsonField, '{"hedge": false}');
    });

    const saveButton = container.querySelector('button[aria-label="Save configuration changes"]');
    await act(async () => {
      saveButton.click();
      await Promise.resolve();
    });

    await flushPromises();

    expect(updateConfigSpy).toHaveBeenCalledWith({
      trading: {
        leverage: 3,
        flags: { hedge: false },
      },
    });
    expect(onSuccess).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();

    cleanup();
  });
});
