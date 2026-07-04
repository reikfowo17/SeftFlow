import { describe, expect, it } from "vitest";

import { getHelpDocsForLocale, getHelpNavGroupsForLocale } from "./HelpPage";

describe("HelpPage locale documents", () => {
  it("serves English help documents for the en-US locale", () => {
    const pages = getHelpDocsForLocale("en-US");
    const groups = getHelpNavGroupsForLocale("en-US");

    expect(pages.length).toBeGreaterThan(0);
    expect(groups.length).toBeGreaterThan(0);
    expect(pages[0].title).toBe("SeftFlow Docs Overview");
  });
});
