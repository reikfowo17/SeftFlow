import { describe, expect, it } from "vitest";

import type { CanvasTemplateSummary } from "./types";
import {
  localizeBuiltInTemplateLabel,
  localizeBuiltInTemplateNodeTitle,
  localizeCanvasTemplateSummary,
} from "./canvasTemplateLocalization";

const builtInTemplate: CanvasTemplateSummary = {
  key: "ecommerce-main-image-v1",
  version: 1,
  kind: "full_canvas",
  title: "电商主图",
  description: "生成商品首图，突出主体、利益点和清晰构图。",
  source: "builtin",
  user_template_id: null,
  scenario: {
    scenario: "main_image",
    title: "主图",
    description: "用于商品列表和详情首屏的主视觉。",
    ecommerce_stage: "listing",
    tags: ["main-image"],
  },
  preview_nodes: [
    {
      key: "copy",
      node_type: "copy_generation",
      title: "主图卖点",
      position_x: 320,
      position_y: 88,
      size: null,
    },
    {
      key: "output",
      node_type: "reference_image",
      title: "主图输出",
      position_x: 960,
      position_y: 72,
      size: null,
    },
  ],
  preview_edges: [{ source_node_key: "copy", target_node_key: "output" }],
  output_slots: [{ node_key: "output", label: "主图输出", description: "商品列表和详情首图候选。" }],
  reference_input_hints: [],
  suggested_connections: [],
  default_external_connections: [
    { source: "existing_product_context", target_node_key: "copy", label: "自动接商品" },
  ],
};

describe("canvas template localization", () => {
  it("localizes built-in template catalog content in English", () => {
    const localized = localizeCanvasTemplateSummary(builtInTemplate, "en-US");

    expect(localized.title).toBe("E-commerce main image");
    expect(localized.description).toContain("product hero image");
    expect(localized.scenario.title).toBe("Main image");
    expect(localized.preview_nodes.map((node) => node.title)).toEqual(["Main-image benefits", "Main image output"]);
    expect(localized.output_slots[0]?.label).toBe("Main image output");
    expect(localized.default_external_connections[0]?.label).toBe("Auto-connect product");
  });

  it("keeps user templates unchanged even on the supported locale", () => {
    const userTemplate = {
      ...builtInTemplate,
      key: "user-template-1",
      source: "user",
      user_template_id: "template-1",
      title: "我的活动模板",
    } satisfies CanvasTemplateSummary;

    expect(localizeCanvasTemplateSummary(userTemplate, "en-US")).toBe(userTemplate);
  });

  it("localizes known built-in persisted node titles and labels without changing custom text", () => {
    expect(localizeBuiltInTemplateNodeTitle("copy_generation", "主图卖点", "en-US")).toBe("Main-image benefits");
    expect(localizeBuiltInTemplateNodeTitle("reference_image", "主图输出", "en-US")).toBe("Main image output");
    expect(
      localizeBuiltInTemplateNodeTitle("copy_generation", "自定义标题", "en-US", {
        _canvas_template: {
          source: "builtin",
          template_key: "ecommerce-main-image-v1",
          node_key: "copy",
        },
      }),
    ).toBeNull();
    expect(
      localizeBuiltInTemplateNodeTitle("copy_generation", "主图卖点", "en-US", {
        _canvas_template: {
          source: "builtin",
          template_key: "ecommerce-main-image-v1",
          node_key: "copy",
        },
      }),
    ).toBe("Main-image benefits");
    expect(localizeBuiltInTemplateLabel("主图输出", "en-US")).toBe("Main image output");

    expect(localizeBuiltInTemplateNodeTitle("copy_generation", "我的文案节点", "en-US")).toBeNull();
    expect(localizeBuiltInTemplateLabel("我的输出", "en-US")).toBeNull();
  });
});
