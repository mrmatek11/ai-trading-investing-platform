import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api, type PaperRun } from "../api";

// Plan dnia przebiegu paper na Telegram/Discord — te same kanały co poranny brief (opt-in per przebieg).
export function PaperAlertsToggle({ run }: { run: PaperRun }) {
  const qc = useQueryClient();
  const brief = useQuery({ queryKey: ["brief"], queryFn: api.brief, staleTime: 60_000 });
  const toggle = useMutation<PaperRun, Error, boolean>({
    mutationFn: (enabled) => api.setPaperAlerts(run.id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["paper"] }),
  });
  const sub = brief.data?.subscription;
  const linked = !!sub && (sub.telegram_linked || sub.discord_linked);
  const checked = toggle.isPending ? !!toggle.variables : run.alerts;
  return (
    <div className="flex flex-col gap-1 text-[13px]">
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={checked} disabled={toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)} className="mt-1 accent-[var(--color-accent)]" />
        <span>
          Wysyłaj plan dnia na Telegram/Discord
          <span className="block text-xs text-muted">
            W aktywny dzień, po zamknięciu zakresu 08:00–09:00 Londynu: zlecenia stop, SL i wielkość pozycji do wpisania u brokera.
          </span>
        </span>
      </label>
      {brief.isSuccess && !linked && (
        <p className="text-xs text-warn">
          Nie masz podłączonego Telegrama ani Discorda — połącz kanał w <Link to="/brief" className="text-fg underline">ustawieniach briefu</Link>.
        </p>
      )}
      {toggle.isError && <span role="alert" className="text-xs text-neg">{toggle.error.message}</span>}
    </div>
  );
}
