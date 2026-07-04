import { describe, expect, it } from "vitest";

import type { TranslationKey, TranslationParams } from "../../lib/i18n";
import type { WorkflowNode } from "../../lib/types";
import {
  connectionDescription,
  defaultTitleForNodeType,
  referenceSlotLabel,
  workflowNodeDisplayLabel,
  workflowNodeDisplayTitle,
} from "./nodeDisplay";

const baseNode: WorkflowNode = {
  id: "node-1",
  workflow_id: "workflow-1",
  node_type: "reference_image",
  title: "Reference 1",
  position_x: 0,
  position_y: 0,
  config_json: {},
  status: "idle",
  output_json: null,
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

function stubT(values: Partial<Record<TranslationKey, string>>) {
  return (key: TranslationKey, params?: TranslationParams): string => {
    const value = values[key] ?? key;
    return value.replace(/\{(\w+)\}/g, (_match, paramKey: string) => String(params?.[paramKey] ?? `{${paramKey}}`));
  };
}

describe("node display helpers", () => {
  it("uses business-facing labels for internal node types", () => {
    expect(workflowNodeDisplayLabel({ ...baseNode, node_type: "product_context" })).toBe("Product context");
    expect(workflowNodeDisplayLabel(baseNode)).toBe("Image carrier node");
    expect(workflowNodeDisplayLabel({ ...baseNode, node_type: "copy_generation" })).toBe("Copy generation node");
    expect(workflowNodeDisplayLabel({ ...baseNode, node_type: "image_generation" })).toBe("Image trigger node");
  });

  it("derives reference slot labels from explicit labels and merchant roles", () => {
    expect(referenceSlotLabel({ ...baseNode, config_json: { label: "Campaign image" } })).toBe("Campaign image");
    expect(referenceSlotLabel({ ...baseNode, config_json: { role: "model_image" } })).toBe("Model image");
    expect(referenceSlotLabel({ ...baseNode, config_json: { role: "scene_image" } })).toBe("Scene image");
    expect(referenceSlotLabel(baseNode)).toBe("Image carrier node");
  });

  it("hides legacy default titles but preserves user-authored titles", () => {
    expect(workflowNodeDisplayTitle({ ...baseNode, title: "Reference 2" })).toBe("Image carrier node");
    expect(workflowNodeDisplayTitle({ ...baseNode, title: "Image node 2" })).toBe("Image carrier node");
    expect(workflowNodeDisplayTitle({ ...baseNode, title: "Detail A" })).toBe("Detail A");
    expect(defaultTitleForNodeType("reference_image", 2)).toBe("Image carrier node 2");
    expect(defaultTitleForNodeType("image_generation", 2)).toBe("Image trigger node 2");
  });

  it("explains confusing connection semantics", () => {
    const copyNode: WorkflowNode = {
      ...baseNode,
      id: "copy-node",
      node_type: "copy_generation",
      title: "Copy 1",
    };
    const imageNode: WorkflowNode = {
      ...baseNode,
      id: "image-node",
      node_type: "image_generation",
      title: "Image trigger node 1",
    };

    expect(connectionDescription(baseNode, { ...baseNode, title: "Reference 2" })).toContain(
      "Image carrier nodes cannot be connected to each other",
    );
    expect(connectionDescription(baseNode, copyNode)).toBe(
      "Image carrier node is used as reference for Copy generation node.",
    );
    expect(
      connectionDescription(
        { ...baseNode, title: "Image node 1" },
        { ...copyNode, title: "Product copy 1" },
        stubT({
          "detail.connection.referenceToCopy": "{source} references {target}.",
        }),
      ),
    ).toBe("Image carrier node references Copy generation node.");
  });
});
