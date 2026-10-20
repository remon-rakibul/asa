// SMS segment counting — mirrors utils/sms_segments.py (keep them in sync).
// GSM-7: 160 chars single / 153 per part. Anything else (all Bangla) is
// UCS-2: 70 single / 67 per part — so Bangla templates cost far more parts.

const GSM7_BASIC = new Set(
  "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?" +
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà",
);
const GSM7_EXT = new Set("^{}\\[~]|€\f");

export type SmsCount = { encoding: "gsm7" | "ucs2"; units: number; segments: number; perPart: number };

export function countSms(text: string): SmsCount {
  const chars = Array.from(text); // code points, like Python str iteration
  const gsm = chars.every((c) => GSM7_BASIC.has(c) || GSM7_EXT.has(c));
  const units = gsm
    ? chars.reduce((n, c) => n + (GSM7_EXT.has(c) ? 2 : 1), 0)
    : chars.reduce((n, c) => n + ((c.codePointAt(0) ?? 0) > 0xffff ? 2 : 1), 0);
  const [single, part] = gsm ? [160, 153] : [70, 67];
  const segments = units === 0 ? 0 : units <= single ? 1 : Math.ceil(units / part);
  return { encoding: gsm ? "gsm7" : "ucs2", units, segments, perPart: segments > 1 ? part : single };
}
