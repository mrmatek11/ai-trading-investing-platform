import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { api, type ImportReport, type Mapping, type SuggestResponse } from "../api";
import { MappingStep } from "../components/MappingStep";

const BROKERS = [
  { value: "auto", label: "Wykryj automatycznie" },
  { value: "xtb", label: "XTB · zamknięte pozycje (XLSX/CSV)" },
  { value: "mt5", label: "MetaTrader 5 · lista transakcji (Deals)" },
  { value: "ibkr", label: "Interactive Brokers · raport Flex (XML)" },
  { value: "custom", label: "Inny broker — dopasuj kolumny" },
];

const TZ_HINT: Record<string, string> = {
  xtb: "Europe/Warsaw",
  mt5: "Etc/GMT-2",
  ibkr: "America/New_York",
};

export function ImportPage() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [broker, setBroker] = useState("auto");
  const [tz, setTz] = useState("");
  const [suggestion, setSuggestion] = useState<SuggestResponse | null>(null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["stats"] });
    qc.invalidateQueries({ queryKey: ["positions"] });
  };
  const suggest = useMutation<SuggestResponse, Error>({
    mutationFn: () => api.suggestMapping(file!),
    onSuccess: setSuggestion,
  });
  const mutation = useMutation<ImportReport, Error, Mapping | undefined>({
    mutationFn: (mapping) => api.importFile(file!, broker, tz, mapping),
    onSuccess: (report, mapping) => {
      refresh();
      if (mapping) setSuggestion(null);
      // nierozpoznany format → od razu proponujemy mapowanie kolumn
      else if (!report.detected && report.fills === 0) suggest.mutate();
    },
  });
  const report = mutation.data;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-5 px-6 py-8">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Import transakcji</h1>
        <p className="mt-1 text-muted">
          Ponowny import tego samego pliku nie tworzy duplikatów. Czas zapisujemy w UTC. MT5 i IBKR możesz też{" "}
          <Link to="/connections" className="text-fg underline">połączyć na stałe</Link>.
        </p>
      </div>

      <form
        className="flex flex-col gap-4 rounded-md border border-line p-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (!file) return;
          setSuggestion(null);
          if (broker === "custom") suggest.mutate();
          else mutation.mutate(undefined);
        }}
      >
        <label className="flex flex-col gap-2 rounded-md border border-dashed border-[#34343a] p-6 text-center hover:border-muted">
          <span>{file ? file.name : "Wybierz plik z historią transakcji"}</span>
          <span className="text-xs text-muted">CSV · XLSX · XML — XTB, MetaTrader 5, IBKR Flex</span>
          <input
            type="file"
            accept=".csv,.xlsx,.xlsm,.txt,.xml"
            className="sr-only"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setSuggestion(null);
              mutation.reset();
            }}
          />
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5">
            <span className="text-muted">Format</span>
            <select value={broker} onChange={(e) => setBroker(e.target.value)} className="h-9 rounded-md border border-line bg-surface px-2">
              {BROKERS.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-muted">Strefa czasowa pliku (opcjonalnie)</span>
            <input
              value={tz}
              onChange={(e) => setTz(e.target.value)}
              placeholder={TZ_HINT[broker] ?? "domyślna dla formatu"}
              className="num h-9 rounded-md border border-line bg-surface px-2 placeholder:text-muted"
            />
          </label>
        </div>

        <button type="submit" disabled={!file || mutation.isPending || suggest.isPending} className="h-10 rounded-md bg-fg font-medium text-bg disabled:opacity-40">
          {mutation.isPending ? "Importuję…" : suggest.isPending ? "Analizuję kolumny…" : broker === "custom" ? "Dalej: mapowanie kolumn" : "Importuj"}
        </button>
      </form>

      {suggest.isError && <p role="alert" className="text-neg">Nie udało się odczytać pliku: {suggest.error.message}</p>}
      {suggestion && (
        <MappingStep data={suggestion} pending={mutation.isPending} onSubmit={(m) => mutation.mutate(m)} onCancel={() => setSuggestion(null)} />
      )}

      {mutation.isError && <p role="alert" className="text-neg">Import nie powiódł się: {mutation.error.message}</p>}

      {report && !suggestion && !suggest.isPending && (
        <section aria-label="Raport importu" className="rounded-md border border-line p-5">
          <h2 className="mb-3 text-[13px] font-medium">
            Raport importu{report.detected ? ` · format: ${report.detected.toUpperCase()}` : ""}
          </h2>
          <p className="num">
            <span className="text-pos">{report.new} nowych</span>
            <span className="text-muted"> · {report.duplicates} duplikatów · </span>
            <span className={report.error_count ? "text-warn" : "text-muted"}>{report.error_count} błędów</span>
          </p>
          {report.errors.length > 0 && (
            <ul className="mt-3 list-disc pl-5 text-muted">
              {report.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
