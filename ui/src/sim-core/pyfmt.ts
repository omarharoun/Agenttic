/* ============================================================================
   Python-faithful number/string formatting.

   sim-core reproduces the platform's *receipt text* byte-for-byte (Hard Rule
   24: the reason string is the product). Python's `f"{x:.2f}"` and `str(x)` and
   `repr(s)` have specific behaviours JS does not share by default:

     - `format(x, '.2f')` rounds half-to-even on exact dyadic ties (0.125 ->
       "0.12"), whereas JS `(0.125).toFixed(2)` gives "0.13".
     - `str(1.0)` is "1.0" (JS `String(1.0)` is "1").
     - `repr('helpful')` is "'helpful'" (single-quoted).

   These helpers close those gaps. They are the single formatting authority for
   every sim-core module, and the golden parity harness proves them correct.
   ========================================================================== */

function incDecimalString(s: string): string {
  const a = s.split("");
  let i = a.length - 1;
  while (i >= 0) {
    if (a[i] === "9") { a[i] = "0"; i--; }
    else { a[i] = String.fromCharCode(a[i].charCodeAt(0) + 1); break; }
  }
  if (i < 0) a.unshift("1");
  return a.join("");
}

/** Python `format(x, f'.{digits}f')` — fixed decimals with CPython's rounding:
 *  round-half-to-even, applied to the double's EXACT value (not to `x*10^n`,
 *  whose float error would invent or hide ties). Every IEEE-754 double has a
 *  terminating decimal expansion, so a long `toFixed` reveals the exact digits
 *  we need to decide the rounding at position `digits`. */
export function pyFixed(x: number, digits = 2): string {
  if (!Number.isFinite(x)) return x > 0 ? "inf" : x < 0 ? "-inf" : "nan";
  const neg = x < 0 || Object.is(x, -0);
  const ax = Math.abs(x);
  const long = ax.toFixed(80);            // exact fractional expansion (+ zeros)
  const dot = long.indexOf(".");
  const intPart = long.slice(0, dot);
  const frac = long.slice(dot + 1);
  const keep = frac.slice(0, digits);
  const nextDigit = frac.charCodeAt(digits) - 48;
  const tailNonZero = /[1-9]/.test(frac.slice(digits + 1));

  let roundUp: boolean;
  if (nextDigit > 5) roundUp = true;
  else if (nextDigit < 5) roundUp = false;
  else if (tailNonZero) roundUp = true;   // > exact half
  else {
    // exact half -> round to even
    const lastKept = digits > 0
      ? keep.charCodeAt(digits - 1) - 48
      : intPart.charCodeAt(intPart.length - 1) - 48;
    roundUp = lastKept % 2 === 1;
  }

  let ds = intPart + keep;
  if (roundUp) ds = incDecimalString(ds);
  let s: string;
  if (digits === 0) s = ds;
  else { const cut = ds.length - digits; s = ds.slice(0, cut) + "." + ds.slice(cut); }
  if (neg && /[1-9]/.test(ds)) s = "-" + s;
  return s;
}

/** Python `str(float)` — shortest round-trip repr, but integers keep a ".0"
 *  (str(1.0) == "1.0"). Matches CPython for the finite values sim-core emits. */
export function pyStr(x: number): string {
  if (!Number.isFinite(x)) return x > 0 ? "inf" : x < 0 ? "-inf" : "nan";
  if (Number.isInteger(x)) return `${x}.0`;
  return String(x);
}

/** Python `repr(str)` for the criterion-id case — single-quoted, with the two
 *  escapes that can occur in an id. Full CPython repr is more elaborate; ids in
 *  practice are simple tokens, and the parity harness guards the rest. */
export function pyRepr(s: string): string {
  const body = s.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  return `'${body}'`;
}

/** Python `repr(list[str])` — e.g. ['tone', 'acc']. */
export function pyListRepr(items: string[]): string {
  return `[${items.map(pyRepr).join(", ")}]`;
}

/** Python `format(x, f'+.{digits}f')` — fixed decimals with a forced sign. */
export function pyFixedSigned(x: number, digits = 2): string {
  const sign = x < 0 ? "-" : "+";
  return sign + pyFixed(Math.abs(x), digits);
}

/** Python `format(x, f'.{digits}%')` — percent, round-half-to-even, trailing %.
 *  The value is scaled by 100 then formatted (sign only when negative). */
export function pyPct(x: number, digits = 0): string {
  return pyFixed(x * 100, digits) + "%";
}

/** Python `format(x, f'+.{digits}%')` — percent with a forced sign. */
export function pyPctSigned(x: number, digits = 0): string {
  const sign = x < 0 ? "-" : "+";
  return sign + pyFixed(Math.abs(x) * 100, digits) + "%";
}

/** Python `format(x, 'g')` — general format: up to 6 significant digits, strip
 *  trailing zeros, integers render without a decimal point (2.0 -> "2"). */
export function pyG(x: number): string {
  if (!Number.isFinite(x)) return x > 0 ? "inf" : x < 0 ? "-inf" : "nan";
  if (x === 0) return "0";
  return String(parseFloat(x.toPrecision(6)));
}
