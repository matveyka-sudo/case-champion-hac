import type { ComponentPropsWithoutRef } from 'react';

type RouteLinkProps = Omit<ComponentPropsWithoutRef<'a'>, 'href'> & {
  to: '/' | '/atlas';
};

// Hash routes are application destinations, not document element IDs.
export function RouteLink({ to, ...props }: RouteLinkProps) {
  return <a {...props} href={`#${to}`} />;
}
