import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";

function Copy({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted">{label}</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => {
            navigator.clipboard?.writeText(text).then(() => setDone(true), () => setDone(false));
          }}
          className="text-xs text-muted hover:text-fg"
        >
          {done ? "Skopiowano" : "Kopiuj"}
        </button>
      </div>
      <pre className="num overflow-x-auto whitespace-pre rounded-md border border-line-soft bg-surface px-3 py-2 text-[12px]">{text}</pre>
    </div>
  );
}

// Claude Code / Claude Desktop jako „drugi analityk”: serwer MCP z danymi journala, tylko do odczytu.
export function ClaudeConnectCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["mcp-tokens"], queryFn: api.mcpTokens });
  const [fresh, setFresh] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["mcp-tokens"] });
  const create = useMutation<{ token: string }, Error>({
    mutationFn: () => api.createMcpToken("Claude Code"),
    onSuccess: (r) => {
      setFresh(r.token);
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: api.deleteMcpToken, onSuccess: refresh });
  const url = `${window.location.origin}/api/mcp`;
  const tok = fresh ?? "tpk_…";
  const cli = `claude mcp add --transport http goldtape ${url} \\\n  --header "Authorization: Bearer ${tok}"`;
  const desktop = JSON.stringify(
    { mcpServers: { goldtape: { command: "npx", args: ["-y", "mcp-remote", url, "--header", `Authorization: Bearer ${tok}`] } } },
    null,
    2,
  );

  return (
    <section aria-label="Claude Code" className="flex flex-col gap-4 rounded-md border border-line p-5">
      <h2 className="text-[13px] font-medium">Claude Code i Claude Desktop</h2>
      <p className="text-xs leading-relaxed text-muted">
        Podłącz Claude do swojego journala przez MCP — na swojej subskrypcji Claude, bez klucza API. Claude widzi statystyki, transakcje, portfel,
        limity prop, kalendarz i brief (tylko odczyt) i może je analizować, np. „które setupy tracą w dni CPI?”. Token pokazujemy raz; w bazie
        trzymamy tylko jego skrót.
      </p>
      {fresh ? (
        <p className="text-xs text-warn">Skopiuj polecenie teraz — tokenu nie da się wyświetlić ponownie.</p>
      ) : (
        <button type="button" onClick={() => create.mutate()} disabled={create.isPending} className="h-9 w-fit rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
          Utwórz token
        </button>
      )}
      {create.isError && <p role="alert" className="text-xs text-neg">{create.error.message}</p>}
      <Copy label="Claude Code (terminal)" text={cli} />
      <Copy label="Claude Desktop (claude_desktop_config.json)" text={desktop} />
      {(q.data ?? []).length > 0 && (
        <ul className="flex flex-col divide-y divide-line-soft rounded-md border border-line-soft">
          {q.data!.map((t) => (
            <li key={t.id} className="flex items-center gap-3 px-3 py-2 text-xs">
              <span className="num">{t.hint}</span>
              <span className="text-muted">{t.name}</span>
              <div className="flex-1" />
              <span className="text-muted">
                {t.last_used_at ? `użyty ${new Date(t.last_used_at).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" })}` : "nieużyty"}
              </span>
              <button type="button" onClick={() => remove.mutate(t.id)} className="text-muted hover:text-neg">
                Unieważnij
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
