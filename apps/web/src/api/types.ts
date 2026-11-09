import type { Schemas } from "./generated/schema";
export type { Schemas };
export type AnalysisRequest =
  | Schemas["UrbanPassengerRequest"]
  | Schemas["IntercityPassengerRequest"]
  | Schemas["UrbanCargoRequest"]
  | Schemas["IntercityCargoRequest"];
export type AnalysisKind = AnalysisRequest["analysis_kind"];
export type Category = Schemas["UrbanPassengerRequest"]["categories"][number];
export type Territory = Schemas["TerritoryOption"];
export type AnalysisResult = Schemas["AnalysisResult"];
export type Selection = Schemas["ExplanationSelection"];
