import "@testing-library/jest-dom";

globalThis.requestAnimationFrame = (cb: FrameRequestCallback) => {
  cb(performance.now() + 10000);
  return 0;
};

// jsdom doesn't implement matchMedia (used by StatsRow's reduced-motion check).
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
