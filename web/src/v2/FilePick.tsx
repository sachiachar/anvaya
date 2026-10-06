// A button that opens the file picker, in v2's style. The picked file goes to onFile, once.
export default function FilePick({ label, accept, onFile, disabled, quiet }: {
  label: string; accept: string; onFile: (f: File) => void; disabled?: boolean; quiet?: boolean;
}) {
  return (
    <label className={`v2-btn${quiet ? " quiet" : ""}${disabled ? " is-disabled" : ""}`}>
      {label}
      <input type="file" accept={accept} hidden disabled={disabled}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
    </label>
  );
}
