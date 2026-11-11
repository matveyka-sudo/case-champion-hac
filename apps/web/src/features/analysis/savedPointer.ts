export type SavedPointer = { kind: "analysis" | "job"; id: string };
const validIdentifier = (id: string) => id.length > 0 && id.length <= 256 && id.trim() === id &&
  [...id].every((character) => character.codePointAt(0)! >= 32 && character.codePointAt(0) !== 127);
export function parseSavedPointer(hash: string): SavedPointer | null {
  if (hash === "#atlas" || hash === "") return null;
  const invalid = () => new Error("Ссылка на сохранённый расчёт некорректна или не поддерживается.");
  if (!hash.startsWith("#atlas?") || hash.length > 4096) throw invalid();
  const query = hash.slice(7), separator = query.indexOf("=");
  if (separator < 0 || query.includes("&")) throw invalid();
  let kind: string, id: string;
  try {
    kind = decodeURIComponent(query.slice(0, separator).replaceAll("+", " "));
    id = decodeURIComponent(query.slice(separator + 1).replaceAll("+", " "));
    encodeURIComponent(id); // Reject invalid Unicode before it reaches a request path.
  } catch { throw invalid(); }
  if ((kind !== "analysis" && kind !== "job") || !validIdentifier(id)) throw invalid();
  return { kind, id };
}
export function savedPointerHash(pointer: SavedPointer) {
  const hash = `#atlas?${pointer.kind}=${encodeURIComponent(pointer.id)}`;
  parseSavedPointer(hash);
  return hash;
}
export function subscribeAtlasLocation(listener: () => void) {
  window.addEventListener("hashchange", listener);
  window.addEventListener("popstate", listener);
  window.addEventListener("waydex:navigate", listener);
  return () => {
    window.removeEventListener("hashchange", listener);
    window.removeEventListener("popstate", listener);
    window.removeEventListener("waydex:navigate", listener);
  };
}
export const atlasLocation = () => window.location.hash;
export function writeSavedPointer(pointer: SavedPointer, replace: boolean) {
  const hash = savedPointerHash(pointer);
  if (hash === window.location.hash) return;
  window.history[replace ? "replaceState" : "pushState"](null, "", hash);
  window.dispatchEvent(new Event("waydex:navigate"));
}
