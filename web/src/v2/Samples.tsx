// Demo only: synthetic documents for the handover, so the demo can be finished without one's own
// (PR-14.1). Offered by the tour and by Priya's two upload steps.

export const SAMPLES = {
  certificate: { href: "/demo/sample-certificate-of-passing.pdf", name: "sample certificate of passing" },
  authority: { href: "/demo/sample-succession-certificate.pdf", name: "sample succession certificate" },
} as const;
export type SampleKind = keyof typeof SAMPLES;

/** A link to a sample document, for the screens where one is uploaded. */
export function Sample({ which }: { which: SampleKind }) {
  const s = SAMPLES[which];
  return (
    <p className="v2-muted v2-small">
      Demo: no document to hand? <a href={s.href} download>Download the {s.name} (PDF)</a>, then upload it. It's marked as a specimen, so the checks flag it.
    </p>
  );
}
