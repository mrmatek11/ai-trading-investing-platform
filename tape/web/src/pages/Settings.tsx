import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { AiKeyCard } from "../components/AiKeyCard";
import { ClaudeConnectCard } from "../components/ClaudeConnectCard";

export function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [email, setEmail] = useState("");
  const [weekly, setWeekly] = useState(true);
  const [alerts, setAlerts] = useState(true);
  useEffect(() => {
    if (q.data) {
      setEmail(q.data.email);
      setWeekly(q.data.weekly_report);
      setAlerts(q.data.prop_alerts);
    }
  }, [q.data]);
  const save = useMutation<unknown, Error>({
    mutationFn: () => api.saveSettings({ email: email.trim(), weekly_report: weekly, prop_alerts: alerts }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const preview = useMutation({ mutationFn: api.weeklyPreview });

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-5 px-6 py-8">
      <h1 className="text-xl font-semibold tracking-tight">Ustawienia</h1>
      <AiKeyCard />
      <ClaudeConnectCard />
      <form
        className="flex flex-col gap-4 rounded-md border border-line p-5"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <h2 className="text-[13px] font-medium">Powiadomienia e-mail</h2>
        {q.data && !q.data.mail_configured && (
          <p className="text-xs text-warn">Serwer nie ma skonfigurowanej wysyłki (SMTP) — ustawienia się zapiszą, e-maile ruszą po konfiguracji.</p>
        )}
        <label className="flex flex-col gap-1.5">
          <span className="text-muted">Adres e-mail</span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ty@example.com" className="h-9 rounded-md border border-line bg-surface px-2 placeholder:text-muted" />
        </label>
        <label className="flex items-start gap-2.5">
          <input type="checkbox" checked={weekly} onChange={(e) => setWeekly(e.target.checked)} className="mt-1" />
          <span>
            Raport tygodniowy <span className="text-muted">— poniedziałek rano: wynik tygodnia, koszt błędów, limity prop, ważne dane USD</span>
          </span>
        </label>
        <label className="flex items-start gap-2.5">
          <input type="checkbox" checked={alerts} onChange={(e) => setAlerts(e.target.checked)} className="mt-1" />
          <span>
            Alert limitu prop <span className="text-muted">— gdy zostaje ≤ 25% dziennego limitu albo limit jest złamany (maks. raz dziennie na konto)</span>
          </span>
        </label>
        <div className="flex items-center gap-3">
          {save.isError && <span role="alert" className="text-neg">{save.error.message}</span>}
          {save.isSuccess && <span className="text-pos">Zapisano</span>}
          <div className="flex-1" />
          <button type="button" onClick={() => preview.mutate()} disabled={preview.isPending} className="h-9 rounded-md border border-line px-4 disabled:opacity-40">
            Podgląd raportu
          </button>
          <button type="submit" disabled={save.isPending} className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
            Zapisz
          </button>
        </div>
      </form>
      {preview.isError && <p className="text-muted">{preview.error.message}</p>}
      {preview.data && (
        <section aria-label="Podgląd raportu" className="overflow-hidden rounded-md border border-line">
          <p className="border-b border-line px-4 py-2 text-xs text-muted">Temat: {preview.data.subject}</p>
          {/* sandbox bez skryptów: treść maila w izolacji od aplikacji */}
          <iframe title="Podgląd raportu tygodniowego" sandbox="" srcDoc={preview.data.html} className="h-[560px] w-full bg-[#0b0b0d]" />
        </section>
      )}
    </div>
  );
}
