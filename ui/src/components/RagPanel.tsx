import { useRef, useState } from 'react';
import { Upload } from 'lucide-react';

import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { num } from '../lib/format';

/**
 * Universal RAG grounding panel (FR-039, FR-041, FR-068).
 *
 * Present on every route because grounding is cross-cutting: the `GroundedLLM`
 * wrapper is the only LLM entry point, so a document uploaded here becomes
 * context for every agent immediately, with no restart.
 *
 * The active embedding function is displayed rather than assumed. Three
 * strategies are possible — gateway (MODEL_EMBEDDING), local MiniLM, and a
 * hashed-n-gram fallback — and only the first two are semantic. The fallback
 * retrieves on shared vocabulary rather than meaning, which is a real downgrade
 * the operator needs to know about rather than infer from poor results.
 */

const COLLECTIONS = ['pricing_policy', 'market_intel', 'product_kb', 'user_uploads'];

export function RagPanel() {
  const stats = useApi(() => api.ragStats(), [], 30000);
  const [target, setTarget] = useState('user_uploads');
  const [status, setStatus] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = async (file: File) => {
    setStatus('Embedding…');
    try {
      const result = await api.ragIngestFile(file, target);
      setStatus(`${file.name}: ${result.ingested} chunk(s) embedded.`);
      await stats.reload();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  const lexical = stats.data?.semantic === false;
  const mismatch = stats.data?.mismatch ?? null;

  const reEmbed = async () => {
    setStatus('Dropping collections…');
    try {
      const result = await api.ragReset();
      setStatus(result.detail);
      await stats.reload();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div>
      <div className="label mb-2.5">Grounding</div>

      <dl className="space-y-1.5">
        {COLLECTIONS.map((name) => (
          <div key={name} className="flex justify-between">
            <dt className="text-tiny text-faint">{name}</dt>
            <dd className="text-tiny text-muted">
              {num(stats.data?.collections?.[name]?.chunks ?? 0)}
            </dd>
          </div>
        ))}
      </dl>

      <label className="sr-only" htmlFor="rag-collection">
        Target collection
      </label>
      <select
        id="rag-collection"
        value={target}
        onChange={(e) => setTarget(e.target.value)}
        className="input mt-3 text-tiny py-1.5"
      >
        {COLLECTIONS.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>

      <button
        onClick={() => fileRef.current?.click()}
        disabled={!stats.data?.available}
        className="mt-2 w-full border border-dashed border-line rounded-lg py-2
                   text-tiny text-faint hover:text-muted hover:border-info
                   transition-colors disabled:opacity-40 flex items-center
                   justify-center gap-2"
      >
        <Upload size={12} aria-hidden />
        Upload policy documents
      </button>
      <input
        ref={fileRef}
        type="file"
        accept={(stats.data?.supported_uploads ?? ['.txt', '.md', '.csv']).join(',')}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void upload(file);
          e.target.value = '';
        }}
      />

      {status && <p className="text-micro text-faint mt-2 leading-snug">{status}</p>}

      {mismatch && (
        // Silent failure here would present as merely poor retrieval, or as an
        // opaque dimensionality error on the operator's next query.
        <div
          className={`mt-3 border rounded-lg p-2.5 ${
            mismatch.compatible
              ? 'border-accent/40 bg-accent-wash'
              : 'border-danger/40 bg-danger-wash'
          }`}
        >
          <p
            className={`text-micro leading-snug ${
              mismatch.compatible ? 'text-accent' : 'text-danger'
            }`}
          >
            {mismatch.detail}
          </p>
          <button
            onClick={reEmbed}
            className="mt-2 w-full border border-line rounded-md py-1 text-micro
                       text-muted hover:text-ink transition-colors"
          >
            Drop collections and re-embed
          </button>
        </div>
      )}

      <p className="text-micro text-faint mt-3 leading-snug">
        {stats.data?.available
          ? `Embeddings: ${stats.data.embedding_model}`
          : 'Vector store unavailable — recommendations will carry no citations.'}
      </p>
      {stats.data?.chunking && (
        <p className="text-micro text-faint mt-1 leading-snug">
          Chunking: {stats.data.chunking.strategy} ·{' '}
          {stats.data.chunking.chunk_size}/{stats.data.chunking.chunk_overlap}
        </p>
      )}
      {lexical && (
        <p className="text-micro text-accent mt-1.5 leading-snug">
          Lexical fallback in use: retrieval matches shared vocabulary, not
          meaning. Set MODEL_EMBEDDING for gateway embeddings, or CA_BUNDLE_PATH
          to unblock the local model.
        </p>
      )}
      {lexical && stats.data?.degraded_reason && (
        <p className="text-micro text-faint mt-1 leading-snug break-words">
          {stats.data.degraded_reason}
        </p>
      )}
    </div>
  );
}
