export function londonTime(value: string | null | undefined, options: Intl.DateTimeFormatOptions = {}) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    hour: "2-digit",
    minute: "2-digit",
    ...options,
  }).format(new Date(value));
}
