"use client";

import { useEffect } from "react";

/** Warn before the tab is closed/reloaded while there are unsaved edits.
 * (App Router has no router-events API for in-app nav, so this covers the
 * highest-risk case — accidental refresh / close / back.) */
export function useUnsavedChanges(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}
