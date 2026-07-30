import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { MarkdownContent } from "./MarkdownContent";

describe("MarkdownContent (safe render)", () => {
  it("does not inject raw HTML script tags", () => {
    const { container } = render(<MarkdownContent text={"Hi <script>alert(1)</script> there"} />);
    expect(container.querySelector("script")).toBeNull();
  });

  it("neutralizes javascript: link hrefs", () => {
    const { container } = render(<MarkdownContent text={"[click](javascript:alert(1))"} />);
    const anchors = Array.from(container.querySelectorAll("a"));
    expect(anchors.every((a) => !(a.getAttribute("href") ?? "").toLowerCase().startsWith("javascript:"))).toBe(true);
  });

  it("neutralizes data: link hrefs", () => {
    const { container } = render(<MarkdownContent text={"[x](data:text/html;base64,PHNjcmlwdD4=)"} />);
    const anchors = Array.from(container.querySelectorAll("a"));
    expect(anchors.every((a) => !(a.getAttribute("href") ?? "").toLowerCase().startsWith("data:"))).toBe(true);
  });

  it("keeps safe https links intact", () => {
    const { container } = render(<MarkdownContent text={"[docs](https://example.test/guide)"} />);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("https://example.test/guide");
  });

  it("renders images by default for existing consumers", () => {
    const { container } = render(<MarkdownContent text={"![diagram](https://example.test/net.png)"} />);
    expect(container.querySelector("img")).not.toBeNull();
  });

  it("drops images when the caller disallows them (briefing policy)", () => {
    const { container } = render(
      <MarkdownContent text={"![diagram](https://example.test/net.png)"} disallowedElements={["img"]} />,
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("never yields a javascript: image source", () => {
    const { container } = render(<MarkdownContent text={"![x](javascript:alert(1))"} />);
    const src = container.querySelector("img")?.getAttribute("src") ?? "";
    expect(src.toLowerCase().startsWith("javascript:")).toBe(false);
  });

  it("neutralizes a scheme smuggled past a naive prefix test with an entity-encoded tab", () => {
    // `jav<TAB>ascript:` — a browser strips the tab and executes it; the URL
    // policy must reject it before it reaches the DOM.
    const { container } = render(<MarkdownContent text={"[open](<jav&#x09;ascript:alert(1)>)"} />);
    const href = container.querySelector("a")?.getAttribute("href") ?? "";
    expect(/javascript:/i.test(href.replace(/[\t\n\r]/g, ""))).toBe(false);
  });

  it("neutralizes a scheme smuggled with an entity-encoded newline", () => {
    const { container } = render(<MarkdownContent text={"[open](<jav&#x0a;ascript:alert(1)>)"} />);
    const href = container.querySelector("a")?.getAttribute("href") ?? "";
    expect(/javascript:/i.test(href.replace(/[\t\n\r]/g, ""))).toBe(false);
  });
});
