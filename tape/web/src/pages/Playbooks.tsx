import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Setup, type SetupInput } from "../api";
import { money, pct, r, tone } from "../format";

function SetupForm({ initial, onDone }: { initial?: Setup; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [rules, setRules] = useState((initial?.rules ?? []).join("\n"));
  const save = useMutation<unknown, Error, SetupInput>({
    mutationFn: (body) => (initial ? api.updateSetup(initial.id, body) : api.createSetup(body)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["setups"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      onDone();
    },
  });
  return (
    <form
      className="flex flex-col gap-3 rounded-md border border-line p-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ name, description, rules: rules.split("\n").map((x) => x.trim()).filter(Boolean) });
      }}
    >
      <label className="flex flex-col gap-1.5">
        <span className="text-muted">Nazwa</span>
        <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} className="h-9 rounded-md border border-line bg-surface px-2" placeholder="np. London breakout" />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="text-muted">Opis (opcjonalnie)</span>
        <input value={description} onChange={(e) => setDescription(e.target.value)} className="h-9 rounded-md border border-line bg-surface px-2" />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="text-muted">Reguły — jedna w linii; staną się checklistą przy każdej transakcji</span>
        <textarea value={rules} onChange={(e) => setRules(e.target.value)} rows={5} className="rounded-md border border-line bg-surface px-2.5 py-2 leading-relaxed" placeholder={"Wybicie zakresu azjatyckiego po 07:00 UTC\nBrak ważnego newsa ±30 min\nRyzyko ≤ 1% konta"} />
      </label>
      <div className="flex items-center gap-3">
        {save.isError && <span role="alert" className="text-neg">{save.error.message}</span>}
        <div className="flex-1" />
        <button type="button" onClick={onDone} className="h-9 rounded-md border border-line px-4">Anuluj</button>
        <button type="submit" disabled={save.isPending || !name.trim()} className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
          {save.isPending ? "Zapisuję…" : "Zapisz"}
        </button>
      </div>
    </form>
  );
}

export function Playbooks() {
  const qc = useQueryClient();
  const setups = useQuery({ queryKey: ["setups"], queryFn: api.setups });
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteSetup(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["setups"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });
  const noSetup = stats.data?.setups.find((g) => g.key === "Bez setupu");

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5 px-6 py-6">
      <div className="flex items-center">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Playbooki</h1>
          <p className="mt-1 text-muted">Setupy z regułami. Przypisuj je do transakcji — zobaczysz, które naprawdę zarabiają.</p>
        </div>
        <div className="flex-1" />
        {editing === null && (
          <button type="button" onClick={() => setEditing("new")} className="h-9 rounded-md bg-fg px-4 font-medium text-bg">
            Nowy setup
          </button>
        )}
      </div>

      {editing === "new" && <SetupForm onDone={() => setEditing(null)} />}

      {setups.data?.length === 0 && editing === null && (
        <p className="rounded-md border border-dashed border-line p-6 text-center text-muted">Brak setupów. Zacznij od tego, który grasz najczęściej.</p>
      )}

      <ul className="flex flex-col gap-3">
        {setups.data?.map((s) =>
          editing === s.id ? (
            <li key={s.id}>
              <SetupForm initial={s} onDone={() => setEditing(null)} />
            </li>
          ) : (
            <li key={s.id} className="rounded-md border border-line p-4">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="text-[15px] font-medium">{s.name}</h2>
                  {s.description && <p className="mt-0.5 text-muted">{s.description}</p>}
                </div>
                <button type="button" onClick={() => setEditing(s.id)} className="text-xs text-muted hover:text-fg">Edytuj</button>
                <button
                  type="button"
                  onClick={() => {
                    if (confirm(`Usunąć setup „${s.name}”? Transakcje zostaną bez setupu.`)) remove.mutate(s.id);
                  }}
                  className="text-xs text-muted hover:text-neg"
                >
                  Usuń
                </button>
              </div>
              {s.rules.length > 0 && (
                <ol className="mt-2 list-decimal pl-5 text-muted">
                  {s.rules.map((rule) => <li key={rule}>{rule}</li>)}
                </ol>
              )}
              <div className="num mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs">
                {s.stats ? (
                  <>
                    <span><span className="text-muted">transakcje </span>{s.stats.trades}</span>
                    <span><span className="text-muted">win rate </span>{pct(s.stats.win_rate)}</span>
                    <span><span className="text-muted">śr. </span><span className={tone(s.stats.avg_pnl)}>{money(s.stats.avg_pnl)}</span></span>
                    <span><span className="text-muted">avg R </span><span className={tone(s.stats.avg_r)}>{r(s.stats.avg_r)}</span></span>
                    <span><span className="text-muted">łącznie </span><span className={tone(s.stats.net_pnl)}>{money(s.stats.net_pnl)}</span></span>
                    {s.stats.trades < 20 && <span className="text-warn">mała próba — wnioski ostrożnie</span>}
                  </>
                ) : (
                  <span className="text-muted">Brak transakcji z tym setupem.</span>
                )}
              </div>
            </li>
          ),
        )}
      </ul>

      {noSetup && (
        <p className="text-muted">
          Transakcje bez setupu: <span className="num text-fg">{noSetup.trades}</span>, łącznie{" "}
          <span className={`num ${tone(noSetup.net_pnl)}`}>{money(noSetup.net_pnl)}</span>.
        </p>
      )}
    </div>
  );
}
