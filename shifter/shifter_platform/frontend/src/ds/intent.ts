/** Generic UI intents from the design system (#1299). Domain values map onto
 * these; the tokens never carry domain meaning directly. */
export type Intent = "neutral" | "info" | "success" | "warning" | "danger";

/** Status-pill intents (the dot+label indicator has no neutral variant). */
export type StatusIntent = Exclude<Intent, "neutral">;
