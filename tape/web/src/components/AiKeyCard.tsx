import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type AiProvider } from "../api";

const HELP: Record<AiProvider, { placeholder: string; where: string }> = {
  anthropic: { placeholder: "sk-ant-…", where: "console.anthropic.com → API Keys" },
  deepseek: { placeholder: "sk-…", where: "platform.deepseek.com → API keys" },
};

// Własny klucz AI (Claude albo DeepSeek): przegląd AI i mapowanie kolumn idą na rachunek użytkownika.
export function AiKeyCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["ai-settings"], queryFn: api.aiSettings });
  const [key, setKey] = useState("");
  const [provider, setProvider] = useState<AiProvider>("anthropic");
  const [model, setModel] = useState("claude-opus-5-5");
  useEffect(() => {
    if (q.data) {
      setModel(q.data.model);
      setProvider(q.data.model_provider[q.data.model] ?? "anthropic");
    }
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
  const sameProvider = d.has_key && d.provider === provider;
  const changed = key.trim() !== "" || (sameProvider && model !== d.model);
  const models = Object.entries(d.models).filter(([id]) => d.model_provider[id] === provider);
  const pick = (p: AiProvider) => {
    setProvider(p);
    setModel(d.has_key && d.provider === p ? d.model : (Object.keys(d.models).find((id) => d.model_provider[id] === p) ?? ""));
    save.reset();
  };

  return (
    <form
      className="flex flex-col gap-4 rounded-md border border-line p-5"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[13px] font-medium">Twój klucz AI</h2>
        <span className={`text-xs ${d.has_key ? "text-pos" : "text-muted"}`}>
          {d.has_key
            ? `${d.providers[d.provider ?? "anthropic"]} · …${d.last4}`
            : d.server_key
              ? `używasz klucza serwera (${d.providers[d.server_provider ?? "anthropic"]})`
              : "brak — funkcje AI wyłączone"}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-muted">
        Przegląd journala i dopasowanie kolumn przy imporcie idą na Twój rachunek u wybranego dostawcy. Klucz szyfrujemy przed zapisem i
        nigdy go nie pokazujemy — widać tylko cztery ostatnie znaki. Niezależnie od dostawcy AI dostaje wyłącznie fakty policzone przez kod,
        a wnioski z liczbami spoza faktów są odrzucane.
      </p>
      {!d.encryption ? (
        <p className="text-xs text-warn">Serwer nie ma klucza szyfrowania (TAPE_SECRET_KEYS) — zapisywanie kluczy jest wyłączone.</p>
      ) : (
        <>
          <div role="radiogroup" aria-label="Dostawca AI" className="flex w-fit rounded-md border border-line p-0.5">
            {(Object.keys(d.providers) as AiProvider[]).map((p) => (
              <button key={p} type="button" role="radio" aria-checked={provider === p} onClick={() => pick(p)}
                className={`h-8 rounded px-3 text-xs ${provider === p ? "bg-fg text-bg" : "text-muted hover:text-fg"}`}>
                {d.providers[p]}
              </button>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_260px]">
            <label className="flex flex-col gap-1.5">
              <span className="text-muted">
                {sameProvider ? "Nowy klucz (zostaw puste, żeby zmienić tylko model)" : `Klucz API (${HELP[provider].where})`}
              </span>
              <input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" spellCheck={false}
                placeholder={HELP[provider].placeholder} className="num h-9 rounded-md border border-line bg-surface px-2 placeholder:text-muted" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-muted">Model</span>
              <select value={model} onChange={(e) => setModel(e.target.value)} className="h-9 rounded-md border border-line bg-surface px-2">
                {models.map(([id, label]) => (
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
            <button type="submit" disabled={save.isPending || (!sameProvider && !key.trim()) || !changed}
              className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
              {save.isPending ? "Sprawdzam klucz…" : "Zapisz"}
            </button>
          </div>
        </>
      )}
    </form>
  );
}
