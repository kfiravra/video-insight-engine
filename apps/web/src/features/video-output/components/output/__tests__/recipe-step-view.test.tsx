import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabStateProvider } from '@/features/video-output/contexts/TabStateContext';
import { RecipeStepView } from '../RecipeStepView';
import type { StepItem } from '@vie/types';

const STEPS: StepItem[] = [
  { number: 1, instruction: 'Rinse the rice under cold water.', duration: '3' },
  { number: 2, instruction: 'Cover and simmer.', duration: '10' },
];

function RecipeStepHarness() {
  const [currentStep, setCurrentStep] = useState(0);
  return (
    <TabStateProvider videoId="recipe-step-test">
      <RecipeStepView steps={STEPS} currentStep={currentStep} onStepChange={setCurrentStep} onComplete={vi.fn()} />
    </TabStateProvider>
  );
}

describe('RecipeStepView', () => {
  describe('step timer', () => {
    it('should count a bare-number duration in minutes when the step has no unit', () => {
      render(<RecipeStepHarness />);
      expect(screen.getByText('3:00')).toBeInTheDocument();
    });

    it("should restart the timer from the next step's duration when the step changes", async () => {
      const user = userEvent.setup();
      render(<RecipeStepHarness />);
      await user.click(screen.getByRole('button', { name: /Next/ }));
      expect(screen.getByText('10:00')).toBeInTheDocument();
    });
  });
});
