import type { ComponentPropsWithoutRef } from "react";
type Destination = "top" | "about" | "features" | "how" | "atlas";
type Props = Omit<ComponentPropsWithoutRef<"a">, "href"> & { to: Destination };
// These fragments represent application destinations, including separately mounted views.
export function NavigationLink({ to, ...props }: Props) {
  return <a {...props} href={"#" + to} />;
}
