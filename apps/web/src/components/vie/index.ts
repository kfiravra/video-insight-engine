// ═══════════════════════════════════════════════════
// VIE Component Library — Barrel Exports
// Props-first, domain-free presentational components.
// ═══════════════════════════════════════════════════

// Cards & Surfaces
export { GlassCard } from './cards/GlassCard';
export { ExpandableCard } from './cards/ExpandableCard';
export { HeroCard } from './cards/HeroCard';
export { ImageCard } from './cards/ImageCard';
export { VideoHero } from './cards/VideoHero';

// Content Blocks
export { TextBlock } from './content/TextBlock';
export { CodeSnippet } from './content/CodeSnippet';
export { QuoteBlock } from './content/QuoteBlock';
export { TableView } from './content/TableView';
export { ListItems } from './content/ListItems';
export { DefinitionItem } from './content/DefinitionItem';

// Data Display
export { ScoreRing } from './data/ScoreRing';
export { StatPill } from './data/StatPill';
export { Badge } from './data/Badge';
export { detectBadgeVariant } from './data/badge-colors';
export { KeyValue } from './data/KeyValue';
export { CostDisplay } from './data/CostDisplay';
export { Timer } from './data/Timer';
export { Timestamp } from './data/Timestamp';
export { SeekChip } from './data/SeekChip';
export { VisualEvidence, EvidenceImage, type VisualEvidenceProps } from './data/VisualEvidence';

// Feedback & Animation
export { Callout } from './feedback/Callout';
export { Celebration, CelebrationNextButton } from './feedback/Celebration';
export { FadeIn } from './feedback/FadeIn';
export { InlineScore } from './feedback/InlineScore';
export { Shake } from './feedback/Shake';

// Interactive Primitives
export { CheckItem } from './interactive/CheckItem';
export { FlipCard } from './interactive/FlipCard';
export { OptionGrid } from './interactive/OptionGrid';
export { ActionButton } from './interactive/ActionButton';
export { EmojiMarker } from './interactive/EmojiMarker';
export { MapLink } from './interactive/MapLink';

// Media
export { ImageGallery } from './media/ImageGallery';
export { Lightbox } from '../ui/Lightbox';
export type { LightboxFrame } from '../ui/Lightbox';

// Navigation
export { ProgressBar } from './navigation/ProgressBar';
export { CrossTabButton } from './navigation/CrossTabButton';
export { TabBar } from './navigation/TabBar';
export { SectionNav } from './navigation/SectionNav';
export { Stepper } from './navigation/Stepper';
export { BackForward } from './navigation/BackForward';
export { AccentLane, type AccentLaneState } from './navigation/AccentLane';
export {
  VieMenu,
  VieMenuItem,
  VieMenuSeparator,
  VieMenuDestructiveDivider,
  VieMenuHeader,
  VieMenuSub,
} from './navigation/VieMenu';
