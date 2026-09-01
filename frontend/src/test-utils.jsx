import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react-dom/test-utils';
import { createRoot } from 'react-dom/client';
import React from 'react';

export function renderWithClient(ui, options = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        ...options.queryOptions,
      },
    },
  });

  const root = createRoot(container);

  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    );
  });

  const cleanup = () => {
    act(() => {
      root.unmount();
    });
    if (container.parentNode) {
      container.parentNode.removeChild(container);
    }
    queryClient.clear();
  };

  const rerender = (nextUi) => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>{nextUi}</QueryClientProvider>
      );
    });
  };

  return {
    container,
    queryClient,
    cleanup,
    rerender,
  };
}

export async function flushPromises() {
  await act(async () => {
    await Promise.resolve();
  });
}

// React-query settles over several microtask+render cycles; spin until the
// selector appears (or an optional predicate passes) instead of guessing.
export async function waitForRendered(container, selector, predicate) {
  for (let i = 0; i < 40; i += 1) {
    await flushPromises();
    const nodes = Array.from(container.querySelectorAll(selector));
    const found = predicate ? nodes.find(predicate) : nodes[0];
    if (found) return found;
  }
  if (typeof console !== 'undefined') {
    console.error(
      'waitForRendered timeout HTML:',
      container.innerHTML.slice(0, 1500)
    );
  }
  throw new Error(`waitForRendered: ${selector} never appeared`);
}
