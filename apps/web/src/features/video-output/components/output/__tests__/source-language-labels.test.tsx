import { useState, type ReactNode } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { DirectionProvider } from '@/contexts/DirectionContext';
import { TabStateProvider } from '@/features/video-output/contexts/TabStateContext';
import { ListenStepView, StudyStepView, type ListenSegment } from '../RecipeStepView';
import { ComparisonInteractive } from '../interactive/ComparisonInteractive';
import { ConceptCanvas } from '../interactive/ConceptCanvas';
import { StepFlowCanvas } from '../interactive/StepFlowCanvas';
import type { ConceptItem, QuizArenaQuestion, StepItem } from '@vie/types';

// pC review: the chrome added in this task reads in the page's language, so a
// Hebrew page (the source-language view of a Hebrew video) shows no English
// control or caption among its Hebrew content.

function hebrew(ui: ReactNode): ReactNode {
  return (
    <DirectionProvider language="he" isRTL>
      <TabStateProvider videoId="he-labels-test">{ui}</TabStateProvider>
    </DirectionProvider>
  );
}

const QUESTION: QuizArenaQuestion = {
  question: 'מה הבעיה האמיתית?',
  options: ['מי נתן לו להחליט', 'מה הוא החליט'],
  correctIndex: 0,
  explanation: 'הדובר אומר זאת במפורש.',
  timestamp: 181,
};

function Study() {
  const [step, setStep] = useState(0);
  return <StudyStepView questions={[QUESTION]} currentStep={step} onStepChange={setStep} onComplete={vi.fn()} onSeek={vi.fn()} />;
}

function Listen({ segments }: { segments: ListenSegment[] }) {
  const [step, setStep] = useState(0);
  return <ListenStepView segments={segments} currentStep={step} onStepChange={setStep} onComplete={vi.fn()} />;
}

const CONCEPTS: ConceptItem[] = [
  { name: 'LangGraph', definition: 'גרף של צעדים שיכול לחזור אחורה.', example: 'סוכן שמנסה שוב.', analogy: 'מפת רכבת.', connections: [] },
];

const STEPS: StepItem[] = [
  { number: 1, title: 'התקנה', instruction: 'מתקינים את הספרייה.' },
  { number: 2, title: 'הרצה', instruction: 'מריצים את הסוכן.' },
];

const PRICED = [
  { feature: 'Price', thisProduct: '$249', competitor: '$299', competitorName: 'Sony', winner: 'left' as const },
  { feature: 'Battery', thisProduct: '6 h', competitor: '8 h', competitorName: 'Sony', winner: 'right' as const },
  { feature: 'Weight', thisProduct: '5 g', competitor: '6 g', competitorName: 'Sony' },
];

describe('source-language page labels (Hebrew)', () => {
  it('should name the Study reveal button in Hebrew when the output is Hebrew', () => {
    render(hebrew(<Study />));

    expect(screen.getByRole('button', { name: 'חשפו את התשובה' })).toBeInTheDocument();
  });

  it('should name the Study "watch this part" button in Hebrew once the answer is revealed', async () => {
    const user = userEvent.setup();
    render(hebrew(<Study />));

    await user.click(screen.getByRole('button', { name: 'חשפו את התשובה' }));

    expect(screen.getByRole('button', { name: /צפו בקטע הזה/ })).toBeInTheDocument();
  });

  it('should say a Listen play range in Hebrew when the output is Hebrew', () => {
    render(hebrew(<Listen segments={[{ label: 'פתיחה', seconds: 148, endSeconds: 344 }]} />));

    expect(screen.getByText('נגן 2:28 – 5:44')).toBeInTheDocument();
  });

  it('should say an open-ended Listen range in Hebrew when the segment has no end', () => {
    render(hebrew(<Listen segments={[{ label: 'סיום', seconds: 344 }]} />));

    expect(screen.getByText('נגן מ־5:44')).toBeInTheDocument();
  });

  it('should name the concept canvas views in Hebrew when the output is Hebrew', () => {
    render(hebrew(<ConceptCanvas concepts={CONCEPTS} />));

    expect(screen.getByRole('button', { name: 'מפה' })).toBeInTheDocument();
  });

  it('should label a concept example and analogy in Hebrew when the output is Hebrew', () => {
    render(hebrew(<ConceptCanvas concepts={CONCEPTS} />));

    expect([screen.getByText('דוגמה'), screen.getByText('אנלוגיה')]).toHaveLength(2);
  });

  it('should name the step canvas views in Hebrew when the output is Hebrew', () => {
    render(hebrew(<StepFlowCanvas steps={STEPS} />));

    expect(screen.getByRole('button', { name: 'תרשים' })).toBeInTheDocument();
  });

  it('should explain a lower-is-better radar spoke in Hebrew when the output is Hebrew', () => {
    render(hebrew(<ComparisonInteractive comparisons={PRICED} view="radar" />));

    expect(screen.getByText('רחוק יותר מהמרכז = טוב יותר — הערך הנמוך מנצח ב: Price.')).toBeInTheDocument();
  });
});
