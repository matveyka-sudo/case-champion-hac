import Ajv from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import raw from "./generated/validation.json" with { type: "json" };
import type { Schemas } from "./types";
const ajv = new Ajv({ strict: false, allErrors: false });
addFormats(ajv);
ajv.addSchema(raw);
export function decode<K extends keyof Schemas>(
  name: K,
  value: unknown,
): Schemas[K] {
  const validate = ajv.getSchema(`waydex-v2#/components/schemas/${name}`);
  if (!validate || !validate(value)) throw new Error("API_CONTRACT_INVALID");
  return value as Schemas[K];
}
