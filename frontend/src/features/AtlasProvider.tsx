import { useReducer, type ReactNode } from "react";
import { Context, initialState, reducer } from "./state";
export function AtlasProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  return <Context value={{ state, dispatch }}>{children}</Context>;
}
