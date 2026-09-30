import "@testing-library/jest-dom/vitest";

// jsdom 缺少 matchMedia：主题/动效相关组件需要
window.matchMedia ??= ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  addListener: () => undefined,
  removeListener: () => undefined,
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

if (typeof Element !== "undefined" && !("scrollTo" in Element.prototype)) {
  (Element.prototype as unknown as { scrollTo: () => void }).scrollTo =
    () => undefined;
}
