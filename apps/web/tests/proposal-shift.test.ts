import { describe, expect, it } from "vitest";
import { proposalShiftText } from "../src/api/adapters/result";
describe("canonical proposal departure shift label", () => {
  it.each([[120, "+2 мин"], [-120, "-2 мин"], [30, "+30 с"], [-90, "-90 с"], [61, "+61 с"], [0, "0 мин"]] as const)("shows the exact signed shift %s before execution", (seconds, label) => {
    expect(proposalShiftText(seconds)).toBe(`Сдвиг отправления: ${label}`);
  });
});
