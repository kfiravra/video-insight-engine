import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { DirectionProvider } from '@/contexts/DirectionContext';
import { VieMenu } from '@/components/vie';
import { UsageMenuItem } from '../UsageMenuItem';

function LocationProbe() {
  return <output data-testid="path">{useLocation().pathname}</output>;
}

function renderMenu(language = 'en') {
  return render(
    <MemoryRouter initialEntries={['/board']}>
      <DirectionProvider language={language} isRTL={language === 'he' || language === 'ar'}>
        <VieMenu open trigger={<button type="button">Profile</button>}>
          <UsageMenuItem />
        </VieMenu>
      </DirectionProvider>
      <LocationProbe />
    </MemoryRouter>,
  );
}

describe('UsageMenuItem', () => {
  it('should be a menu item, which the menu can focus with the keyboard', () => {
    renderMenu();

    expect(screen.getByRole('menuitem', { name: 'Minutes & plan' })).toBeInTheDocument();
  });

  it('should not be a link inside the menu', () => {
    renderMenu();

    expect(screen.queryByRole('link')).toBeNull();
  });

  it('should open the billing page when selected with Enter', async () => {
    const user = userEvent.setup();
    renderMenu();
    screen.getByRole('menuitem', { name: 'Minutes & plan' }).focus();

    await user.keyboard('{Enter}');

    expect(screen.getByTestId('path')).toHaveTextContent('/settings/billing');
  });

  it('should open the billing page when clicked', async () => {
    renderMenu();

    await userEvent.setup().click(screen.getByRole('menuitem', { name: 'Minutes & plan' }));

    expect(screen.getByTestId('path')).toHaveTextContent('/settings/billing');
  });

  it('should be labelled in Hebrew under a Hebrew direction context', () => {
    renderMenu('he');

    expect(screen.getByRole('menuitem', { name: 'דקות ומסלול' })).toBeInTheDocument();
  });
});
