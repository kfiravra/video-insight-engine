import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';

export const USER_ID = 'a'.repeat(24);

/** Render inside a query client and return a spy on its invalidations. */
export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const view = render(createElement(QueryClientProvider, { client }, ui));
  return { ...view, invalidate };
}

export function typeReason(reason: string): void {
  fireEvent.change(screen.getByTestId('action-reason'), { target: { value: reason } });
}

export function setField(testId: string, value: string): void {
  fireEvent.change(screen.getByTestId(testId), { target: { value } });
}

/** Submit the dialog's form the way Enter or the button would. */
export function submitDialog(): void {
  const form = screen.getByTestId('action-reason').closest('form');
  if (!form) throw new Error('dialog form not found');
  fireEvent.submit(form);
}
