import { BANDS, BAND_SHORT } from '../lib/labels';
import type { Band } from '../lib/types';

/**
 * Band indicators use **shape and label as well as colour** (FR-118).
 *
 * This is not decoration. Around 8% of men have some form of colour vision
 * deficiency, and amber/red is exactly the pair they most often cannot
 * separate. A reviewer who cannot distinguish "decided automatically" from
 * "blocked" is being shown the opposite of what the autonomy model intends. The
 * glyph carries the meaning; the colour reinforces it for everyone else.
 *
 *   square   decided automatically   (settled, closed)
 *   circle   needs your decision     (open, needs a look)
 *   diamond  blocked                 (hazard convention)
 *
 * The wording lives in `labels.ts` — this file owns the shapes and colours only,
 * so a rename never has to happen in two places.
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

export function BandGlyph({ band, size = 9 }: { band: Band; size?: number }) {
  return (
    <span
      aria-hidden
      className={`inline-block shrink-0 ${SHAPE[band]} ${COLOUR[band]}`}
      style={{ width: size, height: size }}
    />
  );
}

export function BandLegend() {
  return (
    <div className="flex flex-wrap gap-4 text-tiny text-muted">
      {BANDS.map((band) => (
        <span key={band} className="flex items-center gap-2">
          <BandGlyph band={band} />
          {BAND_SHORT[band]}
        </span>
      ))}
    </div>
  );
}
