import {
  ArrowRight,
  BookOpen,
  Box,
  ChevronRight,
  CircleHelp,
  GalleryHorizontalEnd,
  GitBranch,
  Image,
  Layers3,
  Search,
  Settings,
  Sparkles,
  TerminalSquare,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { SelectField } from "../components/SelectField";
import { TopNav } from "../components/TopNav";
import type { Locale } from "../lib/i18n";
import { useI18n } from "../lib/preferences";

type SectionBlock =
  | {
      type: "paragraph";
      text: string;
    }
  | {
      type: "list";
      items: string[];
    }
  | {
      type: "steps";
      items: string[];
    }
  | {
      type: "table";
      headers: [string, string];
      rows: [string, string][];
    }
  | {
      type: "code";
      text: string;
    }
  | {
      type: "callout";
      title: string;
      text: string;
    };

interface DocSection {
  id: string;
  title: string;
  blocks: SectionBlock[];
}

interface DocPage {
  slug: string;
  title: string;
  description: string;
  category: string;
  icon: LucideIcon;
  sections: DocSection[];
}

interface NavGroup {
  title: string;
  pages: string[];
}

interface SearchResult {
  page: DocPage;
  matchedSectionTitle: string | null;
  preview: string;
  score: number;
}

const DOC_PAGES: DocPage[] = [
  {
    slug: "overview",
    title: "SeftFlow Docs Overview",
    description: "SeftFlow is a self-hosted, single-admin product asset workbench for organizing product data, references, copy, and image generation in one traceable workspace.",
    category: "Getting started",
    icon: BookOpen,
    sections: [
      {
        id: "what-is-productflow",
        title: "What SeftFlow is",
        blocks: [
          { type: "paragraph", text: "SeftFlow is built for solo merchants, small operations teams, and developers who want a private AI asset pipeline. It keeps product information, image assets, copy generation, image generation, run state, and gallery saves in one private workbench." },
          { type: "paragraph", text: "This version is not a public registration platform or a multi-tenant SaaS product. The deployer manages the database, Redis, storage directory, and model credentials." },
        ],
      },
      {
        id: "main-surfaces",
        title: "Main surfaces",
        blocks: [
          {
            type: "table",
            headers: ["Page", "Purpose"],
            rows: [
              ["Products", "Create products, open the product workbench, organize nodes, and run workflows."],
              ["Image chat", "Continue generating and editing around image results, compare candidates, and write results back to products."],
              ["Gallery", "Save selected generated images with source, prompt, size, model, and download metadata."],
              ["Settings", "Manage providers, models, sizes, prompt templates, upload limits, and secrets."],
              ["Help", "Read the built-in product operation docs."],
            ],
          },
        ],
      },
      {
        id: "recommended-path",
        title: "Recommended reading path",
        blocks: [
          {
            type: "steps",
            items: [
              "Start with Quick start to complete one flow from product image to generated image.",
              "Read Product workbench, Canvas and nodes, Templates, and Generate copy and images to understand the canvas workflow.",
              "Read Gallery when you need to save and review generated images.",
              "Read Image chat overview, Base and reference images, Generation settings, and Tasks and results for iterative image editing.",
              "Read Settings overview, Model providers, Image tool parameters, Prompt templates, Operations and safety, and Troubleshooting for deployment and operations.",
            ],
          },
        ],
      },
    ],
  },
  {
    slug: "quickstart",
    title: "Quick start",
    description: "Create a product, choose an initial canvas template, and generate the first product image with the shortest path.",
    category: "Getting started",
    icon: TerminalSquare,
    sections: [
      { id: "before-you-start", title: "Before you start", blocks: [{ type: "list", items: ["Backend API, worker, PostgreSQL, and Redis should be available.", "If login protection is enabled, sign in with the admin key first.", "Prepare a clear product main image. JPG, PNG, and WebP are recommended."] }] },
      {
        id: "create-product",
        title: "Create a product",
        blocks: [
          { type: "steps", items: ["Open Products from the top navigation.", "Click New product.", "Upload the product main image.", "Enter the product name.", "Choose a canvas template. For first use, choose a product hero template.", "Click Create and continue."] },
          { type: "callout", title: "Templates can be reused", text: "After the product is created, the Templates panel in the product workbench can still add scene templates. Product context from the template is automatically connected to the current canvas product node." },
        ],
      },
      { id: "first-run", title: "Generate the first image", blocks: [{ type: "steps", items: ["In the product workbench, click the product node and complete category, price, and description in Details.", "Select a copy node, enter generation requirements, and run the node.", "Review the copy output and edit it directly when needed.", "Select an image generation node and confirm that it connects to at least one downstream reference image node.", "Enter image requirements and run the node or the full workflow.", "View and download results from downstream reference image nodes or the Library panel."] }] },
    ],
  },
  {
    slug: "workbench",
    title: "Product workbench",
    description: "The product workbench is the core operating surface. Desktop keeps the canvas in the center and inspectors on the right; mobile keeps the canvas as the main surface.",
    category: "Canvas workbench",
    icon: GitBranch,
    sections: [
      { id: "layout", title: "Layout", blocks: [{ type: "table", headers: ["Area", "Description"], rows: [["Canvas", "Shows product, reference image, copy, and image generation nodes plus their edges."], ["Details", "Edits the selected node configuration and output."], ["Runs", "Shows workflow run history, failure reasons, and retryable runs."], ["Library", "Shows product assets, generated images, and images that can fill reference nodes."], ["Templates", "Inserts built-in scene templates or manages user-saved templates."], ["Mobile bottom toolbar", "Switches Browse, Edit, and Select modes, and opens workflow run, Single node, Templates, Details, Runs, and Library entrypoints."], ["Mobile bottom sheet", "Carries the desktop right-panel Single node, Templates, Details, Runs, and Library content on phones."]] }] },
      { id: "mobile-workbench", title: "Mobile workbench", blocks: [{ type: "paragraph", text: "On phones, the product detail page keeps the canvas as the main operating area. The bottom mode control provides Browse, Edit, and Select. Browse pans the canvas, selects nodes, and supports two-finger pinch zoom. Edit allows node dragging and edge creation. Select lets taps add or remove nodes from multi-select." }, { type: "paragraph", text: "The bottom toolbar also provides workflow run, Single node, Templates, Details, Runs, and Library entrypoints. Opening one of these entrypoints expands a bottom sheet; closing it returns to the canvas." }] },
      { id: "node-types", title: "Node types", blocks: [{ type: "table", headers: ["Node", "Description"], rows: [["Product", "Product context entry for name, category, price, and description."], ["Reference image", "A single image slot that can be uploaded manually or filled by an upstream image generation node."], ["Copy", "Generates and edits structured copy. It can be freeform text, copy blocks, or layout sections, and downstream image generation reads the structured copy context."], ["Image generation", "Triggers image generation. Results are written into downstream reference image nodes and are not downloaded from the generation node itself."]] }] },
      { id: "run-model", title: "Run model", blocks: [{ type: "paragraph", text: "Edge direction controls which context a downstream node can read at run time. Connecting A to B means B can reference A." }, { type: "callout", title: "Save before running", text: "Workflow runs read saved content. If Details contains an unsaved draft, the run button first attempts to save it. If saving fails, the run is not submitted." }] },
    ],
  },
  {
    slug: "canvas-nodes",
    title: "Canvas and nodes",
    description: "The canvas supports zooming, panning, node dragging, edge creation, box selection, and multi-select operations.",
    category: "Canvas workbench",
    icon: Box,
    sections: [
      { id: "canvas-controls", title: "Canvas controls", blocks: [{ type: "table", headers: ["Action", "Description"], rows: [["Desktop zoom", "Use the mouse wheel, or the bottom-right - / percentage / + controls."], ["Desktop pan", "Drag an empty canvas area with the left mouse button when no modifier is pressed. Dragging nodes, clicking controls, uploading, and drawing edges do not pan the canvas."], ["Desktop move one node", "Drag the node body or title area. The position is saved after release."], ["Desktop move multiple nodes", "Select multiple nodes, then drag any selected node to move the group."], ["Desktop create an edge", "Drag from the output handle on the right of a node to the input handle on the left of the target node."], ["Select one node", "Click the node body or the input handle. The Details panel shows that node."], ["Desktop add or remove from multi-select", "Ctrl-click a node. On macOS, Cmd-click also works. Shift-click also toggles selection."], ["Desktop box select", "Hold Shift, drag from an empty canvas area, and release to select nodes inside the box."]] }] },
      { id: "mobile-canvas-controls", title: "Mobile canvas controls", blocks: [{ type: "table", headers: ["Mode", "Description"], rows: [["Browse", "Default mode. One-finger dragging on blank canvas pans the view, tapping a node selects it, and two-finger pinch zooms the canvas."], ["Edit", "Touch and pen input can drag nodes and create edges by dragging from an output handle to a target node."], ["Select", "Tapping nodes adds or removes them from multi-select. Tapping blank canvas exits the temporary selection mode."], ["Bottom toolbar", "Provides workflow run, Single node, Templates, Details, Runs, and Library entrypoints. Panel content opens from the bottom sheet."]] }] },
      { id: "multi-select", title: "Multi-select nodes", blocks: [{ type: "paragraph", text: "After selecting multiple nodes, the canvas shows the selected count; the toolbar above the primary selected node provides Duplicate, Focus, Save template, and Delete actions." }, { type: "steps", items: ["Ctrl / Cmd / Shift-click the first node to add it to the selection.", "Keep using Ctrl / Cmd / Shift-click to add or remove nodes.", "For dense desktop canvases, hold Shift and drag a box from an empty area.", "On mobile, switch to Select mode and tap nodes to add or remove them.", "After selection, drag any selected node to move the group.", "Click Save template in the primary node toolbar to save selected nodes as a user template.", "Click Delete in the primary node toolbar to remove selected nodes and related edges; click X in the selected-count layer to clear multi-select."] }, { type: "callout", title: "Two uses of Shift", text: "Shift-click toggles a node. Shift-drag from an empty area starts desktop box selection. Dragging an empty area without Shift pans the canvas." }, { type: "list", items: ["A saved template cannot include the product node.", "Clicking an empty canvas area clears multi-select while preserving the primary selected node.", "If the current node has an unsaved draft, the page attempts to save it before switching selection."] }] },
    ],
  },
  {
    slug: "templates",
    title: "Templates",
    description: "Templates are organized by ecommerce image scenarios. They can initialize a new product canvas or be added inside the workbench.",
    category: "Canvas workbench",
    icon: Layers3,
    sections: [
      { id: "template-usage", title: "How templates are used", blocks: [{ type: "table", headers: ["Location", "Use"], rows: [["New product", "Choose one scene template to initialize the canvas."], ["Product workbench", "Add more built-in scene templates; the product context node reuses the existing product node on the canvas."], ["User templates", "Save selected nodes from the current canvas for later insertion inside the product workbench."]] }] },
      { id: "built-in-templates", title: "Built-in scene templates", blocks: [{ type: "list", items: ["Blank canvas: keeps only the product context entry for free arrangement.", "Listing hero: ecommerce hero, marketplace main image, and white-background assets for search, recommendations, first-screen detail, and platform requirements.", "Detail persuasion: SKU and variants, multiple angles, feature benefits, size specs, scale references, packing lists, usage steps, comparisons, and material/detail images.", "Scene gallery: model/lifestyle and usage-scene images for detail galleries, styling, home, space, and pairing displays.", "Content seeding: social note images and short-video covers for content feeds and live previews.", "Campaign: promotion images for campaign slots, event entry points, and ads."] }] },
      { id: "save-user-template", title: "Save a user template", blocks: [{ type: "steps", items: ["Select two or more nodes on the canvas with Ctrl / Cmd / Shift-click, or hold Shift and box-select from an empty desktop canvas area. On mobile, use Select mode and tap nodes.", "Confirm that the selected group does not include the product node.", "Click Save template in the primary node toolbar.", "Enter a template name and optional description.", "After saving, view the custom template in the right Templates panel."] }, { type: "callout", title: "Templates do not save artifacts", text: "User templates save reusable configuration and internal edges among selected nodes. They do not save product data, generated images, or copy outputs, and they currently do not appear on the new product page." }] },
    ],
  },
  {
    slug: "generate-assets",
    title: "Generate copy and images",
    description: "Copy nodes generate text assets. Image generation nodes trigger image generation and fill downstream reference image nodes.",
    category: "Canvas workbench",
    icon: Image,
    sections: [
      { id: "copy-generation", title: "Generate copy", blocks: [{ type: "steps", items: ["Select the product node and confirm product data is saved.", "Select a copy node.", "Enter requirements such as audience, tone, and key selling points.", "Run the current node.", "Review and edit the generated summary, body, copy blocks, or layout sections. Empty optional fields stay collapsed until needed."] }, { type: "callout", title: "Copy is no longer forced into four fields", text: "Copy nodes save CopyPayloadV2. The model can output freeform text, short copy blocks, visual guidance, or layout notes by scenario. Downstream image generation reads the structured copy context directly." }] },
      { id: "image-generation", title: "Generate images", blocks: [{ type: "steps", items: ["Select an image generation node.", "Confirm that it connects to at least one downstream reference image node.", "Enter image requirements, including subject, background, lighting, composition, and purpose.", "Run the current node or the workflow.", "View results in downstream reference image nodes or the Library panel."] }, { type: "callout", title: "Image generation nodes are not image slots", text: "Generated images are written into downstream reference image nodes. If no downstream reference image exists, the system asks you to connect an image/reference node first." }] },
      { id: "prompt-pattern", title: "Prompt pattern", blocks: [{ type: "code", text: "Place a white tote bag on a commuter desk beside a laptop and coffee. Use clean natural light, keep the full product visible, preserve clear texture, and make it suitable for an ecommerce hero image." }, { type: "paragraph", text: "Change only one or two factors per run, such as background, composition, lighting, or product details. Changing too much at once makes it difficult to identify which phrase affected the result." }] },
    ],
  },
  {
    slug: "image-chat",
    title: "Image chat overview",
    description: "Image chat supports independent image sessions, iterative edits, multi-candidate comparison, reference control, and saving results back to products or the gallery.",
    category: "Image chat",
    icon: Sparkles,
    sections: [
      { id: "layout", title: "Page layout", blocks: [{ type: "table", headers: ["Area", "Use"], rows: [["Desktop left session list", "Create, select, rename, or delete image chat sessions. Each session keeps its own history, references, and generation tasks."], ["Desktop center result area", "Shows the selected generated candidate, running placeholders, failed state, download button, and Send to gallery button."], ["Desktop bottom history", "Shows results by branch. Clicking a completed image selects it as the current result and base image for the next round."], ["Desktop right generation settings", "Manages the write-back target product, save-to-product actions, session references, image description, size, candidate count, and advanced image tool parameters."], ["Mobile top bar", "The left button opens the session drawer, the center shows the current session title, the pencil renames it, and the right button opens history."], ["Mobile left session drawer", "Creates, selects, and deletes sessions. Session cards show the latest thumbnail, round count, and update time."], ["Mobile right history drawer", "Shows branches and candidates in a narrow drawer. Tapping a completed image selects it as the current result and next base image; tapping a placeholder shows that candidate state."], ["Mobile bottom action bar", "Always exposes generation. After a completed result is selected, it also exposes download and send-to-gallery."], ["Mobile bottom generation sheet", "Uses Generation / Advanced tabs for the write-back target product, product/session references, image description, size, candidate count, and image tool parameters."]] }] },
      { id: "create-session", title: "Create and select sessions", blocks: [{ type: "steps", items: ["Open Image chat from the top navigation.", "On desktop, click New in the left session area. On mobile, tap the top-left menu and use the plus button in the session drawer.", "Image chat sessions are independent from products. Select a target product in Generation settings only when saving a result back to a product.", "Click or tap any session card to switch sessions. Cards show the latest thumbnail, round count, and update time; on mobile, selection closes the drawer and returns to the main view.", "To rename a session, use Rename in desktop Generation settings or tap the pencil in the mobile top bar, enter a name, and save.", "Delete a session from the session card delete button. If business deletion is disabled in Settings, the delete button is disabled and explains why."] }, { type: "callout", title: "Sessions and products are different objects", text: "Image chat sessions are independent from products. The current candidate is written back to a product only after you select a target product and click Save as reference or Set as product main image." }] },
    ],
  },
  {
    slug: "image-chat-references",
    title: "Base and reference images",
    description: "Explains the difference between base images, session references, and the per-round image context limit.",
    category: "Image chat",
    icon: Sparkles,
    sections: [
      { id: "base-image", title: "Base image vs reference image", blocks: [{ type: "paragraph", text: "Each image chat round can use both a base image and reference images. The base image comes from a selected completed history result and means continue editing from this image. Reference images come from session references and provide extra context such as style, material, pose, or background." }, { type: "steps", items: ["For the first round, write the desired image directly in Image description.", "After generation completes, click a completed image in bottom history; on mobile, select it from the right history drawer. The center result area shows Base selected.", "For more visual context, upload images in Session references; on mobile, use the Generation tab of the bottom generation sheet.", "Select references before submitting. The system sends the base image and selected references together as this round's context."] }] },
      { id: "reference-limit", title: "Image context count", blocks: [{ type: "callout", title: "Up to 6 images per round", text: "One round can select up to 6 image contexts. This count includes the history base image and explicitly selected references. If a base image is selected, at most 5 more references can be selected." }, { type: "list", items: ["Use a history result as the base when you want to preserve the product angle.", "Use session references when you need extra material, style, background, or pose context.", "When results drift too much, reducing references is often easier than adding more."] }] },
    ],
  },
  {
    slug: "image-chat-generation",
    title: "Generation settings",
    description: "Explains how image description, size, candidate count, and advanced image tool parameters affect image chat tasks.",
    category: "Image chat",
    icon: Sparkles,
    sections: [
      { id: "generation-settings", title: "Fields", blocks: [{ type: "table", headers: ["Setting", "Description"], rows: [["Write-back target product", "Select a target product before saving a generated result as product reference material or as the product main-image reference."], ["Session references", "Uploads reusable references for this session and selects them for the next round. The per-round image context limit is still 6 images."], ["Image description", "The actual user requirement submitted for this round. State subject, what to preserve, what to change, background, composition, lighting, and purpose."], ["Size", "Choose common 1K / 2K / 4K presets or enter a custom width and height. Values are calibrated against the backend maximum single-edge limit before submission."], ["Candidate count", "Controls how many candidates are created for this round. Multiple candidates appear as placeholders in history and are replaced individually when complete."], ["Generation / Advanced tabs", "Generation contains the write-back target product, session references, description, size, and candidate count. Advanced contains provider image tool parameters."], ["Image tool parameters", "Only fields enabled in Settings under available tool fields are visible, such as quality, format, background, and input fidelity. Disabled fields are not submitted."], ["Submit button", "On desktop it stays at the bottom of the right settings panel; on mobile it stays at the bottom of the bottom generation sheet. Its label reflects the current candidate count."]] }] },
      { id: "prompt-pattern", title: "Iterative editing pattern", blocks: [{ type: "code", text: "Keep the bag angle unchanged and change the background to a brighter office. Reduce desk clutter, keep only the laptop and coffee, preserve clear bag texture, and use soft shadows." }, { type: "paragraph", text: "For iterative editing, explicitly say what to keep unchanged and what to modify. A broad new description may be treated as a fresh generation rather than a controlled edit." }] },
    ],
  },
  {
    slug: "image-chat-tasks",
    title: "Tasks and results",
    description: "Explains image chat task states, retry, cancel, download, send to gallery, and save-back-to-product rules.",
    category: "Image chat",
    icon: Sparkles,
    sections: [
      { id: "run-status", title: "Run status, retry, and cancel", blocks: [{ type: "table", headers: ["Status", "Page behavior"], rows: [["Queued", "The center result area and history show placeholders and may show queue position, tasks ahead, and global active count."], ["Generating", "The placeholder shows candidate index, total candidates, latest progress, and provider status."], ["Complete", "The placeholder is replaced by the real candidate image and the page reports that a new candidate was generated."], ["Failed", "The failure reason is shown. Retry generation appears when the task is retryable."], ["Cancelled", "The page shows the task as cancelled and no new candidate result is written."]] }, { type: "list", items: ["Running tasks can be cancelled with Cancel generation.", "Failed retryable tasks can use Retry generation. Retry reuses the original prompt, size, references, and advanced parameters.", "If you changed description, size, or references, submit a new generation instead of retrying the old failed task.", "While running, the page polls lightweight status and refreshes full session detail after the task ends."] }] },
      { id: "save-results", title: "Save results", blocks: [{ type: "table", headers: ["Action", "Result"], rows: [["Download", "Downloads the currently selected candidate original image."], ["Send to gallery", "Saves the current candidate to the global gallery with source session, prompt, size, model, and download entry."], ["Save as reference", "Writes the current candidate into the target product's reference image assets for later use in the product workbench."], ["Set as product main image", "Saves the current candidate as a product main image reference asset for later product asset workflows."]] }, { type: "callout", title: "Save actions require a selected candidate", text: "Download, Send to gallery, and save-to-product actions have a clear target only when the center result area shows a completed image. Selecting a running placeholder or having no result will not submit these actions." }] },
      { id: "mobile-layout", title: "Mobile layout", blocks: [{ type: "table", headers: ["Location", "Behavior on phones"], rows: [["Top bar", "The left button opens the session drawer, the center shows the current session title, the pencil starts rename, and the history button opens the narrow history drawer."], ["Main view", "Generation status, current result, failure reason, and provider notes remain visible. Tapping the current result opens preview."], ["Right history drawer", "Shows branches, candidates, and running placeholders. Multi-candidate submissions first create matching placeholders; after completion they refresh into real candidates or failed/cancelled states."], ["Bottom action bar", "Generation is always available. After a completed image is selected, the bar adds Download and Send to gallery."], ["Bottom generation sheet", "Generation manages the write-back target product, target product assets, session references, image description, size, and candidate count; Advanced manages image tool parameters. The bottom button submits the next generation round."]] }] },
    ],
  },
  {
    slug: "gallery",
    title: "Gallery",
    description: "Gallery stores selected image chat results for centralized browsing and download.",
    category: "Gallery",
    icon: GalleryHorizontalEnd,
    sections: [
      { id: "save-to-gallery", title: "Save to gallery", blocks: [{ type: "paragraph", text: "Image chat results can be saved to the gallery. Each gallery entry keeps source session, prompt, size, model, and download access." }, { type: "list", items: ["Useful for reusable backgrounds or compositions before attaching them to a product.", "Useful for collecting candidates for others to review.", "Useful for saving good parameter-exploration results that are not the current final draft."] }] },
    ],
  },
  {
    slug: "settings",
    title: "Settings overview",
    description: "Settings manage runtime business configuration. Infrastructure configuration is still controlled by environment variables and is not overridden in the settings page.",
    category: "Settings",
    icon: Settings,
    sections: [
      { id: "settings-access", title: "Access and save rules", blocks: [{ type: "list", items: ["Settings require login. If the settings page requires secondary unlock, enter `SETTINGS_ACCESS_TOKEN` as well.", "Each setting shows its source. Database overrides are marked as database source; otherwise env/default is used.", "Only changed fields are submitted. Leaving secret fields blank does not overwrite existing values.", "Restore default removes the database override so the field falls back to env/default."] }] },
      { id: "env-only", title: "Env-only settings", blocks: [{ type: "list", items: ["Infrastructure settings such as `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`, and `ADMIN_ACCESS_KEY` cannot be overridden in Settings.", "Settings secondary unlock is protected by `SETTINGS_ACCESS_TOKEN`.", "Disabling login protection does not disable the settings secondary unlock."] }] },
    ],
  },
  {
    slug: "settings-providers",
    title: "Model providers",
    description: "Explains provider profiles, copy/image purpose bindings, models, and base image generation parameters.",
    category: "Settings",
    icon: Settings,
    sections: [
      { id: "text-settings", title: "Copy generation", blocks: [{ type: "table", headers: ["Field", "Description"], rows: [["Provider profile", "Stores provider type, connection data, API key, and capabilities. Google Gemini uses the official SDK endpoint and does not configure a Base URL. Secrets are not returned; leaving API key blank while editing preserves the old value."], ["Copy purpose binding", "Selects `mock` or a real OpenAI Responses-compatible interface, and points to a provider profile with copy capability."], ["Product understanding model", "Organizes product name, category, price, and description into a CreativeBrief."], ["Copy generation model", "Generates CopyPayloadV2 structured copy, which can contain freeform text, copy blocks, layout sections, and visual guidance."]] }] },
      { id: "image-settings", title: "Image generation", blocks: [{ type: "table", headers: ["Field", "Description"], rows: [["Provider profile", "OpenAI-compatible profiles can declare copy, Responses image, and Images API image capabilities. Google Gemini profiles declare only Gemini image capability."], ["Image purpose binding", "Selects `mock`, OpenAI Responses, OpenAI Images API, or Google Gemini Image, and points to a provider profile with the matching image capability."], ["Image model", "Default image model sent to the image provider. Responses, Images API, and Gemini support different model sets."], ["Responses background mode", "Only belongs to the OpenAI Responses image binding. When enabled, long tasks first receive a response_id and then poll status; gateways that clearly do not support it retry as synchronous requests."], ["Images API Quality / Style", "Only belongs to the OpenAI Images API image binding. Compatible gateways that reject optional fields retry with the base parameters."], ["Gemini API version / output MIME", "Only belongs to the Google Gemini image binding. API version defaults to `v1beta`; blank output MIME uses the provider default."], ["Image max single edge", "Maximum width or height in pixels for workbench image generation and image chat. Maximum area uses this value squared."], ["Main image size (compat default)", "Advanced compatibility value used only when provider input does not explicitly send image_size and kind is main image. New workflows prefer the node size picker."], ["Promo poster size (compat default)", "Advanced compatibility value used only when provider input does not explicitly send image_size and kind is promo poster."], ["Poster generation mode", "`Template render` does not consume the image model; `AI generation` calls the image provider."], ["Poster font path", "Font file used for Chinese text rendering in template posters and mock images."]] }] },
    ],
  },
  {
    slug: "settings-image-tool",
    title: "Image tool parameters",
    description: "Explains advanced Responses image_generation tool fields and their relationship with visible controls and backend persistence.",
    category: "Settings",
    icon: Settings,
    sections: [
      { id: "tool-settings", title: "Fields", blocks: [{ type: "paragraph", text: "Image tool parameters mainly cover advanced fields for the Responses `image_generation` tool. The available tool fields in Settings decide which advanced controls appear in the frontend and which fields the backend can persist; compatibility fields are filtered by provider capability." }, { type: "table", headers: ["Field", "Description"], rows: [["Available tool fields", "Multi-select field. Unselected advanced fields are hidden in the frontend and are not sent to the provider."], ["Tool model", "Model field sent inside the image_generation tool. Leave blank to omit; requires provider support."], ["Quality", "Optional default, Auto, Low, Medium, or High for providers that support quality."], ["Format", "Optional default, PNG, JPEG, or WebP. Affects provider output format."], ["Compression", "0-100; blank means not sent. Usually meaningful only for JPEG/WebP."], ["Background", "Optional default, Auto, Opaque, or Transparent. Sent only when background is enabled in available tool fields."], ["Moderation", "Optional default, Auto, or Low. Effect depends on provider support."], ["Action", "Optional default, Auto, Generate, or Edit. Hints whether the task is closer to generation or editing."], ["Input fidelity", "Optional default, Low, or High for controlling reference image fidelity when supported."], ["Partial", "0-3; blank means not sent. Used by providers that support partial images."], ["Images API n (auto)", "Internal Images API field. SeftFlow calculates it from the image chat candidate count or workflow downstream reference_image receiver count. The Responses `image_generation` tool has no n and is requested one image at a time."]] }, { type: "callout", title: "Candidate count and Images API n", text: "The candidate count in image chat is the generation count for that round. With the Images API, the backend sends the same count as request `n`; with Responses, it sends separate one-image requests. Workflow image-generation nodes generate and fill the number of downstream reference_image nodes; Images API providers use a batch request, while Responses providers request images one by one." }] },
    ],
  },
  {
    slug: "settings-prompts",
    title: "Prompt templates",
    description: "Explains which defaults global prompt templates control and which requirements should remain in one-off node or image chat inputs.",
    category: "Settings",
    icon: Settings,
    sections: [
      { id: "prompt-settings", title: "Fields", blocks: [{ type: "table", headers: ["Field", "Description"], rows: [["Product understanding system prompt", "Used for product data understanding; structured output is enforced by backend schema and provider structured outputs."], ["Copy generation system prompt", "Used for main image/poster copy generation; structured output is enforced by backend schema and provider structured outputs."], ["Poster image prompt template", "Used for workbench AI image generation. Common placeholders include `instruction`, `size`, `context_block`, `reference_policy`, and `kind`."], ["Workbench image-edit prompt template", "Used for workbench image-edit runs with reference or upstream context."], ["Workbench visual reference policy", "Fills the `reference_policy` placeholder in workbench image templates to control visual-reference priority rules."], ["Image chat prompt template", "Used for image chat. Available placeholders include `prompt`, `size`, and `history_block`."]] }, { type: "callout", title: "Keep one-off requirements out of global templates", text: "If a background, composition, or tone is needed only for this run, put it in the node requirement or image chat description. Prompt templates are better for long-term default behavior." }] },
    ],
  },
  {
    slug: "settings-operations",
    title: "Operations and safety",
    description: "Explains upload limits, generation concurrency, task recovery, provider timeout, and safety switches.",
    category: "Settings",
    icon: Settings,
    sections: [
      { id: "upload-and-queue", title: "Upload, queue, and recovery", blocks: [{ type: "table", headers: ["Field", "Description"], rows: [["Max bytes per image", "Limits the size of one uploaded image."], ["Max reference images", "Limits reference image count. Image chat also has a 6-image context limit per round."], ["Max pixels", "Limits the pixel area of uploaded images."], ["Allowed image MIME", "Comma-separated list such as `image/png,image/jpeg,image/webp`."], ["Global generation concurrency", "Shared protection threshold for workflow and image chat generation. When reached, the page asks users to retry later."], ["Image chat progress stale recovery threshold", "During worker startup recovery, running image chat tasks are checked by recent progress heartbeat."], ["Workflow image provider timeout", "Project-level timeout ceiling for one workflow AI image generation provider call. Timeout safely fails the task and releases queue capacity."]] }] },
      { id: "security-settings", title: "Security and operations", blocks: [{ type: "paragraph", text: "Secrets are not returned by API responses or shown in the page. Leaving a secret field blank keeps the existing secret; only entering a new value writes a database override." }, { type: "table", headers: ["Field", "Description"], rows: [["Require login access key", "Enabled by default. The normal workbench and private APIs require `ADMIN_ACCESS_KEY` login; when disabled, `SETTINGS_ACCESS_TOKEN` is still required for system settings."], ["Enable business deletion", "Disabled by default. Used by demo deployments to prevent deleting whole products and image chat sessions, preserving traceability. Workflow node/edge editing and reference deletion are not controlled by this switch."]] }] },
    ],
  },
  {
    slug: "troubleshooting",
    title: "Troubleshooting",
    description: "Start with the failure reason shown in the page, then decide whether to retry, cancel, adjust the prompt, change parameters, or inspect provider configuration.",
    category: "Settings",
    icon: TriangleAlert,
    sections: [
      { id: "failure-categories", title: "Failure categories", blocks: [{ type: "table", headers: ["Message", "Action"], rows: [["Quota or rate limit", "Retry later or lower concurrency."], ["Content policy", "Adjust prompt or reference images."], ["Network interruption", "Check network, proxy, and provider availability."], ["Request timeout", "Retry later; if repeated, check provider status and timeout settings."], ["Unsupported parameter", "Check size, model, and advanced parameters."]] }] },
      { id: "retry-or-new-run", title: "Retry or run again", blocks: [{ type: "paragraph", text: "Retry is suitable for temporary failures and usually reuses the task's prompt, size, references, and advanced parameters. If product data, copy, references, or image requirements changed, start a new run." }] },
      { id: "stuck-running", title: "Task stays running for a long time", blocks: [{ type: "list", items: ["Running pages poll lightweight status and refresh full detail only after the task ends.", "Cancelable runs show a cancel control.", "API and worker startup recover unfinished tasks.", "If refresh still shows no change, inspect backend, worker, Redis, and provider logs."] }] },
    ],
  },
];

const NAV_GROUPS: NavGroup[] = [
  { title: "Getting started", pages: ["overview", "quickstart"] },
  { title: "Canvas workbench", pages: ["workbench", "canvas-nodes", "templates", "generate-assets"] },
  { title: "Gallery", pages: ["gallery"] },
  { title: "Image chat", pages: ["image-chat", "image-chat-references", "image-chat-generation", "image-chat-tasks"] },
  {
    title: "Settings",
    pages: [
      "settings",
      "settings-providers",
      "settings-image-tool",
      "settings-prompts",
      "settings-operations",
      "troubleshooting",
    ],
  },
];

export function getHelpDocsForLocale(_locale: Locale): DocPage[] {
  return DOC_PAGES;
}

export function getHelpNavGroupsForLocale(_locale: Locale): NavGroup[] {
  return NAV_GROUPS;
}

function findPage(slug: string | null, pages: DocPage[]): DocPage {
  return pages.find((page) => page.slug === slug) ?? pages[0];
}

function pageIndex(page: DocPage, pages: DocPage[]): number {
  return pages.findIndex((item) => item.slug === page.slug);
}

function blockSearchText(block: SectionBlock): string {
  if (block.type === "paragraph" || block.type === "code") {
    return block.text;
  }
  if (block.type === "list" || block.type === "steps") {
    return block.items.join(" ");
  }
  if (block.type === "table") {
    return [block.headers.join(" "), ...block.rows.map((row) => row.join(" "))].join(" ");
  }
  return `${block.title} ${block.text}`;
}

function collectPageSearchText(page: DocPage): string {
  return [
    page.title,
    page.description,
    page.category,
    ...page.sections.flatMap((section) => [section.title, ...section.blocks.map(blockSearchText)]),
  ].join(" ");
}

function findMatchedSection(page: DocPage, query: string): DocSection | null {
  return (
    page.sections.find((section) => {
      const sectionText = [section.title, ...section.blocks.map(blockSearchText)].join(" ").toLowerCase();
      return sectionText.includes(query);
    }) ?? null
  );
}

function getSearchPreview(page: DocPage, query: string): string {
  const source = collectPageSearchText(page).replace(/\s+/g, " ").trim();
  const index = source.toLowerCase().indexOf(query);
  if (index === -1) {
    return page.description;
  }
  const start = Math.max(0, index - 24);
  const end = Math.min(source.length, index + query.length + 44);
  return `${start > 0 ? "..." : ""}${source.slice(start, end)}${end < source.length ? "..." : ""}`;
}

function searchDocPages(queryText: string, pages: DocPage[]): SearchResult[] {
  const query = queryText.trim().toLowerCase();
  if (!query) {
    return [];
  }
  return pages.flatMap((page) => {
    const pageText = collectPageSearchText(page).toLowerCase();
    if (!pageText.includes(query)) {
      return [];
    }
    const matchedSection = findMatchedSection(page, query);
    const titleMatch = page.title.toLowerCase().includes(query);
    const categoryMatch = page.category.toLowerCase().includes(query);
    const descriptionMatch = page.description.toLowerCase().includes(query);
    const score = (titleMatch ? 4 : 0) + (categoryMatch ? 2 : 0) + (descriptionMatch ? 1 : 0);
    return [
      {
        page,
        matchedSectionTitle: matchedSection?.title ?? null,
        preview: getSearchPreview(page, query),
        score,
      },
    ];
  })
    .sort((left, right) => right.score - left.score || pageIndex(left.page, pages) - pageIndex(right.page, pages))
    .slice(0, 8);
}

function renderBlock(block: SectionBlock) {
  if (block.type === "paragraph") {
    return <p className="text-[15px] leading-7 text-slate-700 dark:text-slate-300">{block.text}</p>;
  }
  if (block.type === "list") {
    return (
      <ul className="list-disc space-y-2 pl-5 text-[15px] leading-7 text-slate-700 dark:text-slate-300">
        {block.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    );
  }
  if (block.type === "steps") {
    return (
      <ol className="space-y-3">
        {block.items.map((item, index) => (
          <li key={item} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-3 text-[15px] leading-7 text-slate-700 dark:text-slate-300">
            <span className="mt-0.5 flex h-7 w-7 items-center justify-center rounded-full border border-slate-300 bg-white text-xs font-semibold text-slate-600 dark:border-violet-400/35 dark:bg-violet-500/15 dark:text-violet-100">
              {index + 1}
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ol>
    );
  }
  if (block.type === "table") {
    return (
      <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700/80">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="bg-slate-50 text-slate-600 dark:bg-[#151f33] dark:text-slate-300">
            <tr>
              <th className="w-[32%] border-b border-slate-200 px-4 py-3 font-semibold dark:border-slate-700/80">{block.headers[0]}</th>
              <th className="border-b border-slate-200 px-4 py-3 font-semibold dark:border-slate-700/80">{block.headers[1]}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800 dark:bg-[#0f1726]">
            {block.rows.map(([left, right]) => (
              <tr key={`${left}-${right}`}>
                <td className="px-4 py-3 font-medium text-slate-950 dark:text-slate-100">{left}</td>
                <td className="px-4 py-3 leading-6 text-slate-700 dark:text-slate-300">{right}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (block.type === "code") {
    return (
      <pre className="overflow-x-auto rounded-lg border border-slate-200 bg-slate-950 px-4 py-3 text-sm leading-6 text-slate-100 dark:border-slate-700/80 dark:bg-[#060a12]">
        <code>{block.text}</code>
      </pre>
    );
  }
  return (
    <div className="rounded-lg border border-indigo-100 bg-indigo-50/70 px-4 py-3 dark:border-violet-400/35 dark:bg-violet-500/14">
      <div className="text-sm font-semibold text-indigo-900 dark:text-violet-100">{block.title}</div>
      <p className="mt-1 text-sm leading-6 text-indigo-900/80 dark:text-slate-300">{block.text}</p>
    </div>
  );
}

export function HelpPage() {
  const { locale, t } = useI18n();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [searchQuery, setSearchQuery] = useState("");
  const docPages = getHelpDocsForLocale(locale);
  const navGroups = getHelpNavGroupsForLocale(locale);
  const page = findPage(searchParams.get("page"), docPages);
  const currentIndex = pageIndex(page, docPages);
  const previousPage = currentIndex > 0 ? docPages[currentIndex - 1] : null;
  const nextPage = currentIndex < docPages.length - 1 ? docPages[currentIndex + 1] : null;
  const PageIcon = page.icon;
  const pagesBySlug = useMemo(() => new Map(docPages.map((item) => [item.slug, item])), [docPages]);
  const searchResults = useMemo(() => searchDocPages(searchQuery, docPages), [docPages, searchQuery]);
  const normalizedSearchQuery = searchQuery.trim();

  const openPage = (slug: string) => {
    setSearchParams({ page: slug });
    setSearchQuery("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <div className="flex min-h-screen flex-col bg-white dark:bg-[#060a12] dark:text-slate-100">
      <TopNav breadcrumbs={t("help.breadcrumb")} onHome={() => navigate("/products")} />

      <main className="mx-auto grid w-full max-w-[1440px] flex-1 grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)_220px]">
        <aside className="border-b border-slate-200 bg-slate-50/70 dark:border-slate-800 dark:bg-[#0f1726] lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
          <div className="border-b border-slate-200 px-5 py-5 dark:border-slate-800">
            <button
              type="button"
              onClick={() => openPage("overview")}
              className="flex items-center gap-2 text-left text-base font-semibold text-slate-950 dark:text-white"
            >
              <BookOpen size={18} className="text-indigo-600 dark:text-violet-300" />
              {t("help.title")}
            </button>
            <div className="relative mt-4">
              <label htmlFor="help-search" className="sr-only">
                {t("help.search")}
              </label>
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500" />
              <input
                id="help-search"
                type="search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder={t("help.search")}
                className="h-9 w-full rounded-lg border border-slate-200 bg-white px-9 text-sm text-slate-900 outline-none transition-colors placeholder:text-slate-400 focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-[#0b1220] dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-violet-400 dark:focus:ring-violet-400/20"
              />
              {normalizedSearchQuery ? (
                <div className="absolute left-0 right-0 top-11 z-20 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg dark:border-slate-700/80 dark:bg-[#151f33] dark:shadow-black/30">
                  {searchResults.length > 0 ? (
                    <div className="max-h-[360px] overflow-y-auto py-1">
                      {searchResults.map((result) => (
                        <button
                          key={result.page.slug}
                          type="button"
                          onClick={() => openPage(result.page.slug)}
                          className="block w-full px-3 py-2.5 text-left transition-colors hover:bg-slate-50 dark:hover:bg-violet-500/12"
                        >
                          <div className="flex items-center gap-2">
                            <span className="rounded border border-slate-200 px-1.5 py-0.5 text-[11px] font-medium text-slate-500 dark:border-slate-700 dark:text-slate-400">
                              {result.page.category}
                            </span>
                            <span className="min-w-0 truncate text-sm font-semibold text-slate-950 dark:text-white">
                              {result.page.title}
                            </span>
                          </div>
                          {result.matchedSectionTitle ? (
                            <div className="mt-1 text-xs font-medium text-indigo-700 dark:text-violet-200">{result.matchedSectionTitle}</div>
                          ) : null}
                          <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-600 dark:text-slate-400">{result.preview}</p>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="px-3 py-3 text-sm text-slate-500 dark:text-slate-400">{t("help.noSearchResults")}</div>
                  )}
                </div>
              ) : null}
            </div>
          </div>

          <nav className="hidden space-y-6 px-3 py-5 lg:block" aria-label={t("help.nav")}>
            {navGroups.map((group) => (
              <div key={group.title}>
                <div className="px-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{group.title}</div>
                <div className="mt-2 space-y-1">
                  {group.pages.map((slug) => {
                    const item = pagesBySlug.get(slug);
                    if (!item) {
                      return null;
                    }
                    const Icon = item.icon;
                    const active = item.slug === page.slug;
                    return (
                      <button
                        key={item.slug}
                        type="button"
                        onClick={() => openPage(item.slug)}
                        aria-current={active ? "page" : undefined}
                        className={`flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors ${
                          active
                            ? "bg-white font-semibold text-indigo-700 shadow-sm ring-1 ring-slate-200 dark:bg-violet-500/18 dark:text-violet-100 dark:ring-violet-400/35"
                            : "text-slate-600 hover:bg-white hover:text-slate-950 dark:text-slate-300 dark:hover:bg-violet-500/12 dark:hover:text-white"
                        }`}
                      >
                        <Icon size={15} className={active ? "text-indigo-600 dark:text-violet-200" : "text-slate-400 dark:text-slate-500"} />
                        <span className="min-w-0 truncate">{item.title}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>

          <div className="p-4 lg:hidden">
            <label htmlFor="doc-page" className="mb-2 block text-xs font-semibold text-slate-500 dark:text-slate-400">
              {t("help.pageSelect")}
            </label>
            <SelectField
              id="doc-page"
              value={page.slug}
              groups={navGroups.map((group) => ({
                label: group.title,
                options: group.pages
                  .map((slug) => {
                    const item = pagesBySlug.get(slug);
                    return item ? { value: item.slug, label: item.title } : null;
                  })
                  .filter((item): item is { value: string; label: string } => Boolean(item)),
              }))}
              onChange={openPage}
              radius="lg"
            />
          </div>
        </aside>

        <article className="min-w-0 bg-white px-5 py-8 dark:bg-[#0b1220] sm:px-8 lg:px-12 lg:py-12">
          <header className="max-w-3xl">
            <div className="mb-4 flex items-center gap-2 text-sm font-medium text-slate-500 dark:text-slate-400">
              <span>{page.category}</span>
              <ChevronRight size={14} />
              <span>{page.title}</span>
            </div>
            <div className="mb-5 inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-indigo-600 dark:border-violet-400/35 dark:bg-violet-500/15 dark:text-violet-100">
              <PageIcon size={20} />
            </div>
            <h1 className="text-3xl font-semibold tracking-tight text-slate-950 dark:text-white sm:text-4xl">{page.title}</h1>
            <p className="mt-4 text-base leading-7 text-slate-600 dark:text-slate-300">{page.description}</p>
          </header>

          <div className="mt-10 max-w-3xl space-y-10">
            {page.sections.map((section) => (
              <section key={section.id} id={section.id} className="scroll-mt-6">
                <h2 className="text-xl font-semibold tracking-tight text-slate-950 dark:text-white">{section.title}</h2>
                <div className="mt-4 space-y-4">{section.blocks.map((block, index) => <div key={index}>{renderBlock(block)}</div>)}</div>
              </section>
            ))}
          </div>

          <footer className="mt-12 grid max-w-3xl gap-3 border-t border-slate-200 pt-6 dark:border-slate-800 sm:grid-cols-2">
            {previousPage ? (
              <button
                type="button"
                onClick={() => openPage(previousPage.slug)}
                className="rounded-lg border border-slate-200 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:border-slate-700/80 dark:bg-[#0f1726] dark:hover:bg-violet-500/12"
              >
                <div className="text-xs font-medium text-slate-500 dark:text-slate-400">{t("help.previous")}</div>
                <div className="mt-1 text-sm font-semibold text-slate-950 dark:text-white">{previousPage.title}</div>
              </button>
            ) : (
              <div />
            )}
            {nextPage ? (
              <button
                type="button"
                onClick={() => openPage(nextPage.slug)}
                className="rounded-lg border border-slate-200 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:border-slate-700/80 dark:bg-[#0f1726] dark:hover:bg-violet-500/12 sm:text-right"
              >
                <div className="text-xs font-medium text-slate-500 dark:text-slate-400">{t("help.next")}</div>
                <div className="mt-1 inline-flex items-center text-sm font-semibold text-indigo-700 dark:text-violet-200">
                  {nextPage.title}
                  <ArrowRight size={14} className="ml-1" />
                </div>
              </button>
            ) : null}
          </footer>
        </article>

        <aside className="hidden border-l border-slate-200 bg-slate-50/70 px-5 py-12 dark:border-slate-800 dark:bg-[#0f1726] lg:block">
          <div className="sticky top-8">
            <div className="text-sm font-semibold text-slate-950 dark:text-white">{t("help.onThisPage")}</div>
            <nav className="mt-3 space-y-2" aria-label={t("help.onThisPage")}>
              {page.sections.map((section) => (
                <a
                  key={section.id}
                  href={`#${section.id}`}
                  className="block border-l border-slate-200 pl-3 text-sm leading-5 text-slate-500 transition-colors hover:border-indigo-400 hover:text-slate-950 dark:border-slate-700 dark:text-slate-400 dark:hover:border-violet-400 dark:hover:text-white"
                >
                  {section.title}
                </a>
              ))}
            </nav>
            <div className="mt-8 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700/80 dark:bg-[#151f33]">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-950 dark:text-white">
                <CircleHelp size={15} className="text-indigo-600 dark:text-violet-300" />
                {t("help.needAction")}
              </div>
              <div className="mt-3 grid gap-2">
                <button
                  type="button"
                  onClick={() => navigate("/products")}
                  className="rounded-md bg-slate-950 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-violet-500 dark:hover:bg-violet-400"
                >
                  {t("help.openProducts")}
                </button>
                <button
                  type="button"
                  onClick={() => navigate("/image-chat")}
                  className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:text-slate-950 dark:border-slate-700 dark:bg-[#0b1220] dark:text-slate-300 dark:hover:bg-violet-500/12 dark:hover:text-white"
                >
                  {t("help.openImageChat")}
                </button>
              </div>
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}