import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

interface FieldChrome {
  label: string;
  required?: boolean;
  error?: string;
  help?: string;
}

function useFieldIds(help?: string, error?: string) {
  const id = useId();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = [help ? helpId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  return { id, helpId, errorId, describedBy };
}

function FieldLabel({ id, label, required }: Readonly<{ id: string; label: string; required?: boolean }>) {
  return (
    <label className="ds-label" htmlFor={id}>
      {label}
      {required ? (
        <span className="ds-label__required" aria-hidden="true">
          *
        </span>
      ) : null}
    </label>
  );
}

function FieldMessages({
  help,
  helpId,
  error,
  errorId,
}: Readonly<{ help?: string; helpId: string; error?: string; errorId: string }>) {
  return (
    <>
      {help ? (
        <span className="ds-help" id={helpId}>
          {help}
        </span>
      ) : null}
      {error ? (
        <span className="ds-error" id={errorId}>
          {error}
        </span>
      ) : null}
    </>
  );
}

export function TextField({
  label,
  required,
  error,
  help,
  ...input
}: FieldChrome & InputHTMLAttributes<HTMLInputElement>) {
  const { id, helpId, errorId, describedBy } = useFieldIds(help, error);
  return (
    <div className="ds-field">
      <FieldLabel id={id} label={label} required={required} />
      <input
        className="ds-input"
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        {...input}
      />
      <FieldMessages help={help} helpId={helpId} error={error} errorId={errorId} />
    </div>
  );
}

export function TextAreaField({
  label,
  required,
  error,
  help,
  ...input
}: FieldChrome & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { id, helpId, errorId, describedBy } = useFieldIds(help, error);
  return (
    <div className="ds-field">
      <FieldLabel id={id} label={label} required={required} />
      <textarea
        className="ds-textarea"
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        {...input}
      />
      <FieldMessages help={help} helpId={helpId} error={error} errorId={errorId} />
    </div>
  );
}

export function SelectField({
  label,
  required,
  error,
  help,
  children,
  ...select
}: FieldChrome & SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  const { id, helpId, errorId, describedBy } = useFieldIds(help, error);
  return (
    <div className="ds-field">
      <FieldLabel id={id} label={label} required={required} />
      <select
        className="ds-select"
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        {...select}
      >
        {children}
      </select>
      <FieldMessages help={help} helpId={helpId} error={error} errorId={errorId} />
    </div>
  );
}

export interface CheckboxOption {
  value: string;
  label: string;
}

export function CheckboxGroup({
  legend,
  options,
  selected,
  onChange,
  error,
}: Readonly<{
  legend: string;
  options: ReadonlyArray<CheckboxOption>;
  selected: string[];
  onChange: (next: string[]) => void;
  error?: string;
}>) {
  const errorId = useId();
  function toggle(value: string, checked: boolean) {
    onChange(checked ? [...selected, value] : selected.filter((item) => item !== value));
  }
  return (
    <fieldset
      className="ds-field"
      style={{ border: 0, margin: 0, padding: 0 }}
      aria-invalid={error ? true : undefined}
      aria-describedby={error ? errorId : undefined}
    >
      <legend className="ds-label">{legend}</legend>
      {options.map((option) => (
        <label className="ds-choice" key={option.value}>
          <input
            className="ds-checkbox__control"
            type="checkbox"
            checked={selected.includes(option.value)}
            onChange={(event) => toggle(option.value, event.target.checked)}
          />{" "}
          {option.label}
        </label>
      ))}
      {error ? (
        <span className="ds-error" id={errorId}>
          {error}
        </span>
      ) : null}
    </fieldset>
  );
}
