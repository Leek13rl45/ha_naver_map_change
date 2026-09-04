// Naver Map Change: swap the basemap style URL core fetches for our own.
// Nothing else. Every style decision is made server side, so this file has no
// logic to get wrong and no secret to leak.
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
            return original.call(this, replacement, init);
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
