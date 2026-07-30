import { useEffect, useState } from "react";

/** Transient toast, plus the persistent conflict prompt. */
export default function Banner({ banner, conflicts, onResolve, onDismiss }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!banner) return setVisible(false);
    setVisible(true);
    if (!banner.timeout) return;
    const id = setTimeout(() => {
      setVisible(false);
      onDismiss();
    }, banner.timeout);
    return () => clearTimeout(id);
  }, [banner, onDismiss]);

  const conflict = conflicts && conflicts[0];

  if (conflict) {
    return (
      <div className="banner warn show">
        <span>
          “{conflict.local.title || "A reminder"}” changed on another device.
          Keeping the iCloud version; your edit is saved.
        </span>
        <button className="ghost" onClick={() => onResolve(conflict.id, "local")}>
          Use mine
        </button>
        <button className="ghost" onClick={() => onResolve(conflict.id, "remote")}>
          Keep iCloud's
        </button>
      </div>
    );
  }

  if (!banner) return null;
  return (
    <div className={`banner ${banner.kind} ${visible ? "show" : ""}`}>
      <span>{banner.message}</span>
    </div>
  );
}
