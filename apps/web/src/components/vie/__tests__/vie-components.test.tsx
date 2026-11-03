/// <reference types="@testing-library/jest-dom" />
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';

import {
  GlassCard,
  ExpandableCard,
  HeroCard,
  ImageCard,
  ScoreRing,
  StatPill,
  Badge,
  KeyValue,
  CostDisplay,
  Timestamp,
  Celebration,
  CelebrationNextButton,
  FadeIn,
  InlineScore,
  Shake,
  ProgressBar,
  CrossTabButton,
  TabBar,
  SectionNav,
  Stepper,
  BackForward,
  CheckItem,
  FlipCard,
  OptionGrid,
  EmojiMarker,
  MapLink,
  CodeSnippet,
  ListItems,
  DefinitionItem,
  TextBlock,
  TableView,
  QuoteBlock,
} from '../index';

// ── Cards ──

describe('GlassCard', () => {
  it('should render children', () => {
    render(<GlassCard>Hello</GlassCard>);
    expect(screen.getByText('Hello')).toBeInTheDocument();
  });

  it('should apply variant class', () => {
    const { container } = render(<GlassCard variant="elevated">Test</GlassCard>);
    // Elevated uses the `--glass-shadow-elevated` token (falls back to
    // `--glass-shadow`) instead of the raw `shadow-lg` utility.
    expect(container.firstChild).toHaveClass(
      'shadow-[var(--glass-shadow-elevated,var(--glass-shadow))]',
    );
  });

  it('should accept className', () => {
    const { container } = render(<GlassCard className="custom">Test</GlassCard>);
    expect(container.firstChild).toHaveClass('custom');
  });
});

describe('ExpandableCard', () => {
  it('should toggle content on click', async () => {
    const user = userEvent.setup();
    render(
      <ExpandableCard header={<span>Header</span>}>
        <span>Content</span>
      </ExpandableCard>,
    );

    // Content is always mounted (animated via grid-template-rows 0fr → 1fr).
    // Use aria-expanded on the header button to assert collapsed/expanded.
    const trigger = screen.getByRole('button', { expanded: false });
    expect(trigger).toBeInTheDocument();
    expect(screen.getByText('Content')).toBeInTheDocument();

    await user.click(screen.getByText('Header'));
    expect(screen.getByRole('button', { expanded: true })).toBeInTheDocument();
  });

  it('should start expanded when defaultExpanded', () => {
    render(
      <ExpandableCard header={<span>Header</span>} defaultExpanded>
        <span>Content</span>
      </ExpandableCard>,
    );
    expect(screen.getByText('Content')).toBeInTheDocument();
  });
});

describe('HeroCard', () => {
  it('should render title and subtitle', () => {
    render(<HeroCard title="Title" subtitle="Subtitle" emoji="🎉" />);
    expect(screen.getByText('Title')).toBeInTheDocument();
    expect(screen.getByText('Subtitle')).toBeInTheDocument();
    expect(screen.getByText('🎉')).toBeInTheDocument();
  });

  it('should render default variant as start-aligned (logical) with inline emoji', () => {
    const { container } = render(<HeroCard title="Title" emoji="🎉" />);
    const card = container.firstElementChild as HTMLElement;
    expect(card.className).toContain('text-start');
    // Default emoji is small (text-xl), not large (text-4xl)
    expect(screen.getByText('🎉').className).toContain('text-xl');
  });

  it('should render marketing variant as centered with large emoji', () => {
    const { container } = render(
      <HeroCard variant="marketing" title="Title" emoji="🎉" />,
    );
    const card = container.firstElementChild as HTMLElement;
    expect(card.className).toContain('text-center');
    expect(screen.getByText('🎉').className).toContain('text-4xl');
  });
});

describe('ImageCard', () => {
  it('should render image with alt text', () => {
    render(<ImageCard src="/test.jpg" alt="Test image" />);
    expect(screen.getByAltText('Test image')).toBeInTheDocument();
  });
});

// ── Data Display ──

describe('ScoreRing', () => {
  it('should render percentage display', () => {
    render(<ScoreRing score={85} />);
    expect(screen.getByText('85%')).toBeInTheDocument();
  });

  it('should render fraction display', () => {
    render(<ScoreRing score={7} total={10} />);
    expect(screen.getByText('7.0')).toBeInTheDocument();
  });

  it('should render label', () => {
    render(<ScoreRing score={50} label="Progress" />);
    expect(screen.getByText('Progress')).toBeInTheDocument();
  });

  it('should have accessible aria-label', () => {
    render(<ScoreRing score={85} label="Test" />);
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'Score: 85% — Test');
  });
});

describe('StatPill', () => {
  it('should render value and label', () => {
    render(<StatPill value="42" label="Users" />);
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('Users')).toBeInTheDocument();
  });

  it('should render context', () => {
    render(<StatPill value="99" label="Score" context="Top performer" />);
    expect(screen.getByText('Top performer')).toBeInTheDocument();
  });
});

describe('Badge', () => {
  it('should render children', () => {
    render(<Badge>Status</Badge>);
    expect(screen.getByText('Status')).toBeInTheDocument();
  });
});

describe('KeyValue', () => {
  it('should render key-value pairs', () => {
    render(<KeyValue items={[{ key: 'Name', value: 'John' }, { key: 'Age', value: '30' }]} />);
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('John')).toBeInTheDocument();
    expect(screen.getByText('Age')).toBeInTheDocument();
    expect(screen.getByText('30')).toBeInTheDocument();
  });

  it('should return null for empty items', () => {
    const { container } = render(<KeyValue items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('CostDisplay', () => {
  it('should render amount with currency', () => {
    render(<CostDisplay amount={1500} currency="€" label="Total" />);
    // Currency and amount render in the same span separated by whitespace
    // (the count-up renders as "€ 1,500" with a non-breaking-width space).
    const amount = screen.getByText((_, el) => el?.textContent === '€ 1,500');
    expect(amount).toBeInTheDocument();
    expect(screen.getByText('Total')).toBeInTheDocument();
  });
});

describe('Timestamp', () => {
  it('should render formatted time', () => {
    render(<Timestamp seconds={125} />);
    expect(screen.getByText('2:05')).toBeInTheDocument();
  });

  it('should be clickable when onClick provided', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Timestamp seconds={60} onClick={onClick} />);
    await user.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalled();
  });
});

// ── Feedback ──

describe('Celebration', () => {
  it('should render emoji and title', () => {
    render(<Celebration emoji="🎉" title="Done!" />);
    expect(screen.getByText('🎉')).toBeInTheDocument();
    expect(screen.getByText('Done!')).toBeInTheDocument();
  });

  it('should render action slot', () => {
    render(
      <Celebration
        emoji="✅"
        title="Complete"
        action={<button>Next</button>}
      />,
    );
    expect(screen.getByText('Next')).toBeInTheDocument();
  });

  it('should have alert role', () => {
    render(<Celebration emoji="🎉" title="Done!" />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});

describe('CelebrationNextButton', () => {
  it('should call onClick', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<CelebrationNextButton label="Continue" onClick={onClick} />);
    await user.click(screen.getByText('Continue'));
    expect(onClick).toHaveBeenCalled();
  });
});

describe('FadeIn', () => {
  it('should render children', () => {
    render(<FadeIn>Content</FadeIn>);
    expect(screen.getByText('Content')).toBeInTheDocument();
  });

  it('should apply stagger delay', () => {
    // Stagger step is 60ms per index (was 75ms — tightened in the UX pass).
    const { container } = render(<FadeIn index={2}>Content</FadeIn>);
    expect(container.firstChild).toHaveStyle({ animationDelay: '120ms' });
  });
});

describe('InlineScore', () => {
  it('should display score ratio', () => {
    render(<InlineScore correct={3} total={5} />);
    expect(screen.getByText('3/5')).toBeInTheDocument();
  });
});

describe('Shake', () => {
  it('should render children', () => {
    render(<Shake active={false}>Content</Shake>);
    expect(screen.getByText('Content')).toBeInTheDocument();
  });
});

// ── Navigation ──

describe('ProgressBar', () => {
  it('should render percentage', () => {
    render(<ProgressBar value={3} max={10} />);
    expect(screen.getByText('30%')).toBeInTheDocument();
  });

  it('should have progressbar role', () => {
    render(<ProgressBar value={5} max={10} />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });
});

describe('CrossTabButton', () => {
  it('should render label and call onClick', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<CrossTabButton label="See more" onClick={onClick} />);
    // Label no longer carries an inline arrow char — a ChevronRight icon
    // sits beside it now.
    await user.click(screen.getByText('Next: See more'));
    expect(onClick).toHaveBeenCalled();
  });
});

describe('TabBar', () => {
  const tabs = [
    { id: 'a', label: 'Tab A', emoji: '📝' },
    { id: 'b', label: 'Tab B', emoji: '🔧' },
  ];

  it('should render all tabs', () => {
    render(<TabBar tabs={tabs} activeId="a" onTabChange={vi.fn()} />);
    expect(screen.getByText('Tab A')).toBeInTheDocument();
    expect(screen.getByText('Tab B')).toBeInTheDocument();
  });

  it('should call onTabChange when clicked', async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();
    render(<TabBar tabs={tabs} activeId="a" onTabChange={onTabChange} />);
    await user.click(screen.getByText('Tab B'));
    expect(onTabChange).toHaveBeenCalledWith('b');
  });

  it('should mark active tab as selected', () => {
    render(<TabBar tabs={tabs} activeId="a" onTabChange={vi.fn()} />);
    expect(screen.getByRole('tab', { name: /Tab A/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /Tab B/i })).toHaveAttribute('aria-selected', 'false');
  });
});

describe('SectionNav', () => {
  const sections = [{ id: 'a', label: 'Day 1' }, { id: 'b', label: 'Day 2' }, { id: 'c', label: 'Day 3' }];

  it('should render sections and call onSelect', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<SectionNav sections={sections} activeId="a" onSelect={onSelect} />);
    await user.click(screen.getByText('Day 2'));
    expect(onSelect).toHaveBeenCalledWith('b');
  });

  it('should scroll as a chip row with the scrollbar hidden, fade edges and snapping', () => {
    render(<SectionNav sections={sections} activeId="a" onSelect={vi.fn()} />);
    const nav = screen.getByRole('navigation', { name: 'Sections' });
    expect(nav.className).toContain('overflow-x-auto');
    expect(nav.className).toContain('scrollbar-hide');
    expect(nav.className).toContain('scroll-fade-x');
    expect(nav.className).toContain('snap-x');
    // `scrollbar-none` is not a utility this app defines — it showed a raw scrollbar.
    expect(nav.className).not.toContain('scrollbar-none');
    for (const chip of screen.getAllByRole('button')) expect(chip.className).toContain('snap-center');
  });

  it('should scroll the active chip into view when the active section changes, not on first paint', () => {
    const scrollIntoView = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    scrollIntoView.mockClear();
    const { rerender } = render(<SectionNav sections={sections} activeId="a" onSelect={vi.fn()} />);
    // Mount must not scroll: a nav below the fold would otherwise pull the page down.
    expect(scrollIntoView).not.toHaveBeenCalled();

    rerender(<SectionNav sections={sections} activeId="c" onSelect={vi.fn()} />);

    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.instances[0]).toBe(screen.getByRole('button', { name: 'Day 3' }));
    expect(scrollIntoView.mock.calls[0][0]).toMatchObject({ inline: 'center', block: 'nearest' });
  });
});

describe('Stepper', () => {
  it('should render correct number of steps', () => {
    render(<Stepper total={5} current={2} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(5);
  });
});

describe('BackForward', () => {
  it('should call onBack and onForward', async () => {
    const user = userEvent.setup();
    const onBack = vi.fn();
    const onForward = vi.fn();
    render(<BackForward onBack={onBack} onForward={onForward} />);
    await user.click(screen.getByText('Previous'));
    expect(onBack).toHaveBeenCalled();
    await user.click(screen.getByText('Next'));
    expect(onForward).toHaveBeenCalled();
  });

  it('should disable buttons when specified', () => {
    render(<BackForward onBack={vi.fn()} onForward={vi.fn()} backDisabled forwardDisabled />);
    expect(screen.getByText('Previous').closest('button')).toBeDisabled();
    expect(screen.getByText('Next').closest('button')).toBeDisabled();
  });
});

// ── Interactive Primitives ──

describe('CheckItem', () => {
  it('should render label and toggle', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<CheckItem label="Item 1" checked={false} onToggle={onToggle} />);
    expect(screen.getByText('Item 1')).toBeInTheDocument();
    await user.click(screen.getByRole('button'));
    expect(onToggle).toHaveBeenCalled();
  });
});

describe('FlipCard', () => {
  it('should show front initially and flip on click', async () => {
    const user = userEvent.setup();
    render(<FlipCard front="Question" back="Answer" />);
    expect(screen.getByText('Question')).toBeInTheDocument();
    await user.click(screen.getByRole('button'));
    // Both sides are always in DOM, just visually flipped via CSS
    expect(screen.getByText('Answer')).toBeInTheDocument();
  });
});

describe('OptionGrid', () => {
  it('should render options and call onSelect', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<OptionGrid options={['A', 'B', 'C']} onSelect={onSelect} />);
    expect(screen.getByText('A')).toBeInTheDocument();
    await user.click(screen.getByText('B'));
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});

describe('EmojiMarker', () => {
  it('should render emoji with aria-hidden', () => {
    render(<EmojiMarker emoji="🔥" />);
    expect(screen.getByText('🔥')).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('MapLink', () => {
  it('should render link to Google Maps', () => {
    render(<MapLink name="Paris" />);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', expect.stringContaining('maps.google.com'));
    expect(link).toHaveAttribute('target', '_blank');
  });
});

// ── Content ──

describe('CodeSnippet', () => {
  it('should render code text', () => {
    render(<CodeSnippet code="const x = 1;" />);
    expect(screen.getByText('const x = 1;')).toBeInTheDocument();
  });

  it('should return null for empty code', () => {
    const { container } = render(<CodeSnippet code="" />);
    expect(container.firstChild).toBeNull();
  });
});

describe('ListItems', () => {
  it('should render list items', () => {
    render(<ListItems items={['Item 1', 'Item 2']} />);
    expect(screen.getByText('Item 1')).toBeInTheDocument();
    expect(screen.getByText('Item 2')).toBeInTheDocument();
  });

  it('should return null for empty items', () => {
    const { container } = render(<ListItems items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('DefinitionItem', () => {
  it('should render term and meaning', () => {
    render(<DefinitionItem term="API" meaning="Application Programming Interface" />);
    expect(screen.getByText('API')).toBeInTheDocument();
    expect(screen.getByText('Application Programming Interface')).toBeInTheDocument();
  });
});

describe('TextBlock', () => {
  it('should render text content', () => {
    render(<TextBlock>Important note</TextBlock>);
    expect(screen.getByText('Important note')).toBeInTheDocument();
  });
});

describe('TableView', () => {
  it('should render table with data', () => {
    render(
      <TableView
        columns={[{ key: 'name', label: 'Name' }]}
        rows={[{ name: 'Alice' }]}
      />,
    );
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
  });

  it('should return null for empty data', () => {
    const { container } = render(<TableView columns={[]} rows={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('QuoteBlock', () => {
  it('should render quote text and attribution', () => {
    render(<QuoteBlock text="To be or not to be" attribution="Shakespeare" />);
    expect(screen.getByText('To be or not to be')).toBeInTheDocument();
    expect(screen.getByText('— Shakespeare')).toBeInTheDocument();
  });
});
