import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { App } from '../src/app/App';
import { RouteLink } from '../src/shared/ui/RouteLink';

it('renders application destinations as real hash links', () => {
  render(<RouteLink to="/atlas">Open atlas</RouteLink>);
  expect(screen.getByRole('link').getAttribute('href')).toBe('#/atlas');
});

it('moves keyboard focus to content without changing the route', () => {
  window.location.hash = '#/';
  render(<App />);
  fireEvent.click(screen.getByRole('button', { name: 'К содержимому' }));
  expect(document.activeElement?.id).toBe('main');
  expect(window.location.hash).toBe('#/');
});
