/* Shared, pure billing helpers — formatting + credit math used by the public
   pricing page and the in-app billing dashboard. Kept dependency-free and
   framework-free so it's unit-testable (see billing.test.ts) and safe in the
   public bundle. Mirrors the backend: money is integer cents, 1 credit == 1
   cent by default (credit_cent_value). */

const SYMBOLS: Record<string, string> = { usd: "$", eur: "€", gbp: "£" };

export function currencySymbol(currency = "usd"): string {
  return SYMBOLS[currency.toLowerCase()] || "$";
}

/** Format integer cents as a money string, e.g. 2900 -> "$29.00". Whole-dollar
 *  amounts drop the decimals when `compact` is set (for headline prices). */
export function money(cents: number, currency = "usd", compact = false): string {
  const sym = currencySymbol(currency);
  const dollars = cents / 100;
  if (compact && cents % 100 === 0) return `${sym}${dollars.toFixed(0)}`;
  return `${sym}${dollars.toFixed(2)}`;
}

/** Convert a credit balance to a money string (credits are cents by default). */
export function creditsToMoney(credits: number, creditCentValue = 1, currency = "usd"): string {
  return money(credits * creditCentValue, currency);
}

/** Percentage share of a value within a total (0 when total is 0). */
export function sharePct(value: number, total: number): number {
  return total > 0 ? Math.round((value / total) * 100) : 0;
}
