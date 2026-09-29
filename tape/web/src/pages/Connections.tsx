import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Connection, type SyncReport } from "../api";
import { when } from "../format";

const KIND: Record<Connection["kind"], string> = {
  ibkr_flex: "Interactive Brokers · Flex",
  mt5_push: "MetaTrader 5 · EA Tape Sync",
};

const STATUS: Record<Connection["last_status"], { label: string; cls: string }> = {
  never: { label: "jeszcze nie synchronizowano", cls: "text-muted" },
  ok: { label: "OK", cls: "text-pos" },
  partial: { label: "częściowo", cls: "text-warn" },
  error: { label: "błąd", cls: "text-neg" },
};

const input = "h-9 rounded-md border border-line bg-surface px-2 placeholder:text-muted";

function useRefresh() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["connections"] });
    qc.invalidateQueries({ queryKey: ["stats"] });
    qc.invalidateQueries({ queryKey: ["positions"] });
  };
}

function Row({ c }: { c: Connection }) {
  const refresh = useRefresh();
  const [report, setReport] = useState<SyncReport | null>(null);
  const sync = useMutation<SyncReport, Error>({
    mutationFn: () => api.syncConnection(c.id),
    onSuccess: (r) => {
      setReport(r);
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: () => api.deleteConnection(c.id), onSuccess: refresh });
  const st = STATUS[c.last_status];
  return (
    <li className="flex flex-col gap-2 border-b border-line px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{c.label}</div>
          <div className="text-xs text-muted">{KIND[c.kind]}</div>
        </div>
        <div className="num text-right text-xs">
          <div className={st.cls}>{st.label}</div>
          <div className="text-muted">
            {c.last_sync_at ? `${when(c.last_sync_at)}${c.last_status === "error" ? "" : ` · ${c.last_new} nowych`}` : "—"}
          </div>
        </div>
        {c.kind === "ibkr_flex" && (
          <button type="button" onClick={() => sync.mutate()} disabled={sync.isPending} className="h-8 rounded-md border border-line px-3 disabled:opacity-40">
            {sync.isPending ? "Pobieram…" : "Synchronizuj"}
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            if (confirm(`Usunąć połączenie „${c.label}”? Zaimportowane transakcje zostają.`)) remove.mutate();
          }}
          className="h-8 rounded-md px-2 text-muted hover:text-neg"
          aria-label={`Usuń połączenie ${c.label}`}
        >
          Usuń
        </button>
      </div>
      {c.last_error && <p className="text-xs text-warn">{c.last_error}</p>}
      {sync.isError && <p role="alert" className="text-xs text-neg">{sync.error.message}</p>}
      {report && !sync.isPending && (
        <p className="num text-xs text-muted">
          <span className="text-pos">{report.new} nowych</span> · {report.duplicates} duplikatów
        </p>
      )}
    </li>
  );
}

function AddIbkr({ encryption, onDone }: { encryption: boolean; onDone: () => void }) {
  const refresh = useRefresh();
  const [label, setLabel] = useState("Interactive Brokers");
  const [token, setToken] = useState("");
  const [queryId, setQueryId] = useState("");
  const [tz, setTz] = useState("");
  const add = useMutation<Connection, Error>({
    mutationFn: () => api.addIbkr({ label, token: token.trim(), query_id: queryId.trim(), tz: tz.trim() }),
    onSuccess: async (c) => {
      refresh();
      onDone();
      await api.syncConnection(c.id).catch(() => undefined); // pierwsze pobranie od razu
      refresh();
    },
  });
  if (!encryption) {
    return (
      <p className="rounded-md border border-line p-4 text-muted">
        Serwer nie ma skonfigurowanego klucza szyfrowania (<span className="num">TAPE_SECRET_KEYS</span>), więc nie przyjmie tokenu IBKR.
        Do tego czasu możesz importować raport Flex jako plik XML.
      </p>
    );
  }
  return (
    <form
      className="flex flex-col gap-3 rounded-md border border-line p-4"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <ol className="list-decimal space-y-1 pl-5 text-muted">
        <li>Portal IBKR → Performance &amp; Reports → Flex Queries → utwórz Activity Flex Query z sekcją Trades (format XML).</li>
        <li>W tym samym miejscu włącz Flex Web Service i wygeneruj token (tylko do odczytu raportów).</li>
        <li>Wklej token i numer zapytania (Query ID) poniżej. Token szyfrujemy przed zapisem.</li>
      </ol>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5">
          <span className="text-muted">Nazwa</span>
          <input value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={80} className={input} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-muted">Strefa czasowa raportu (opcjonalnie)</span>
          <input value={tz} onChange={(e) => setTz(e.target.value)} placeholder="America/New_York" className={`num ${input}`} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-muted">Token Flex Web Service</span>
          <input value={token} onChange={(e) => setToken(e.target.value)} required autoComplete="off" spellCheck={false} className={`num ${input}`} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-muted">Query ID</span>
          <input value={queryId} onChange={(e) => setQueryId(e.target.value)} required inputMode="numeric" className={`num ${input}`} />
        </label>
      </div>
      <div className="flex items-center gap-3">
        {add.isError && <span role="alert" className="text-neg">{add.error.message}</span>}
        <div className="flex-1" />
        <button type="button" onClick={onDone} className="h-9 rounded-md border border-line px-4">Anuluj</button>
        <button type="submit" disabled={add.isPending || !token.trim() || !queryId.trim()} className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
          {add.isPending ? "Zapisuję…" : "Połącz"}
        </button>
      </div>
    </form>
  );
}

function AddMt5({ onDone }: { onDone: () => void }) {
  const refresh = useRefresh();
  const [label, setLabel] = useState("MetaTrader 5");
  const [copied, setCopied] = useState(false);
  const add = useMutation<Connection & { token: string }, Error>({ mutationFn: () => api.addMt5(label), onSuccess: refresh });
  const origin = window.location.origin;

  if (add.data) {
    return (
      <div className="flex flex-col gap-3 rounded-md border border-line p-4">
        <p className="font-medium">Token połączenia — skopiuj teraz, później nie będzie widoczny</p>
        <div className="flex gap-2">
          <code className="num flex-1 overflow-x-auto rounded-md border border-line bg-surface px-2 py-2 text-[13px]">{add.data.token}</code>
          <button
            type="button"
            className="h-9 rounded-md border border-line px-3"
            onClick={() => {
              navigator.clipboard?.writeText(add.data.token).then(() => setCopied(true), () => undefined);
            }}
          >
            {copied ? "Skopiowano" : "Kopiuj"}
          </button>
        </div>
        <ol className="list-decimal space-y-1 pl-5 text-muted">
          <li>
            Pobierz <a className="text-fg underline" href="/integrations/TapeSync.mq5" download>TapeSync.mq5</a> i skopiuj do folderu MQL5/Experts (Plik → Otwórz folder danych).
          </li>
          <li>
            MT5 → Narzędzia → Opcje → Doradcy: zaznacz „Zezwalaj na WebRequest” i dodaj <span className="num text-fg">{origin}</span>.
          </li>
          <li>
            Przeciągnij EA na dowolny wykres, w parametrach wklej token i adres <span className="num text-fg">{origin}</span>.
          </li>
        </ol>
        <p className="text-xs text-muted">EA tylko czyta historię konta — nie otwiera ani nie zamyka pozycji. Nowe transakcje pojawią się w Tape w ciągu minuty.</p>
        <div className="flex justify-end">
          <button type="button" onClick={onDone} className="h-9 rounded-md bg-fg px-4 font-medium text-bg">Gotowe</button>
        </div>
      </div>
    );
  }
  return (
    <form
      className="flex flex-col gap-3 rounded-md border border-line p-4"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <label className="flex flex-col gap-1.5">
        <span className="text-muted">Nazwa konta (np. „FTMO 100k” albo numer konta)</span>
        <input value={label} onChange={(e) => setLabel(e.target.value)} required maxLength={80} className={input} />
      </label>
      <div className="flex items-center gap-3">
        {add.isError && <span role="alert" className="text-neg">{add.error.message}</span>}
        <div className="flex-1" />
        <button type="button" onClick={onDone} className="h-9 rounded-md border border-line px-4">Anuluj</button>
        <button type="submit" disabled={add.isPending} className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
          {add.isPending ? "Tworzę…" : "Utwórz token"}
        </button>
      </div>
    </form>
  );
}

export function ConnectionsPage() {
  const q = useQuery({ queryKey: ["connections"], queryFn: api.connections, refetchInterval: 60_000 });
  const [adding, setAdding] = useState<"ibkr" | "mt5" | null>(null);
  const list = q.data?.connections ?? [];

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-5 px-6 py-8">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Połączenia z brokerami</h1>
        <p className="mt-1 text-muted">
          Transakcje spływają same — bez eksportów. Duplikaty z ręcznym importem są pomijane.
        </p>
      </div>

      {q.isError && <p role="alert" className="text-neg">Nie udało się wczytać połączeń: {q.error.message}</p>}

      <section aria-label="Twoje połączenia" className="rounded-md border border-line">
        {q.isLoading ? (
          <p className="px-4 py-6 text-muted">Wczytuję…</p>
        ) : list.length === 0 ? (
          <p className="px-4 py-6 text-muted">Brak połączeń. Dodaj konto poniżej albo użyj importu pliku.</p>
        ) : (
          <ul>
            {list.map((c) => (
              <Row key={c.id} c={c} />
            ))}
          </ul>
        )}
      </section>

      {adding === null && (
        <div className="grid gap-3 sm:grid-cols-2">
          <button type="button" onClick={() => setAdding("mt5")} className="rounded-md border border-line p-4 text-left hover:border-muted">
            <div className="font-medium">MetaTrader 5</div>
            <div className="text-xs text-muted">EA Tape Sync — prop firmy, brokerzy CFD</div>
          </button>
          <button type="button" onClick={() => setAdding("ibkr")} className="rounded-md border border-line p-4 text-left hover:border-muted">
            <div className="font-medium">Interactive Brokers</div>
            <div className="text-xs text-muted">Flex Web Service — futures GC / SI / MGC</div>
          </button>
        </div>
      )}
      {adding === "ibkr" && <AddIbkr encryption={q.data?.encryption ?? false} onDone={() => setAdding(null)} />}
      {adding === "mt5" && <AddMt5 onDone={() => setAdding(null)} />}

      <p className="text-xs text-muted">
        XTB nie udostępnia API do historii rachunku — tam zostaje import pliku (Historia → Eksport).
      </p>
    </div>
  );
}
