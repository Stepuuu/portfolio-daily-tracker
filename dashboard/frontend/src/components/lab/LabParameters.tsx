import { useState } from "react";
import {
  strictJson,
  type JsonValue,
  type LabParams,
  type ParameterSchema,
} from "@/services/lab";
import { Field, inputClass } from "./LabUi";

function JsonParameter({
  name,
  schema,
  value,
  onChange,
}: {
  name: string;
  schema: ParameterSchema;
  value: JsonValue | undefined;
  onChange: (value: JsonValue | undefined) => void;
}) {
  const [raw, setRaw] = useState(
    value === undefined ? "" : JSON.stringify(value, null, 2),
  );
  const [error, setError] = useState("");
  return (
    <Field
      label={schema.title || name}
      help={schema.description || "使用 JSON 编辑这个参数。"}
    >
      <textarea
        className={`${inputClass} font-mono`}
        rows={4}
        required={schema.required !== false}
        value={raw}
        onChange={(e) => {
          setRaw(e.target.value);
          try {
            if (!e.target.value.trim() && schema.required === false) {
              onChange(undefined);
              e.target.setCustomValidity("");
              setError("");
              return;
            }
            const parsed = JSON.parse(e.target.value);
            strictJson(parsed);
            onChange(parsed);
            e.target.setCustomValidity("");
            setError("");
          } catch {
            const message = "请输入有效 JSON，数字必须为有限数。";
            e.target.setCustomValidity(message);
            setError(message);
          }
        }}
        aria-invalid={!!error}
        title={error || undefined}
      />
    </Field>
  );
}

export default function LabParameters({
  schema,
  values,
  onChange,
}: {
  schema: Record<string, ParameterSchema>;
  values: LabParams;
  onChange: (name: string, value: JsonValue | undefined) => void;
}) {
  return (
    <div className="space-y-4">
      {Object.entries(schema).map(([name, definition]) => {
        const value = values[name],
          label = definition.title || name;
        if (definition.enum)
          return (
            <Field key={name} label={label} help={definition.description}>
              <select
                className={inputClass}
                required={definition.required !== false}
                value={value === undefined ? "" : JSON.stringify(value)}
                onChange={(e) =>
                  onChange(
                    name,
                    e.target.value === ""
                      ? undefined
                      : JSON.parse(e.target.value),
                  )
                }
              >
                <option value="">选择参数值</option>
                {definition.enum.map((option, i) => (
                  <option key={i} value={JSON.stringify(option)}>
                    {String(option)}
                  </option>
                ))}
              </select>
            </Field>
          );
        if (definition.type === "boolean")
          return (
            <Field key={name} label={label} help={definition.description}>
              <select
                className={inputClass}
                value={value === undefined ? "" : String(value)}
                required={definition.required !== false}
                onChange={(e) =>
                  onChange(
                    name,
                    e.target.value === ""
                      ? undefined
                      : e.target.value === "true",
                  )
                }
              >
                <option value="">选择参数值</option>
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </Field>
          );
        if (definition.type === "integer" || definition.type === "number")
          return (
            <Field key={name} label={label} help={definition.description}>
              <input
                className={inputClass}
                type="number"
                required={definition.required !== false}
                min={definition.minimum}
                max={definition.maximum}
                step={definition.type === "integer" ? 1 : "any"}
                value={typeof value === "number" ? value : ""}
                onChange={(e) =>
                  onChange(
                    name,
                    e.target.value === "" ? undefined : Number(e.target.value),
                  )
                }
              />
            </Field>
          );
        if (definition.type === "string")
          return (
            <Field key={name} label={label} help={definition.description}>
              <input
                className={inputClass}
                required={definition.required !== false}
                minLength={definition.minLength}
                maxLength={definition.maxLength}
                value={typeof value === "string" ? value : ""}
                onChange={(e) => onChange(name, e.target.value)}
              />
            </Field>
          );
        return (
          <JsonParameter
            key={name}
            name={name}
            schema={definition}
            value={value}
            onChange={(next) => onChange(name, next)}
          />
        );
      })}
    </div>
  );
}
