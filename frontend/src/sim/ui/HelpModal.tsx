"use client";

import { useEffect, type ReactNode } from "react";
import styles from "./sim.module.css";

/**
 * A centred overlay for reference material that is wanted occasionally and in
 * the way permanently -- the camera rig's key list, the legend, the GPU badge.
 *
 * Escape closes it, and so does a click on the backdrop. Both are wired here
 * rather than at the call site so every modal in the sim behaves the same way;
 * a dialog that only one of the two closes is the kind of thing a viewer
 * discovers by getting stuck in it.
 */
export function HelpModal({
  open, title, onClose, children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        // Stop the same press also reaching the stage's own Escape handler,
        // which would hide the whole HUD behind the modal the viewer is closing.
        e.stopPropagation();
        e.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className={styles.modalBackdrop}
      onClick={onClose}
      role="presentation"
    >
      <div
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHead}>
          <span>{title}</span>
          <button type="button" className={styles.modalClose} onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className={styles.modalBody}>{children}</div>
        <p className={styles.modalFoot}>Esc to close</p>
      </div>
    </div>
  );
}
