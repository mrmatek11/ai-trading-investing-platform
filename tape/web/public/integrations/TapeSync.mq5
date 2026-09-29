//+------------------------------------------------------------------+
//| Tape Sync — wysyła zamknięte transakcje z MetaTrader 5 do Tape.   |
//|                                                                  |
//| Instalacja:                                                      |
//|  1. Tape → Połączenia → „Dodaj MetaTrader 5” → skopiuj token.     |
//|  2. MT5: Narzędzia → Opcje → Doradcy → „Zezwalaj na WebRequest”   |
//|     i dodaj adres Tape (np. https://app.tape.example).           |
//|  3. Skopiuj plik do MQL5/Experts, skompiluj, przeciągnij na        |
//|     dowolny wykres i wklej token w parametrach.                   |
//|                                                                  |
//| EA tylko czyta historię — nie składa, nie modyfikuje i nie         |
//| zamyka zleceń. Wysyła transakcje od ostatniej udanej wysyłki;     |
//| serwer ignoruje duplikaty, więc ponowna wysyłka jest bezpieczna.   |
//+------------------------------------------------------------------+
#property copyright "Tape"
#property version   "1.00"
#property strict

input string TapeUrl       = "https://app.tape.example";  // adres Tape (bez końcowego /)
input string TapeToken     = "";                           // token połączenia (tps_…)
input int    IntervalSec   = 60;                           // co ile sekund sprawdzać nowe transakcje
input int    HistoryDays   = 90;                           // ile dni historii wysłać przy pierwszym uruchomieniu
input int    BatchSize     = 500;                          // transakcji w jednym żądaniu

string g_key;   // zmienna globalna terminala z czasem ostatniej udanej wysyłki (per konto)

int OnInit()
{
   if(StringFind(TapeToken, "tps_") != 0)
   {
      Print("Tape Sync: wklej token połączenia z Tape (zaczyna się od tps_)");
      return(INIT_PARAMETERS_INCORRECT);
   }
   g_key = "TapeSync." + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   EventSetTimer(MathMax(IntervalSec, 15));
   Sync();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() { Sync(); }

// Po zamknięciu pozycji wysyłamy od razu, nie czekając na timer.
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &req, const MqlTradeResult &res)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD) Sync();
}

string TypeName(long t)
{
   if(t == DEAL_TYPE_BUY)  return "buy";
   if(t == DEAL_TYPE_SELL) return "sell";
   if(t == DEAL_TYPE_BALANCE) return "balance";
   if(t == DEAL_TYPE_CREDIT)  return "credit";
   return "other";
}

string EntryName(long e)
{
   if(e == DEAL_ENTRY_IN)    return "in";
   if(e == DEAL_ENTRY_OUT)   return "out";
   if(e == DEAL_ENTRY_INOUT) return "inout";
   if(e == DEAL_ENTRY_OUT_BY) return "out_by";
   return "";
}

string JsonEscape(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   return s;
}

string Num(double v, int digits) { return "\"" + DoubleToString(v, digits) + "\""; }

void Sync()
{
   datetime from = HistoryDays > 0 ? TimeCurrent() - HistoryDays * 86400 : 0;
   if(GlobalVariableCheck(g_key)) from = (datetime)GlobalVariableGet(g_key) - 3600;  // zakładka na opóźnienia
   datetime to = TimeCurrent() + 60;
   if(!HistorySelect(from, to)) { Print("Tape Sync: HistorySelect nie powiódł się"); return; }

   int total = HistoryDealsTotal();
   // Różnica czas serwera − UTC; serwer Tape przelicza czasy transakcji na UTC.
   long offset = (long)(TimeTradeServer() - TimeGMT());
   offset = (long)MathRound(offset / 900.0) * 900;

   string items = "";
   int n = 0;
   datetime last = from;
   for(int i = 0; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      string sym = HistoryDealGetString(t, DEAL_SYMBOL);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(digits <= 0) digits = 5;
      datetime when = (datetime)HistoryDealGetInteger(t, DEAL_TIME);
      string item = StringFormat(
         "{\"ticket\":%I64u,\"time\":%I64d,\"symbol\":\"%s\",\"type\":\"%s\",\"entry\":\"%s\","
         "\"volume\":%s,\"price\":%s,\"commission\":%s,\"fee\":%s,\"swap\":%s,\"profit\":%s,\"sl\":%s,"
         "\"contract_size\":%s}",
         t, (long)when, JsonEscape(sym),
         TypeName(HistoryDealGetInteger(t, DEAL_TYPE)), EntryName(HistoryDealGetInteger(t, DEAL_ENTRY)),
         Num(HistoryDealGetDouble(t, DEAL_VOLUME), 2), Num(HistoryDealGetDouble(t, DEAL_PRICE), digits),
         Num(HistoryDealGetDouble(t, DEAL_COMMISSION), 2), Num(HistoryDealGetDouble(t, DEAL_FEE), 2),
         Num(HistoryDealGetDouble(t, DEAL_SWAP), 2), Num(HistoryDealGetDouble(t, DEAL_PROFIT), 2),
         Num(HistoryDealGetDouble(t, DEAL_SL), digits),
         sym == "" ? "null" : Num(SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE), 2));
      items += (n > 0 ? "," : "") + item;
      n++;
      if(when > last) last = when;
      if(n >= BatchSize)
      {
         if(!Post(items, offset)) return;
         GlobalVariableSet(g_key, (double)last);
         items = ""; n = 0;
      }
   }
   if(n > 0 && !Post(items, offset)) return;
   GlobalVariableSet(g_key, (double)MathMax((long)last, (long)from + 3600));
}

bool Post(string items, long offset)
{
   string body = StringFormat("{\"gmt_offset\":%I64d,\"deals\":[%s]}", offset, items);
   char data[], result[];
   string headers = "Content-Type: application/json\r\nAuthorization: Bearer " + TapeToken + "\r\n";
   string reply_headers;
   int len = StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8) - 1;  // bez końcowego \0
   ArrayResize(data, len);
   ResetLastError();
   int code = WebRequest("POST", TapeUrl + "/api/ingest/mt5", headers, 15000, data, result, reply_headers);
   if(code == -1)
   {
      PrintFormat("Tape Sync: WebRequest błąd %d — dodaj %s do dozwolonych adresów (Opcje → Doradcy)",
                  GetLastError(), TapeUrl);
      return false;
   }
   if(code != 200)
   {
      PrintFormat("Tape Sync: serwer odpowiedział %d: %s", code, CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8));
      return false;
   }
   return true;
}
//+------------------------------------------------------------------+
