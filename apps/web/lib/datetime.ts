// <input type="datetime-local"> reads and writes LOCAL time without a
// zone, so every value going in or out has to be local too. Building it
// from toISOString() (UTC) shows a post's time shifted by the viewer's
// offset and, worse, saves it shifted back the other way -- the
// calendar's "reschedule" did exactly that east of Greenwich.

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

export function toLocalInputValue(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Called fresh on every render, never as a module constant, so "now"
// does not freeze at page load.
export function minLocalInput(): string {
  return toLocalInputValue(new Date());
}

export function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function addDays(date: Date, days: number): Date {
  // Date arithmetic via the constructor keeps local midnight across DST
  // changes, which adding 24h of milliseconds would not.
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days, date.getHours(), date.getMinutes());
}

// Weeks start on Monday.
export function startOfWeek(date: Date): Date {
  const day = startOfDay(date);
  const offset = (day.getDay() + 6) % 7;
  return addDays(day, -offset);
}

export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function timezoneLabel(date = new Date()): string {
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "−";
  const hours = Math.floor(Math.abs(offset) / 60);
  const minutes = Math.abs(offset) % 60;
  return `UTC${sign}${hours}${minutes ? `:${pad(minutes)}` : ""}`;
}
