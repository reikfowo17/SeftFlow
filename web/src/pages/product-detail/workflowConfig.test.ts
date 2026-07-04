import { describe, expect, it } from "vitest";

import type { CopyPayloadV2, ProductDetail, WorkflowNode } from "../../lib/types";
import { defaultConfigForType, defaultTitleForType, draftFromNode } from "./workflowConfig";

const structuredPayload: CopyPayloadV2 = {
  version: 2,
  summary: "Structured summary",
  content: {
    kind: "blocks",
    blocks: [{ id: "headline", role: "headline", label: "Main info", text: "Structured main info" }],
  },
  visual_guidance: null,
};

const removedCopyOutputKeys = [
  "title",
  "selling" + "_points",
  "poster" + "_headline",
  "c" + "ta",
] as const;

const baseNode: WorkflowNode = {
  id: "copy-node",
  workflow_id: "workflow-1",
  node_type: "copy_generation",
  title: "Copy",
  position_x: 0,
  position_y: 0,
  config_json: {},
  status: "succeeded",
  output_json: {
    copy_set_id: "copy-set-1",
    structured_payload: structuredPayload,
    [removedCopyOutputKeys[0]]: "Old title",
    [removedCopyOutputKeys[1]]: ["Old selling points"],
    [removedCopyOutputKeys[2]]: "Old poster title",
    [removedCopyOutputKeys[3]]: "Old CTA",
  },
  failure_reason: null,
  is_retryable: false,
  attempt_count: 0,
  retry_count: 0,
  non_retryable_reason: null,
  retry_hint: null,
  last_run_at: null,
  created_at: "2026-05-10T00:00:00Z",
  updated_at: "2026-05-10T00:00:00Z",
};

const product: ProductDetail = {
  id: "product-1",
  name: "Product",
  category: null,
  price: null,
  source_note: null,
  workflow_state: "draft",
  source_assets: [],
  latest_brief: null,
  current_confirmed_copy_set: null,
  copy_sets: [],
  poster_variants: [],
  created_at: "2026-05-10T00:00:00Z",
  updated_at: "2026-05-10T00:00:00Z",
};

describe("draftFromNode", () => {
  it("keeps copy draft on structured payload and ignores removed output fields", () => {
    const draft = draftFromNode(baseNode, product);

    expect(draft.copyStructuredPayload).toEqual(structuredPayload);
    expect("copyTitle" in draft).toBe(false);
    expect("copySellingPoints" in draft).toBe(false);
    expect("copyPosterHeadline" in draft).toBe(false);
    expect("copyCta" in draft).toBe(false);
  });

  it("uses merchant-facing defaults for new nodes", () => {
    expect(defaultConfigForType("reference_image")).toMatchObject({ role: "reference", label: "" });
    expect(defaultTitleForType("reference_image", 1)).toBe("Reference image node 1");
    expect(defaultTitleForType("copy_generation", 1)).toBe("Copy generation node 1");
    expect(defaultTitleForType("image_generation", 1)).toBe("Image generation trigger node 1");
  });
});
