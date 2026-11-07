import type { ReactNode } from 'react';
export function StatusPanel({
  title,
  children,
  error = false,
}: {
  title: string;
  children?: ReactNode;
  error?: boolean;
}) {
  return (
    <section
      className={`alert ${error ? 'alert-danger' : 'alert-light border'}`}
      role={error ? 'alert' : 'status'}
    >
      <h2 className="h6">{title}</h2>
      {children}
    </section>
  );
}
