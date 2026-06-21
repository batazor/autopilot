import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Node 25+ ships a native `localStorage` that requires a CLI file path and
// shadows whatever happy-dom installs on `window`. Override it with a plain
// in-memory store so component code can read/write freely.
function installMemoryStorage(name: "localStorage" | "sessionStorage") {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key) {
      return store.has(key) ? (store.get(key) as string) : null;
    },
    setItem(key, value) {
      store.set(String(key), String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
    key(index) {
      return Array.from(store.keys())[index] ?? null;
    },
  };
  Object.defineProperty(globalThis, name, { value: storage, configurable: true });
  Object.defineProperty(window, name, { value: storage, configurable: true });
}

installMemoryStorage("localStorage");
installMemoryStorage("sessionStorage");

// Headless UI calls Element.prototype.getAnimations during transitions; happy-dom
// does not implement it. Polyfill with a no-op so the warning chatter goes away.
if (typeof Element !== "undefined" && !Element.prototype.getAnimations) {
  Element.prototype.getAnimations = function () {
    return [];
  };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
