import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { RAGChatPanel } from '../RAGChatPanel';

// Mock scrollIntoView as jsdom doesn't support it
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

const createMessage = (overrides = {}) => ({
  id: 'msg-1',
  role: 'user' as const,
  content: 'Hello',
  createdAt: '2024-01-01T00:00:00Z',
  ...overrides,
});

describe('RAGChatPanel', () => {
  const defaultProps = {
    messages: [],
    onSendMessage: vi.fn(),
  };

  describe('rendering', () => {
    it('should render header', () => {
      render(<RAGChatPanel {...defaultProps} />);

      expect(screen.getByText('Chat with your knowledge')).toBeInTheDocument();
    });

    it('should render empty state when no messages', () => {
      render(<RAGChatPanel {...defaultProps} />);

      expect(screen.getByText('Start a conversation')).toBeInTheDocument();
      expect(screen.getByText(/ask questions about your videos/i)).toBeInTheDocument();
    });

    it('should render messages when provided', () => {
      render(
        <RAGChatPanel
          {...defaultProps}
          messages={[
            createMessage({ id: 'msg-1', role: 'user', content: 'What is React?' }),
            createMessage({ id: 'msg-2', role: 'assistant', content: 'React is a UI library.' }),
          ]}
        />
      );

      expect(screen.getByText('What is React?')).toBeInTheDocument();
      expect(screen.getByText('React is a UI library.')).toBeInTheDocument();
    });

    it('should apply custom className', () => {
      const { container } = render(
        <RAGChatPanel {...defaultProps} className="custom-class" />
      );

      expect(container.firstChild).toHaveClass('custom-class');
    });

    it('should show the "This video" scope chip when scoped to a video', () => {
      render(<RAGChatPanel {...defaultProps} scope="video" />);

      expect(screen.getByText('This video')).toBeInTheDocument();
    });

    it('should show the "Your library" scope chip when scoped to the library', () => {
      render(<RAGChatPanel {...defaultProps} scope="library" />);

      expect(screen.getByText('Your library')).toBeInTheDocument();
    });

    it('should not render a scope chip when scope is omitted', () => {
      render(<RAGChatPanel {...defaultProps} />);

      expect(screen.queryByText('This video')).not.toBeInTheDocument();
      expect(screen.queryByText('Your library')).not.toBeInTheDocument();
    });
  });

  describe('message bubbles', () => {
    it('should style user messages differently from assistant', () => {
      const { container } = render(
        <RAGChatPanel
          {...defaultProps}
          messages={[
            createMessage({ role: 'user', content: 'User message' }),
            createMessage({ id: 'msg-2', role: 'assistant', content: 'Assistant message' }),
          ]}
        />
      );

      // User bubble should have primary color
      const userBubble = container.querySelector('.bg-primary.text-primary-foreground');
      expect(userBubble).toBeInTheDocument();
    });

    it('should show streaming cursor when isStreaming', () => {
      render(
        <RAGChatPanel
          {...defaultProps}
          messages={[
            createMessage({
              role: 'assistant',
              content: 'Thinking...',
              isStreaming: true,
            }),
          ]}
        />
      );

      expect(screen.getByText('▌')).toBeInTheDocument();
    });
  });

  describe('billing refusals', () => {
    it('should link a refused reply to the billing page', () => {
      render(
        <MemoryRouter>
          <RAGChatPanel
            {...defaultProps}
            messages={[
              createMessage({
                id: 'msg-2',
                role: 'assistant',
                content: "You're out of tokens — add tokens to keep chatting.",
                billingLink: true,
              }),
            ]}
          />
        </MemoryRouter>,
      );

      expect(screen.getByRole('link', { name: 'Tokens & plan' })).toHaveAttribute('href', '/settings/billing');
    });

    it('should not link an ordinary reply', () => {
      render(
        <RAGChatPanel
          {...defaultProps}
          messages={[createMessage({ id: 'msg-2', role: 'assistant', content: 'React is a UI library.' })]}
        />,
      );

      expect(screen.queryByTestId('chat-billing-link')).toBeNull();
    });
  });

  describe('input handling', () => {
    it('should render input field', () => {
      render(<RAGChatPanel {...defaultProps} />);

      expect(screen.getByPlaceholderText(/ask a question/i)).toBeInTheDocument();
    });

    it('should use custom placeholder when provided', () => {
      render(<RAGChatPanel {...defaultProps} placeholder="Custom placeholder..." />);

      expect(screen.getByPlaceholderText('Custom placeholder...')).toBeInTheDocument();
    });

    it('should call onSendMessage when form submitted', () => {
      const onSendMessage = vi.fn();
      render(<RAGChatPanel {...defaultProps} onSendMessage={onSendMessage} />);

      const input = screen.getByPlaceholderText(/ask a question/i);
      fireEvent.change(input, { target: { value: 'Test message' } });
      fireEvent.submit(input.closest('form')!);

      expect(onSendMessage).toHaveBeenCalledWith('Test message');
    });

    it('should clear input after submit', () => {
      const onSendMessage = vi.fn();
      render(<RAGChatPanel {...defaultProps} onSendMessage={onSendMessage} />);

      const input = screen.getByPlaceholderText(/ask a question/i) as HTMLInputElement;
      fireEvent.change(input, { target: { value: 'Test message' } });
      fireEvent.submit(input.closest('form')!);

      expect(input.value).toBe('');
    });

    it('should trim whitespace from message', () => {
      const onSendMessage = vi.fn();
      render(<RAGChatPanel {...defaultProps} onSendMessage={onSendMessage} />);

      const input = screen.getByPlaceholderText(/ask a question/i);
      fireEvent.change(input, { target: { value: '  Test message  ' } });
      fireEvent.submit(input.closest('form')!);

      expect(onSendMessage).toHaveBeenCalledWith('Test message');
    });

    it('should not submit empty messages', () => {
      const onSendMessage = vi.fn();
      render(<RAGChatPanel {...defaultProps} onSendMessage={onSendMessage} />);

      const input = screen.getByPlaceholderText(/ask a question/i);
      fireEvent.change(input, { target: { value: '   ' } });
      fireEvent.submit(input.closest('form')!);

      expect(onSendMessage).not.toHaveBeenCalled();
    });
  });

  describe('loading state', () => {
    it('should disable input when loading', () => {
      render(<RAGChatPanel {...defaultProps} isLoading />);

      expect(screen.getByPlaceholderText(/ask a question/i)).toBeDisabled();
    });

    it('should disable submit button when loading', () => {
      render(<RAGChatPanel {...defaultProps} isLoading />);

      const submitButton = screen.getByRole('button', { name: /send/i });
      expect(submitButton).toBeDisabled();
    });

    it('should show loading spinner in submit button', () => {
      const { container } = render(<RAGChatPanel {...defaultProps} isLoading />);

      const spinner = container.querySelector('.animate-spin');
      expect(spinner).toBeInTheDocument();
    });

    it('should not call onSendMessage when loading', () => {
      const onSendMessage = vi.fn();
      render(<RAGChatPanel {...defaultProps} onSendMessage={onSendMessage} isLoading />);

      const input = screen.getByPlaceholderText(/ask a question/i);
      fireEvent.change(input, { target: { value: 'Test' } });
      fireEvent.submit(input.closest('form')!);

      expect(onSendMessage).not.toHaveBeenCalled();
    });
  });

  describe('sources display', () => {
    it('should render sources for assistant messages', () => {
      render(
        <RAGChatPanel
          {...defaultProps}
          messages={[
            createMessage({
              role: 'assistant',
              content: 'Here is the answer.',
              sources: [
                {
                  title: 'Source Video',
                  youtubeId: 'xyz789',
                  timestamp: '1:30',
                  timestampSeconds: 90,
                },
              ],
            }),
          ]}
        />
      );

      expect(screen.getByText(/Sources \(\d+\)/)).toBeInTheDocument();
      expect(screen.getByText('Source Video')).toBeInTheDocument();
    });

    it('should not render sources section when empty', () => {
      render(
        <RAGChatPanel
          {...defaultProps}
          messages={[
            createMessage({
              role: 'assistant',
              content: 'No sources here.',
              sources: [],
            }),
          ]}
        />
      );

      expect(screen.queryByText('Sources')).not.toBeInTheDocument();
    });
  });

  describe('seek functionality', () => {
    it('should pass onSeek to message sources', () => {
      const onSeek = vi.fn();
      render(
        <RAGChatPanel
          {...defaultProps}
          onSeek={onSeek}
          messages={[
            createMessage({
              role: 'assistant',
              content: 'Answer with source.',
              sources: [
                {
                  title: 'Test Video',
                  youtubeId: 'test123',
                  timestampSeconds: 60,
                },
              ],
            }),
          ]}
        />
      );

      // The RAGSourceCard should receive onSeek
      const jumpButton = screen.getByRole('button', { name: /jump to/i });
      fireEvent.click(jumpButton);

      expect(onSeek).toHaveBeenCalledWith(60);
    });
  });

  describe('library-mode sources', () => {
    it('should render a navigable YouTube chip when youtubeId is set and no onSeek', () => {
      render(
        <RAGChatPanel
          {...defaultProps}
          messages={[
            createMessage({
              role: 'assistant',
              content: 'Cross-video answer.',
              sources: [
                {
                  title: 'Library Video',
                  youtubeId: 'lib123',
                  timestampSeconds: 42,
                },
              ],
            }),
          ]}
        />
      );

      const link = screen.getByRole('link', { name: /library video/i });
      expect(link).toHaveAttribute(
        'href',
        'https://www.youtube.com/watch?v=lib123&t=42s'
      );
    });

    it('should not render a seek button for library sources', () => {
      render(
        <RAGChatPanel
          {...defaultProps}
          messages={[
            createMessage({
              role: 'assistant',
              content: 'Cross-video answer.',
              sources: [
                { title: 'Library Video', youtubeId: 'lib123', timestampSeconds: 42 },
              ],
            }),
          ]}
        />
      );

      expect(screen.queryByRole('button', { name: /jump to/i })).not.toBeInTheDocument();
    });
  });

  describe('action confirmation', () => {
    it('should not render confirm/cancel buttons when no action is pending', () => {
      render(<RAGChatPanel {...defaultProps} />);

      expect(
        screen.queryByRole('button', { name: /yes, do it/i }),
      ).not.toBeInTheDocument();
    });

    it('should render confirm and cancel buttons when an action is pending', () => {
      render(<RAGChatPanel {...defaultProps} pendingAction />);

      expect(
        screen.getByRole('button', { name: /yes, do it/i }),
      ).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    it('should call onConfirmAction when the confirm button is clicked', () => {
      const onConfirmAction = vi.fn();
      render(
        <RAGChatPanel
          {...defaultProps}
          pendingAction
          onConfirmAction={onConfirmAction}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: /yes, do it/i }));

      expect(onConfirmAction).toHaveBeenCalledTimes(1);
    });

    it('should call onCancelAction when the cancel button is clicked', () => {
      const onCancelAction = vi.fn();
      render(
        <RAGChatPanel
          {...defaultProps}
          pendingAction
          onCancelAction={onCancelAction}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

      expect(onCancelAction).toHaveBeenCalledTimes(1);
    });

    it('should show the pending action summary when provided', () => {
      render(
        <RAGChatPanel
          {...defaultProps}
          pendingAction
          pendingSummary="Delete this folder AND every video inside it"
        />,
      );

      expect(
        screen.getByText('Delete this folder AND every video inside it'),
      ).toBeInTheDocument();
    });

    it('should not render a summary line when pendingSummary is omitted', () => {
      const { container } = render(
        <RAGChatPanel {...defaultProps} pendingAction />,
      );

      expect(
        container.querySelector('[data-slot="pending-summary"]'),
      ).not.toBeInTheDocument();
    });
  });

  describe('accessibility', () => {
    it('should have sr-only label on submit button', () => {
      render(<RAGChatPanel {...defaultProps} />);

      expect(screen.getByRole('button', { name: /send message/i })).toBeInTheDocument();
    });

    it('should have aria-hidden on icons', () => {
      const { container } = render(<RAGChatPanel {...defaultProps} />);

      const icons = container.querySelectorAll('svg[aria-hidden="true"]');
      expect(icons.length).toBeGreaterThan(0);
    });
  });
});
