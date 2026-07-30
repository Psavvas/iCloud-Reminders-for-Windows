import { useEffect, useRef } from "react";

/**
 * Modal wrapper over <dialog>, which brings Esc-to-close and focus trapping
 * from the platform rather than reimplementing them.
 */
export default function Sheet({ children, onClose, className = "" }) {
  const ref = useRef(null);

  useEffect(() => {
    const dlg = ref.current;
    if (dlg && !dlg.open) dlg.showModal();
    const onCancel = (e) => {
      e.preventDefault();
      onClose();
    };
    dlg?.addEventListener("cancel", onCancel);
    return () => dlg?.removeEventListener("cancel", onCancel);
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      className={`sheet ${className}`}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="sheet-body">{children}</div>
    </dialog>
  );
}
