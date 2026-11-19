"use client";

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

// A modal on the native <dialog>: focus trap, Esc and the inert page
// behind it come from the browser instead of hand-rolled code. Clicking
// the dimmed backdrop closes it too.
export function Dialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-label={title}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      {open && (
        <div className="dialog-body">
          <div className="section-head">
            <h2>{title}</h2>
            <button type="button" className="ghost" onClick={onClose} aria-label="Закрыть">
              <X size={18} />
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}
