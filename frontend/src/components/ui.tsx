import { type ReactNode, useEffect, useRef, useState } from "react";

export function ErrorNote({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <div className="note note-error" role="alert">
      {error}
    </div>
  );
}

export function Note({ kind = "info", children }: { kind?: "info" | "warn" | "ok"; children: ReactNode }) {
  return <div className={`note note-${kind}`}>{children}</div>;
}

export function Pill({ kind, children }: { kind: "ok" | "warn" | "bad" | "muted"; children: ReactNode }) {
  return <span className={`pill pill-${kind}`}>{children}</span>;
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="page-header">
      <h1>{title}</h1>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}

export function Loading() {
  return <p className="muted">Loading…</p>;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-small"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        } catch {
          window.prompt("Copy this:", text);
        }
      }}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

/** A password shown as dots until revealed. */
export function Secret({ value }: { value: string }) {
  const [shown, setShown] = useState(false);
  return (
    <span className="secret">
      <code>{shown ? value : "•".repeat(Math.min(value.length, 13))}</code>
      <button type="button" className="btn btn-small" onClick={() => setShown(!shown)}>
        {shown ? "Hide" : "Show"}
      </button>
      <CopyButton text={value} />
    </span>
  );
}

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal?.();
  }, []);
  return (
    <dialog ref={ref} className="modal" onCancel={onClose} onClose={onClose}>
      <div className="modal-head">
        <h2>{title}</h2>
        <button type="button" className="btn btn-small btn-ghost" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <div className="modal-body">{children}</div>
    </dialog>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  // The hint sits outside the <label> so it isn't read as part of the field's name.
  return (
    <div className="field">
      <label className="field-control">
        <span className="field-label">{label}</span>
        {children}
      </label>
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}
