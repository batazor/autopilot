"use client";

import {
  Description,
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import type { ReactNode } from "react";

export type AppConfirmDialogProps = {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  children: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Destructive actions use danger styling on the primary button */
  variant?: "default" | "danger";
  busy?: boolean;
};

export function AppConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  variant = "default",
  busy = false,
}: AppConfirmDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} className="headless-dialog-root">
      <DialogBackdrop transition className="headless-dialog__backdrop" />
      <div className="headless-dialog__container">
        <DialogPanel transition className="headless-dialog__panel">
          <DialogTitle className="headless-dialog__title">{title}</DialogTitle>
          <Description as="div" className="headless-dialog__body">
            {children}
          </Description>
          <div className="headless-dialog__actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={onClose}
            >
              {cancelLabel}
            </button>
            <button
              type="button"
              className={
                variant === "danger" ? "btn-primary headless-dialog__confirm--danger" : "btn-primary"
              }
              disabled={busy}
              onClick={onConfirm}
            >
              {confirmLabel}
            </button>
          </div>
        </DialogPanel>
      </div>
    </Dialog>
  );
}
