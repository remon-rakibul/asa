// Mirrors tests/test_sms_segments.py — the UI counter must match what the
// backend bills.
import { describe, expect, it } from "vitest";
import { countSms } from "@/lib/smsSegments";

const CASES: [string, number][] = [
  ["", 0],
  ["a".repeat(160), 1],
  ["a".repeat(161), 2],
  ["a".repeat(306), 2],
  ["a".repeat(307), 3],
  ["€".repeat(80), 1],
  ["€".repeat(81), 2],
  ["আ".repeat(70), 1],
  ["আ".repeat(71), 2],
  ["আ".repeat(134), 2],
  ["আ".repeat(135), 3],
  ["hello আ", 1],
];

describe("countSms", () => {
  it.each(CASES)("segments(%#)", (text, parts) => {
    expect(countSms(text).segments).toBe(parts);
  });

  it("detects Bangla as UCS-2", () => {
    expect(countSms("Your serial is 5").encoding).toBe("gsm7");
    expect(countSms("সিরিয়াল ৫").encoding).toBe("ucs2");
  });
});
