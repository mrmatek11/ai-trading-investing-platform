import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";

// Własny klucz Claude: przegląd AI i mapowanie kolumn idą na Twój rachunek w Anthropic.
export function AiKeyCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["ai-settings"], queryFn: api.aiSettings });
  const [key, setKey] = useState("");
  const [model, setModel] = useState("claude-opus-5-5");
  useEffect(() => {
    if (q.data) setModel(q.data.model);
  }, [q.data]);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["ai-settings"] });
    qc.invalidateQueries({ queryKey: ["review"] });
  };
  const save = useMutation<unknown, Error>({
    mutationFn: () => api.saveAiKey(key.trim() ? { api_key: key.trim(), model } : { model }),
    onSuccess: () => {
      setKey("");
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: api.deleteAiKey, onSuccess: refresh });
  const d = q.data;
  if (!d) return null;
  const changed = key.trim() !== "" || (d.has_key && model !== d.model);

  return (
    <form
      className="flex flex-col gap-4 rounded-md border border-line p-5"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="flex items-baseline gap-3">
        <h2 className="text-[13px] font-medium">Twój klucz AI</h2>
        <span className={`text-xs ${d.has_key ? "text-pos" : "text-muted"}`}>
          {d.has_key ? `podłączony · …${d.last4}` : d.server_key ? "używasz klucza serwera" : "brak — funkcje AI wyłączone"}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-muted">
        Przegląd journala i dopasowanie kolumn przy imporcie będą szły na Twój rachunek w Anthropic. Klucz szyfrujemy przed zapisem
        i nigdy go nie pokazujemy — widać tylko cztery ostatnie znaki. Klucz utworzysz w console.anthropic.com → API Keys.
      </p>
      {!d.encryption ? (
        <p className="text-xs text-warn">Serwer nie ma klucza szyfrowania (TAPE_SECRET_KEYS) — zapisywanie kluczy jest wyłączone.</p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-[1fr_220px]">
            <label className="flex flex-col gap-1.5">
              <span className="text-muted">{d.has_key ? "Nowy klucz (zostaw puste, żeby zmienić tylko model)" : "Klucz API (sk-ant-…)"}</span>
              <input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" spellCheck={false}
                placeholder="sk-ant-…" className="num h-9 rounded-md border border-line bg-surface px-2 placeholder:text-muted" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-muted">Model</span>
              <select value={model} onChange={(e) => setModel(e.target.value)} className="h-9 rounded-md border border-line bg-surface px-2">
                {Object.entries(d.models).map(([id, label]) => (
                  <option key={id} value={id}>{label}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex items-center gap-3">
            {save.isError && <span role="alert" className="text-neg">{save.error.message}</span>}
            {save.isSuccess && !changed && <span className="text-pos">Klucz sprawdzony i zapisany</span>}
            <div className="flex-1" />
            {d.has_key && (
              <button type="button" onClick={() => remove.mutate()} className="h-9 rounded-md px-3 text-muted hover:text-neg">Odłącz</button>
            )}
            <button type="submit" disabled={save.isPending || (!d.has_key && !key.trim()) || !changed}
              className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
              {save.isPending ? "Sprawdzam klucz…" : "Zapisz"}
            </button>
          </div>
        </>
      )}
    </form>
  );
}
