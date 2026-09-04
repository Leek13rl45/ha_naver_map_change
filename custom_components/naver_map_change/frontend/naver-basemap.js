// Naver Map Change: swap the basemap style URL core fetches for our own.
// Nothing else. Every style decision is made server side, so this file has no
// logic to get wrong and no secret to leak.
//
// The one exception to "every style decision is made server side": the device
// pixel ratio. It is the only fact about the client that the server cannot
// determine from the request, so it is forwarded as ?dpr=<number>. Even here
// this file makes no decision - it passes the raw number along, and whether
// that means @2x tiles is decided by the style endpoint (view.py). Fractional
// ratios (1.5, 2.625) are sent verbatim for the same reason.
//
// Target URLs are core's VECTOR_STYLES (frontend src/common/map/base-layer.ts).
// Failure must always fall through to core's own behaviour: a broken basemap is
// acceptable, a blank map is not.
(() => {
  "use strict";
  try {
    // The module can be loaded twice (extra_module_url plus a cached copy);
    // wrapping fetch twice would nest the interception.
    if (window.__naverMapChangePatched) {
      return;
    }
    window.__naverMapChangePatched = true;

    const MAP = {
      "/static/map/light.json":
        "/api/map_tiles/naver_map_change/style/light.json",
      "/static/map/dark.json": "/api/map_tiles/naver_map_change/style/dark.json",
    };

    // Number.isFinite is false for undefined, null, NaN and Infinity alike, so
    // an unusable value simply means no parameter is appended at all.
    const ratio = window.devicePixelRatio;
    const DPR =
      Number.isFinite(ratio) && ratio > 0 ? "?dpr=" + String(ratio) : "";

    const original = window.fetch;
    if (typeof original !== "function") {
      return;
    }

    window.fetch = function (input, init) {
      try {
        const raw = typeof input === "string" ? input : input && input.url;
        if (raw) {
          const path = new URL(raw, location.origin).pathname;
          const replacement = MAP[path];
          if (replacement) {
            return original.call(this, replacement + DPR, init);
          }
        }
      } catch (_err) {
        // Whatever went wrong, let the original request through untouched.
      }
      return original.apply(this, arguments);
    };
  } catch (_err) {
    // Never break the frontend over a basemap preference.
  }
})();
