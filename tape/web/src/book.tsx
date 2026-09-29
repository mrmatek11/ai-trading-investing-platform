import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

// Wybrany rachunek handlowy (np. jedno konto prop). null = wszystkie rachunki razem.
type BookState = { book: string | null; setBook: (b: string | null) => void };
const Ctx = createContext<BookState>({ book: null, setBook: () => undefined });

export function BookProvider({ children }: { children: ReactNode }) {
  const [book, setBook] = useState<string | null>(() => {
    try {
      const v = localStorage.getItem("tape-book");
      return v === null ? null : v;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    try {
      if (book === null) localStorage.removeItem("tape-book");
      else localStorage.setItem("tape-book", book);
    } catch {
      /* brak storage — wybór tylko na tę sesję */
    }
  }, [book]);
  return <Ctx.Provider value={{ book, setBook }}>{children}</Ctx.Provider>;
}

export function useBook() {
  return useContext(Ctx);
}

export function BookSelect() {
  const { book, setBook } = useBook();
  const q = useQuery({ queryKey: ["books"], queryFn: api.books });
  const list = (q.data ?? []).filter((b) => b.has_trades);
  // wybrany rachunek zniknął (np. usunięte połączenie) → wracamy do „wszystkich”
  useEffect(() => {
    if (q.data && book !== null && !q.data.some((b) => b.id === book)) setBook(null);
  }, [q.data, book, setBook]);
  if (list.length < 2 && book === null) return null;
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted">
      <span className="sr-only sm:not-sr-only">Konto</span>
      <select
        value={book ?? "__all"}
        onChange={(e) => setBook(e.target.value === "__all" ? null : e.target.value)}
        className="h-8 max-w-44 rounded-md border border-line bg-surface px-2 text-[13px] text-fg"
      >
        <option value="__all">Wszystkie konta</option>
        {list.map((b) => (
          <option key={b.id || "_import"} value={b.id}>
            {b.label}
          </option>
        ))}
      </select>
    </label>
  );
}
