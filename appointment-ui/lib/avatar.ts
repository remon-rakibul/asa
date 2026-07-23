// Deterministic avatar helpers — every doctor/reviewer without a photo gets
// a stable, personal-feeling gradient derived from their name, instead of one
// identical placeholder for everyone.

export function nameHue(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360;
  return h;
}

export function avatarGradient(seed: string): string {
  const h = nameHue(seed);
  return `linear-gradient(135deg, hsl(${h} 65% 52%), hsl(${(h + 45) % 360} 70% 42%))`;
}

export function initialsOf(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .filter((w) => !/^(dr\.?|ডাঃ|ডা\.|ডঃ)$/i.test(w))
    .slice(0, 2)
    .map((w) => w[0] ?? "")
    .join("")
    .toUpperCase();
}
