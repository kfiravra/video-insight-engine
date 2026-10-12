import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabCoordinationProvider, useTabCoordination } from '../TabCoordinationContext';

function TestConsumer() {
  const coord = useTabCoordination();
  if (!coord) return <div data-testid="no-provider">no provider</div>;
  const { activeTab, setActiveTab, completedTabs, markTabCompleted } = coord;
  return (
    <div>
      <span data-testid="active-tab">{activeTab}</span>
      <span data-testid="completed-count">{completedTabs.size}</span>
      <span data-testid="completed-list">{Array.from(completedTabs).join(',')}</span>
      <button onClick={() => setActiveTab('quiz')}>Go to quiz</button>
      <button onClick={() => markTabCompleted('overview')}>Complete overview</button>
    </div>
  );
}

describe('TabCoordinationContext', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('should provide initial active tab', () => {
    render(
      <TabCoordinationProvider videoId="v1" initialTab="overview">
        <TestConsumer />
      </TabCoordinationProvider>
    );

    expect(screen.getByTestId('active-tab')).toHaveTextContent('overview');
  });

  it('should update active tab', () => {
    render(
      <TabCoordinationProvider videoId="v1" initialTab="overview">
        <TestConsumer />
      </TabCoordinationProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /go to quiz/i }));

    expect(screen.getByTestId('active-tab')).toHaveTextContent('quiz');
  });

  it('should track completed tabs', () => {
    render(
      <TabCoordinationProvider videoId="v1" initialTab="overview">
        <TestConsumer />
      </TabCoordinationProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /complete overview/i }));

    expect(screen.getByTestId('completed-count')).toHaveTextContent('1');
    expect(screen.getByTestId('completed-list')).toHaveTextContent('overview');
  });

  it('should not duplicate completed tabs', () => {
    render(
      <TabCoordinationProvider videoId="v1" initialTab="overview">
        <TestConsumer />
      </TabCoordinationProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /complete overview/i }));
    fireEvent.click(screen.getByRole('button', { name: /complete overview/i }));

    expect(screen.getByTestId('completed-count')).toHaveTextContent('1');
  });

  it('should persist completed tabs in sessionStorage', () => {
    const { unmount } = render(
      <TabCoordinationProvider videoId="v1" initialTab="overview">
        <TestConsumer />
      </TabCoordinationProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /complete overview/i }));
    unmount();

    // Re-render should load from sessionStorage
    render(
      <TabCoordinationProvider videoId="v1" initialTab="overview">
        <TestConsumer />
      </TabCoordinationProvider>
    );

    expect(screen.getByTestId('completed-count')).toHaveTextContent('1');
  });

  it('should isolate storage by videoId', () => {
    const { unmount } = render(
      <TabCoordinationProvider videoId="v1" initialTab="overview">
        <TestConsumer />
      </TabCoordinationProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /complete overview/i }));
    unmount();

    // Different videoId should start fresh
    render(
      <TabCoordinationProvider videoId="v2" initialTab="overview">
        <TestConsumer />
      </TabCoordinationProvider>
    );

    expect(screen.getByTestId('completed-count')).toHaveTextContent('0');
  });

  it('should return null when used outside provider', () => {
    render(<TestConsumer />);
    expect(screen.getByTestId('no-provider')).toHaveTextContent('no provider');
  });

  describe('sticky initial tab', () => {
    function renderWith(initialTab: string, replacedTabIds?: Record<string, string>) {
      return (
        <TabCoordinationProvider videoId="v1" initialTab={initialTab} replacedTabIds={replacedTabIds}>
          <TestConsumer />
        </TabCoordinationProvider>
      );
    }

    it('should keep the first initial tab active when a tab streamed in ahead of it changes initialTab', () => {
      const { rerender } = render(renderWith('moments'));

      rerender(renderWith('steps'));

      expect(screen.getByTestId('active-tab')).toHaveTextContent('moments');
    });

    it('should apply the first non-empty initialTab when the provider mounted before any tab', () => {
      const { rerender } = render(renderWith(''));

      rerender(renderWith('moments'));

      expect(screen.getByTestId('active-tab')).toHaveTextContent('moments');
    });

    it("should keep the user's choice when initialTab changes later", async () => {
      const user = userEvent.setup();
      const { rerender } = render(renderWith('overview'));
      await user.click(screen.getByRole('button', { name: /go to quiz/i }));

      rerender(renderWith('steps'));

      expect(screen.getByTestId('active-tab')).toHaveTextContent('quiz');
    });

    it('should move focus to the replacing tab when the active tab was superseded', () => {
      const { rerender } = render(renderWith('key_moments'));

      rerender(renderWith('key_moments', { key_moments: 'chapters' }));

      expect(screen.getByTestId('active-tab')).toHaveTextContent('chapters');
    });
  });
});
