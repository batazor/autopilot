import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import type { Dispatch, SetStateAction } from "react";
import { Button } from "@/components/ui";
import type { FarmAccount } from "@/lib/farm/types";

type DeleteAccountDialogProps = {
  target: FarmAccount | null;
  setTarget: Dispatch<SetStateAction<FarmAccount | null>>;
  confirm: string;
  setConfirm: Dispatch<SetStateAction<string>>;
  busy: boolean;
  onConfirm: () => void;
};

/** Type-to-confirm modal for deleting a farm account. */
export function DeleteAccountDialog({
  target,
  setTarget,
  confirm,
  setConfirm,
  busy,
  onConfirm,
}: DeleteAccountDialogProps) {
  return (
    <Dialog
      open={target !== null}
      onClose={() => {
        if (!busy) setTarget(null);
      }}
      className="headless-dialog-root"
    >
      <DialogBackdrop transition className="headless-dialog__backdrop" />
      <div className="headless-dialog__container">
        <DialogPanel transition className="headless-dialog__panel">
          <DialogTitle className="headless-dialog__title">
            Delete {target?.username}?
          </DialogTitle>
          <div className="headless-dialog__body">
            <p className="m-0">Type the nickname exactly to delete this farm account.</p>
            <input
              autoFocus
              type="text"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder={target?.username}
              className="field mt-3 w-full"
            />
          </div>
          <div className="headless-dialog__actions">
            <Button disabled={busy} onClick={() => setTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              className="headless-dialog__confirm--danger"
              disabled={busy || !target || confirm !== target.username}
              onClick={onConfirm}
            >
              Delete account
            </Button>
          </div>
        </DialogPanel>
      </div>
    </Dialog>
  );
}
