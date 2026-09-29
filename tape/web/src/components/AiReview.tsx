import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AiReview as Review, type ReviewFinding } from "../api";
import { when } from "../format";

function conclusions(n: number) {
  if (n === 1) return "wniosek";
  const d = n % 10, dd = n % 100;
  return d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? "wnioski" : "wniosków";
}

function Facts({ ids, review }: { ids: string[]; review: Review }) {
  const byId = new Map(review.facts.map((f) => [f.id, f.text]));
  return (
    <span className="ml-1.5 inline-flex gap-1 align-middle">
      {ids.map((id) => (
        <span key={id} title={byId.get(id)} className="num cursor-help rounded border border-line px-1 text-[11px] text-muted">
          {id}
        </span>
      ))}
    </span>
  );
}

function Findings({ title, items, review, tone }: { title: string; items: ReviewFinding[]; review: Review; tone: string }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h3 className={`mb-1.5 text-xs ${tone}`}>{title}</h3>
      <ul className="flex flex-col gap-2">
        {items.map((f) => (
          <li key={f.title}>
            <span className="font-medium">{f.title}</span>
            <Facts ids={f.facts} review={review} />
            <p className="text-muted">{f.detail}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AiReview() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["review"], queryFn: api.review });
  const gen = useMutation<Review, Error>({
    mutationFn: api.generateReview,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review"] }),
  });
  if (!q.data) return null;
  const { review, ai_available, trades, min_trades } = q.data;
  const enough = trades >= min_trades;

  return (
    <section aria-label="Przegląd AI" className="rounded-md border border-line px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[13px] font-medium">Przegląd AI</h2>
        <span className="text-xs text-muted">
          liczby liczy kod — AI tylko je interpretuje i wskazuje fakty (najedź na F…)
        </span>
        <div className="flex-1" />
        {ai_available && enough && (!review || review.stale) && (
          <button type="button" onClick={() => gen.mutate()} disabled={gen.isPending} className="h-8 rounded-md border border-line px-3 disabled:opacity-40">
            {gen.isPending ? "Analizuję…" : review ? "Odśwież — są nowe dane" : "Wygeneruj przegląd"}
          </button>
        )}
      </div>
      {!ai_available && <p className="mt-2 text-muted">AI nie jest skonfigurowane na tym serwerze.</p>}
      {ai_available && !enough && (
        <p className="mt-2 text-muted">
          Przegląd będzie dostępny od {min_trades} zamkniętych transakcji (masz <span className="num">{trades}</span>).
        </p>
      )}
      {gen.isError && <p role="alert" className="mt-2 text-neg">{gen.error.message}</p>}
      {review && (
        <div className="mt-3 flex flex-col gap-3">
          {review.headline && <p className="text-[15px]">{review.headline}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <Findings title="Co działa" items={review.strengths} review={review} tone="text-pos" />
            <Findings title="Gdzie uciekają pieniądze" items={review.leaks} review={review} tone="text-neg" />
          </div>
          {review.actions.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-xs text-accent">Do zrobienia</h3>
              <ol className="list-decimal space-y-1 pl-5">
                {review.actions.map((a) => (
                  <li key={a.text}>
                    {a.text}
                    <Facts ids={a.facts} review={review} />
                  </li>
                ))}
              </ol>
            </div>
          )}
          <p className="text-xs text-muted">
            {when(review.created_at)}
            {review.stale && " · dane zmieniły się od tego przeglądu"}
            {review.dropped > 0 && ` · odrzucono ${review.dropped} ${conclusions(review.dropped)} bez pokrycia w faktach`}
            {review.caveat && ` · ${review.caveat}`}
          </p>
        </div>
      )}
    </section>
  );
}
