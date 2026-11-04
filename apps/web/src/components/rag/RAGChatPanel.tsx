import { memo, useState, useCallback, useRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { MessageCircle, Send, Loader2, User, Bot, Sparkles, ExternalLink, Check, SquarePen, Film, Library, Coins } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { ScrollContainer } from '@/components/ui/scroll-container';
import { useLabels } from '@/lib/i18n';
import { RAGSourceCard } from './RAGSourceCard';
import { MarkdownContent } from '@/components/ui/markdown-content';

interface RAGSource {
  title: string;
  youtubeId: string;
  thumbnailUrl?: string;
  timestamp?: string;
  timestampSeconds?: number;
  relevanceScore?: number;
}

interface RAGMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: RAGSource[];
  /** Agent tool steps (e.g. "Created folder \"Series\"") shown as ✓ lines. */
  steps?: string[];
  isStreaming?: boolean;
  /** A billing refusal (402 / 403): the reply links to /settings/billing. */
  billingLink?: boolean;
  createdAt: string;
}

type ChatStatus = 'idle' | 'pending' | 'streaming' | 'error';

interface RAGChatPanelProps {
  messages: RAGMessage[];
  /** @deprecated Use `status` instead */
  isLoading?: boolean;
  status?: ChatStatus;
  onSendMessage: (message: string) => void;
  onSeek?: (seconds: number) => void;
  placeholder?: string;
  className?: string;
  /** True when an assistant action is awaiting the user's confirmation. The
   * confirm prompt itself is rendered as a normal assistant message; this flag
   * surfaces the Confirm/Cancel buttons that resolve it. */
  pendingAction?: boolean;
  /** Server-provided one-line description of the parked destructive/costly
   * action (e.g. "Delete this folder AND every video inside it"), shown above
   * the Confirm/Cancel buttons so the user knows exactly what they approve. */
  pendingSummary?: string;
  onConfirmAction?: () => void;
  onCancelAction?: () => void;
  /** Clears the conversation and starts a fresh chat. Disabled when empty. */
  onNewChat?: () => void;
  /** What the assistant is grounded in this turn: a single open video, or the
   * whole library. Surfaced as a header chip so the user knows the chat's scope
   * (both scopes can also act on the library — create/move/organize). */
  scope?: 'video' | 'library';
}

/**
 * Deep-linking source chip. Rendered when no in-page seek handler is available
 * (e.g. the sidebar has no embedded player), so the cited source stays a real
 * navigable target — a YouTube link at the timestamp — never a broken internal
 * link. Used in both single-video and library (cross-video) modes; whenever an
 * `onSeek` handler IS present the seek-enabled RAGSourceCard is used instead.
 */
const LibrarySourceChip = memo(function LibrarySourceChip({
  source,
}: {
  source: RAGSource;
}) {
  const { youtubeId, title, timestamp, timestampSeconds, relevanceScore } = source;
  const youtubeUrl = timestampSeconds
    ? `https://www.youtube.com/watch?v=${youtubeId}&t=${timestampSeconds}s`
    : `https://www.youtube.com/watch?v=${youtubeId}`;

  return (
    <a
      href={youtubeUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-center gap-1.5 rounded-md border border-border/20 bg-muted/20 px-2 py-1.5 text-xs hover:bg-muted/40 transition-colors"
    >
      <ExternalLink className="h-3 w-3 shrink-0 text-primary" aria-hidden="true" />
      <span className="line-clamp-1 flex-1 min-w-0 font-medium">{title}</span>
      {timestamp && (
        <span className="shrink-0 text-muted-foreground">{timestamp}</span>
      )}
      {relevanceScore !== undefined && (
        <span className="shrink-0 text-muted-foreground">
          {Math.round(relevanceScore * 100)}%
        </span>
      )}
    </a>
  );
});

const MessageBubble = memo(function MessageBubble({
  message,
  onSeek,
}: {
  message: RAGMessage;
  onSeek?: (seconds: number) => void;
}) {
  const isUser = message.role === 'user';
  const { billing } = useLabels();

  return (
    <div
      className={cn(
        'flex gap-3',
        isUser ? 'flex-row-reverse' : 'flex-row'
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          'w-8 h-8 rounded-full flex items-center justify-center shrink-0',
          isUser ? 'bg-primary text-primary-foreground' : 'bg-muted'
        )}
      >
        {isUser ? (
          <User className="h-4 w-4" aria-hidden="true" />
        ) : (
          <Bot className="h-4 w-4" aria-hidden="true" />
        )}
      </div>

      {/* Content */}
      <div
        className={cn(
          'flex-1 max-w-[85%] space-y-2',
          isUser && 'text-right'
        )}
      >
        <div
          className={cn(
            'inline-block rounded-lg px-3 py-2 text-sm',
            isUser
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted'
          )}
        >
          {isUser ? (
            message.content
          ) : (
            <>
              {/* Agent tool steps — muted ✓ lines above the reply. */}
              {message.steps && message.steps.length > 0 && (
                <ul className="mb-1.5 space-y-0.5 text-xs text-muted-foreground">
                  {message.steps.map((step, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <Check
                        className="h-3 w-3 mt-0.5 shrink-0"
                        aria-hidden="true"
                      />
                      <span>{step}</span>
                    </li>
                  ))}
                </ul>
              )}
              <MarkdownContent
                content={message.content}
                className={cn(
                  'text-sm',
                  // Sidebar-tuned hierarchy: tight + scannable, no oversized headings.
                  'prose-p:my-1 prose-p:leading-relaxed',
                  'prose-headings:mt-3 prose-headings:mb-1 prose-headings:font-semibold',
                  'prose-h1:text-sm prose-h2:text-sm prose-h3:text-xs',
                  'prose-ul:my-1 prose-ul:space-y-0.5 prose-ol:my-1 prose-ol:space-y-0.5',
                  'prose-li:my-0 prose-strong:text-foreground',
                  'prose-code:text-xs prose-pre:text-xs prose-pre:p-2',
                )}
              />
              {message.billingLink && (
                <Link
                  to="/settings/billing"
                  data-testid="chat-billing-link"
                  className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-primary underline-offset-4 hover:underline"
                >
                  <Coins className="h-3 w-3 shrink-0" aria-hidden="true" />
                  {billing.manageLink}
                </Link>
              )}
            </>
          )}
          {message.isStreaming && (
            <span className="inline-block ml-1 animate-pulse">▌</span>
          )}
        </div>

        {/* Sources (assistant only) — collapsed by default to keep the chat clean */}
        {!isUser && message.sources && message.sources.length > 0 && (
          <details className="mt-2">
            <summary className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer select-none list-none hover:text-foreground/80">
              <Sparkles className="h-3 w-3" aria-hidden="true" />
              <span>Sources ({message.sources.length})</span>
            </summary>
            <div className="mt-1.5 space-y-1">
              {message.sources.map((source, i) =>
                // With a seek handler, use the seek-enabled card; without one
                // (no in-page player), deep-link the source to YouTube so it
                // stays navigable. Applies to single-video and library modes.
                !onSeek && source.youtubeId ? (
                  <LibrarySourceChip key={i} source={source} />
                ) : (
                  <RAGSourceCard key={i} {...source} onSeek={onSeek} />
                ),
              )}
            </div>
          </details>
        )}
      </div>
    </div>
  );
});

/**
 * RAG Chat Panel for conversational Q&A over video content.
 * Supports streaming responses and source attribution.
 */
export const RAGChatPanel = memo(function RAGChatPanel({
  messages,
  isLoading,
  status: statusProp,
  onSendMessage,
  onSeek,
  placeholder,
  className,
  pendingAction = false,
  pendingSummary,
  onConfirmAction,
  onCancelAction,
  onNewChat,
  scope,
}: RAGChatPanelProps) {
  const labels = useLabels();
  const effectivePlaceholder = placeholder ?? labels.libraryChatPlaceholder;
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);

  // Derive status from prop or backward-compatible isLoading
  const status: ChatStatus = statusProp ?? (isLoading ? 'pending' : 'idle');
  const isBusy = status === 'pending' || status === 'streaming';

  // Smart auto-scroll: only scroll if user hasn't scrolled up
  const scrollToBottom = useCallback(() => {
    if (!userScrolledUpRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages.length, scrollToBottom]);

  // Detect manual scroll-up via the forwarded scroll container ref
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const handleScroll = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      userScrolledUpRef.current = !atBottom;
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  // Auto-resize textarea
  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, []);

  const handleSubmit = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isBusy) return;

    onSendMessage(trimmed);
    setInput('');
    userScrolledUpRef.current = false;

    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    textareaRef.current?.focus();
  }, [input, isBusy, onSendMessage]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
    // Shift+Enter → natural newline (default behavior)
  }, [handleSubmit]);

  const handleFormSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    handleSubmit();
  }, [handleSubmit]);

  return (
    <div className={cn('flex flex-col h-full', className)}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b">
        <MessageCircle className="h-5 w-5 text-primary shrink-0" aria-hidden="true" />
        <h3 className="font-medium">Chat with your knowledge</h3>
        {scope && (
          <span
            className="inline-flex items-center gap-1 rounded-full border border-border/40 bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground"
            title={
              scope === 'video'
                ? 'Grounded in the open video — can also organize your library'
                : 'Spanning your whole library — can also organize it'
            }
          >
            {scope === 'video' ? (
              <Film className="h-3 w-3 shrink-0" aria-hidden="true" />
            ) : (
              <Library className="h-3 w-3 shrink-0" aria-hidden="true" />
            )}
            <span>{scope === 'video' ? 'This video' : 'Your library'}</span>
          </span>
        )}
        {onNewChat && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="ms-auto h-8 w-8"
            onClick={onNewChat}
            disabled={messages.length === 0}
            aria-label="New chat"
          >
            <SquarePen className="h-4 w-4" aria-hidden="true" />
          </Button>
        )}
      </div>

      {/* Messages with aria-live for screen readers */}
      <ScrollContainer wrapperClassName="flex-1 min-h-0" className="p-4 space-y-4" ref={scrollContainerRef}>
        <div aria-live="polite" aria-relevant="additions">
          {messages.length === 0 ? (
            <div className="h-full flex items-center justify-center text-center text-muted-foreground">
              <div className="space-y-2">
                <Sparkles className="h-10 w-10 mx-auto opacity-30" aria-hidden="true" />
                <p className="text-sm">{labels.libraryChatEmptyTitle}</p>
                <p className="text-xs max-w-[200px]">{labels.libraryChatEmptyBody}</p>
              </div>
            </div>
          ) : (
            messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                onSeek={onSeek}
              />
            ))
          )}
        </div>
        <div ref={messagesEndRef} />
      </ScrollContainer>

      {/* Action confirmation — the prompt is rendered as an assistant message
          above; these buttons resolve the pending action. */}
      {pendingAction && (
        <div
          role="group"
          aria-label={labels.actionConfirmPrompt.replace('{action}', '')}
          className="px-4 py-3 border-t space-y-2"
        >
          {pendingSummary && (
            <p
              data-slot="pending-summary"
              className="text-xs text-muted-foreground"
            >
              {pendingSummary}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="button" size="sm" onClick={onConfirmAction}>
              {labels.actionConfirm}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onCancelAction}
            >
              {labels.actionCancel}
            </Button>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="p-4 border-t">
        <form onSubmit={handleFormSubmit} className="flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              resizeTextarea();
            }}
            onKeyDown={handleKeyDown}
            aria-label={effectivePlaceholder}
            placeholder={effectivePlaceholder}
            disabled={isBusy}
            rows={1}
            className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          />
          <Button type="submit" disabled={!input.trim() || isBusy}>
            {isBusy ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Send className="h-4 w-4" aria-hidden="true" />
            )}
            <span className="sr-only">Send message</span>
          </Button>
        </form>
      </div>
    </div>
  );
});
