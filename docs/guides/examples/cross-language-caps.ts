import assert from "node:assert/strict";
/** JSON shape exported by this repository; keep the pattern order unchanged. */
export interface CapsDocument {
  effort_rank: Record<string, number>;
  patterns: { pattern: string; family: string }[];
  families: Record<string, {
    disable_mode: string; efforts: string[]; supports_vision: boolean; json_mode: string
  }>;
}
/** Provider family plus whether detection matched a known pattern. */
export interface Detection { family: string; matched: boolean }
interface VectorInput { model: string; reasoning_effort: string | null; strict: boolean }
interface TranslationResult extends Detection { set: Record<string, unknown>; warnings: string[] }
interface ConformanceVector { id: string; input: VectorInput; expect: TranslationResult }
function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function fnmatchCurrent(text: string, pattern: string): boolean {
  const source = pattern.split("*").map(escapeRegex).join(".*");
  return new RegExp(`^${source}$`, "s").test(text);
}
/** Normalize the provider-neutral reasoning effort and report alias warnings. */
export function normalizeReasoningEffort(value: string | null): { effort: string | null; warnings: string[] } {
  if (value === null) return { effort: null, warnings: [] };
  const effort = value.trim().toLowerCase();
  if (!effort) return { effort: null, warnings: [] };
  if (["none", "off", "false"].includes(effort)) {
    return { effort: "disabled", warnings: ["deprecated_effort_alias"] };
  }
  return { effort, warnings: [] };
}
/** Detect a provider family with the caps array's first-match-wins order. */
export function detectProvider(model: string, caps: CapsDocument): Detection {
  const modelLower = model.toLowerCase();
  for (const entry of caps.patterns) {
    if (fnmatchCurrent(modelLower, entry.pattern.toLowerCase())) {
      return { family: entry.family, matched: true };
    }
  }
  return { family: "openai", matched: false };
}
/** Translate one conformance input; this is a reference, not a JS SDK. */
export function translateVector(input: VectorInput, caps: CapsDocument): TranslationResult {
  const normalized = normalizeReasoningEffort(input.reasoning_effort);
  const detection = detectProvider(input.model, caps);
  const warnings = [...normalized.warnings];
  if (!detection.matched) warnings.push("unknown_model");
  const effort = normalized.effort;
  if (input.strict && !detection.matched && effort !== null) {
    warnings.push("strict_drop_effort");
    return { ...detection, set: {}, warnings };
  }
  if (effort === null) return { ...detection, set: {}, warnings };
  const familyCaps = caps.families[detection.family];
  if (effort === "disabled") {
    if (familyCaps.disable_mode === "native") {
      return { ...detection, set: { thinking: { type: "disabled" } }, warnings };
    }
    if (familyCaps.disable_mode === "effort_none") {
      return { ...detection, set: { reasoning_effort: "none" }, warnings };
    }
    if (familyCaps.disable_mode === "minimal_fallback") {
      return { ...detection, set: { reasoning_effort: "minimal" }, warnings };
    }
    if (familyCaps.disable_mode === "unsupported") warnings.push("disable_unsupported");
    return { ...detection, set: {}, warnings };
  }
  if (familyCaps.efforts.length === 0) {
    warnings.push("effort_dropped_unsupported");
    return { ...detection, set: {}, warnings };
  }
  if (familyCaps.efforts.includes(effort)) {
    return { ...detection, set: { reasoning_effort: effort }, warnings };
  }
  const requestedRank = caps.effort_rank[effort] ?? caps.effort_rank.high;
  const ranked = [...familyCaps.efforts].sort(
    (left, right) => caps.effort_rank[left] - caps.effort_rank[right],
  );
  const clamped = ranked.find(
    (candidate) => caps.effort_rank[candidate] >= requestedRank,
  ) ?? ranked[ranked.length - 1];
  const actualRank = caps.effort_rank[clamped];
  warnings.push(actualRank > requestedRank ? "effort_clamp_up" : "effort_clamp_down");
  return { ...detection, set: { reasoning_effort: clamped }, warnings };
}
async function readJson<T>(path: string | undefined, repositoryPath: string): Promise<T> {
  return await Bun.file(path ?? new URL(repositoryPath, import.meta.url)).json() as T;
}
async function runConformance(): Promise<void> {
  const [capsPath, conformancePath] = process.argv.slice(2);
  const caps = await readJson<CapsDocument>(capsPath, "../../../caps.json");
  const conformance = await readJson<{ reviewed: boolean; vectors: ConformanceVector[] }>(
    conformancePath, "../../../conformance.json",
  );
  if (conformance.reviewed !== true) {
    throw new Error("conformance vectors are not reviewed; do not use as a contract");
  }
  const vectors = conformance.vectors;
  for (const vector of vectors) {
    const actual = translateVector(vector.input, caps);
    assert.equal(actual.family, vector.expect.family, vector.id);
    assert.equal(actual.matched, vector.expect.matched, vector.id);
    assert.deepEqual(actual.set, vector.expect.set, `${vector.id}: exact wire fields`);
    assert.deepEqual(actual.warnings, vector.expect.warnings, `${vector.id}: warnings`);
  }
  console.log(`passed ${vectors.length} conformance vectors`);
}
if (import.meta.main) await runConformance();
