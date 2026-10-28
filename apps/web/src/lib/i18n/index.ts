import { useMemo } from 'react';
import { useDirection } from '@/contexts/DirectionContext';
import { AR } from './ar';
import { EN } from './en';
import { HE } from './he';
import type { Labels } from './labels';

export type { BillingLabels, DurationLabels, Labels, PluralForms } from './labels';

const TRANSLATIONS: Record<string, Labels> = {
  en: EN,
  he: HE,
  ar: AR,
};

/** Get labels for a given ISO 639-1 language code. Falls back to English. */
export function getLabels(language: string): Labels {
  return TRANSLATIONS[language] ?? EN;
}

/** Hook that returns translated labels based on the current DirectionContext language. */
export function useLabels(): Labels {
  const { language } = useDirection();
  return useMemo(() => getLabels(language), [language]);
}
