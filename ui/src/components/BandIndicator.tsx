import type { Band } from '../lib/types';

/**
 * Band indicators use **shape and label as well as colour** (FR-118).
 *
 * This is not decoration. Around 8% of men have some form of colour vision
 * deficiency, and amber/red is exactly the pair they most often cannot
 * separate. A reviewer who cannot distinguish "auto-approve" from "escalate" is
 * being shown the opposite of what the autonomy model intends. The glyph
 * carries the meaning; the colour reinforces it for everyone else.
 *
 *   square   Auto-approve   (settled, closed)
 *   circle   Review         (open, needs a look)
 *   diamond  Escalate       (hazard convention)
 */

const SHAPE: Record<Band, string> = {
  auto_approve: 'rounded-none',
  review: 'rounded-full',
  escalate: 'rounded-[2px] rotate-45',
};

const COLOUR: Record<Band, string> = {
  auto_approve: 'bg-accent',
  review: 'bg-info',
  escalate: 'bg-danger',
};

const TEXT: Record<Band, string> = {
  auto_approve: 'text-accent',
  review: 'text-info',
  escalate: 'text-danger',
};

const LABEL: Record<Band, string> = {
  auto_approve: 'Auto-approve',
  review: 'Review',
  escalate: 'Escalate',
};

export function BandGlyph({ band, size = 9 }: { band: Band; size?: number }) {
  return (
    <span
      aria-hidden
      className={`inline-block shrink-0 ${SHAPE[band]} ${COLOUR[band]}`}
      style={{ width: size, height: size }}
    />
  );
}

export function BandIndicator({
  band,
  reason,
  compact = false,
}: {
  band: Band;
  reason?: string;
  compact?: boolean;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <span className="pt-1">
        <BandGlyph band={band} />
      </span>
      <div className="min-w-0">
        <div className={`text-xs tracking-wide ${TEXT[band]}`}>{LABEL[band]}</div>
        {reason && !compact && (
          <div className="text-tiny text-faint leading-snug">{reason}</div>
        )}
      </div>
    </div>
  );
}

export function BandLegend() {
  return (
    <div className="flex flex-wrap gap-4 text-tiny text-muted">
      {(['auto_approve', 'review', 'escalate'] as Band[]).map((band) => (
        <span key={band} className="flex items-center gap-2">
          <BandGlyph band={band} />
          {LABEL[band]}
        </span>
      ))}
    </div>
  );
}

export { LABEL as bandLabels, COLOUR as bandColours, TEXT as bandTextColours };
